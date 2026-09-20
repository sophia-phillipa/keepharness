"""Codex app-server turns; provider adapters supply endpoint and isolation policy."""

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from control.integrations import configurations, inventory
from agent_service.tools import ToolError
from agent_service.tool_metadata import event_metadata
from .rpc import connection, usage_delta


@dataclass
class RuntimeOptions:
    command: list[str]
    environment: dict | None = None
    model_provider: str | None = None
    isolated: bool = False
    session_metadata: dict = field(default_factory=dict)
    thread_instructions: dict = field(default_factory=dict)
    developer_instructions: str = ""


def build_command(binary, permissions, hosted_search=True):
    return [
        binary,
        "app-server",
        "--listen",
        "stdio://",
        "-c",
        "features.hooks=" + str(bool(permissions.get("hooks"))).lower(),
        "-c",
        "features.apps=false",
        "-c",
        "features.shell_tool=" + str(bool(permissions.get("shell"))).lower(),
        "-c",
        "features.unified_exec=" + str(bool(permissions.get("shell"))).lower(),
        "-c",
        'web_search="'
        + ("live" if permissions.get("internet") and hosted_search else "disabled")
        + '"',
    ]


def thread_parameters(config, project, model, workspace, runtime, unrestricted):
    """Translate harness permissions and integrations to app-server settings."""
    cwd, permissions = workspace.cwd, workspace.permissions
    selected = config.get("integrations", [])
    local_provider = runtime.model_provider
    params = {
        "model": model,
        "cwd": str(cwd),
        "sandbox": (
            "danger-full-access"
            if unrestricted
            else "workspace-write" if permissions.get("write") else "read-only"
        ),
        "approvalPolicy": (
            "never"
            if project.get("access_mode") in ("auto", "full", "read_only")
            and not runtime.isolated
            else "on-request"
        ),
        "developerInstructions": "Use the native CLI tools and only the configured integrations. Follow the selected project instructions. Ask approval for actions that exceed the configured permissions. Do not claim a tool succeeded without evidence.",
    }
    params["developerInstructions"] += (
        " Effective permissions for this turn: "
        + json.dumps(permissions)
        + ". Use already authorized network and folder access without requesting escalation preemptively."
    )
    params.update(runtime.thread_instructions)
    params["developerInstructions"] += runtime.developer_instructions
    params["config"] = (
        {"mcp_servers": {}, "plugins": {}}
        if runtime.isolated
        else {
            "mcp_servers": {
                name: {**spec, "enabled": "mcp:" + name in selected}
                for name, spec in configurations()["codex"].items()
            },
            "plugins": {
                item["id"].split(":", 1)[1]: {"enabled": item["id"] in selected}
                for item in inventory()["codex"]
                if item["kind"] == "plugin"
            },
        }
    )
    if local_provider:
        params["modelProvider"] = local_provider
    return params


async def respond_to_interaction(
    rpc, item, approve, project, permissions, unrestricted, isolated
):
    """Map an approval reply to the exact response expected by Codex."""
    kind, params = item["method"], item.get("params", {})
    escalation = "requestApproval" in kind or kind in (
        "execCommandApproval",
        "applyPatchApproval",
    )
    reply = (
        {"approved": False}
        if escalation
        and (
            (
                not unrestricted
                and project.get("access_mode") == "full"
                and "permissions/requestApproval" in kind
            )
            or project.get("access_mode") == "read_only"
            or (
                isolated
                and not permissions.get("internet")
                and project.get("access_mode") != "full"
            )
        )
        else await approve(kind, params)
    )
    decision = reply.get("approved", False)
    if "requestUserInput" in kind:
        result = {"answers": reply.get("answers", {})}
    elif "elicitation" in kind:
        result = {
            "action": "accept" if decision else "decline",
            "content": reply.get("answers") or None,
        }
    elif "permissions/requestApproval" in kind:
        result = {
            "permissions": (params.get("permissions", {}) if decision else {}),
            "scope": "turn",
        }
    elif "requestApproval" in kind or kind in (
        "execCommandApproval",
        "applyPatchApproval",
    ):
        result = {"decision": "accept" if decision else "decline"}
    else:
        rpc.process.stdin.write(
            (
                json.dumps(
                    {
                        "id": item["id"],
                        "error": {
                            "code": -32601,
                            "message": "Unsupported interactive request",
                        },
                    }
                )
                + "\n"
            ).encode()
        )
        await rpc.process.stdin.drain()
        return
    rpc.process.stdin.write(
        (json.dumps({"id": item["id"], "result": result}) + "\n").encode()
    )
    await rpc.process.stdin.drain()
    return


async def run_turn(
    config,
    event,
    project,
    model,
    effort,
    session_dir,
    approve,
    workspace,
    runtime,
    provider,
):
    home, cwd, prompt = workspace.home, workspace.cwd, workspace.prompt
    permissions, images = workspace.permissions, workspace.images
    unrestricted = (
        not runtime.isolated
        and config.get("unrestricted") is True
        and project.get("access_mode") != "read_only"
        and bool(permissions.get("shell"))
    )
    command, environment, local_provider = (
        runtime.command,
        runtime.environment,
        runtime.model_provider,
    )
    started = time.monotonic()
    first = None
    answer = ""
    thinking = ""
    usage = {}
    token_usage = {}
    seen_answer = False
    async with connection(command, env=environment) as rpc:
        marker = home / "native-thread.json"
        turn_started = False
        saved = json.loads(marker.read_text()) if marker.exists() else {}
        resumable = bool(saved) and (
            not runtime.isolated
            or all(
                saved.get(key) == value
                for key, value in runtime.session_metadata.items()
            )
        )
        previous_usage = saved.get("usage_total") if resumable else {}
        isolation = runtime.session_metadata
        params = thread_parameters(
            config, project, model, workspace, runtime, unrestricted
        )
        if resumable:
            params["threadId"] = saved["id"]
            thread = await rpc.call("thread/resume", params)
            event("session_resumed", {"thread_id": params["threadId"]})
        else:
            params["ephemeral"] = not bool(session_dir)
            thread = await rpc.call("thread/start", params)
        thread_id = thread["thread"]["id"]
        marker.write_text(
            json.dumps(
                {
                    "id": thread_id,
                    **isolation,
                    **(
                        {"usage_total": previous_usage}
                        if previous_usage is not None
                        else {}
                    ),
                }
            )
        )
        writable = [
            str(cwd),
            *[
                str(Path(root).resolve())
                for root in project.get("additional_roots", [])
                if permissions.get("read")
            ],
        ]
        await rpc.send(
            "turn/start",
            {
                "threadId": thread_id,
                "cwd": str(cwd),
                "model": model,
                "effort": None if effort == "configured" else effort,
                "sandboxPolicy": (
                    {"type": "dangerFullAccess"}
                    if unrestricted
                    else {
                        "type": (
                            "workspaceWrite" if permissions.get("write") else "readOnly"
                        ),
                        "networkAccess": bool(permissions.get("internet")),
                        **(
                            {
                                "writableRoots": list(dict.fromkeys(writable)),
                                "excludeSlashTmp": True,
                                "excludeTmpdirEnvVar": True,
                            }
                            if permissions.get("write")
                            else {}
                        ),
                    }
                ),
                "input": [{"type": "text", "text": prompt}]
                + [
                    {
                        "type": "image",
                        "url": "data:" + item["media_type"] + ";base64," + item["data"],
                    }
                    for item in images
                ],
            },
        )
        event("planning", {"backend": provider, "model": model, "effort": effort})
        while True:
            item = await rpc.receive()
            kind = item.get("method", "")
            params = item.get("params", {})
            if "error" in item:
                raise ToolError("codex_rpc_error")
            if "id" in item and "method" in item:
                await respond_to_interaction(
                    rpc,
                    item,
                    approve,
                    project,
                    permissions,
                    unrestricted,
                    runtime.isolated,
                )
                continue
            if kind == "item/agentMessage/delta":
                text = params.get("delta", "")
                answer += text
                seen_answer = True
                first = first if first is not None else time.monotonic() - started
                event("answer_delta", {"text": text})
            elif kind in (
                "item/reasoning/textDelta",
                "item/reasoning/summaryTextDelta",
            ):
                text = params.get("delta", "")
                thinking += text
                event(
                    (
                        "reasoning_delta"
                        if kind.endswith("/textDelta")
                        else "reasoning_summary"
                    ),
                    {"text": text},
                )
            elif kind in ("item/started", "item/completed"):
                content = params.get("item", {})
                typ = content.get("type", "")
                if typ in (
                    "mcpToolCall",
                    "commandExecution",
                    "fileChange",
                    "webSearch",
                ):
                    metadata = event_metadata(
                        content,
                        command=(
                            content.get("command")
                            if typ == "commandExecution"
                            else None
                        ),
                    )
                    event(
                        "tool_start" if kind.endswith("started") else "tool_end",
                        {
                            "tool": content.get("tool") or typ,
                            "status": content.get("status"),
                            "result": content.get("result"),
                            **metadata,
                        },
                    )
                elif typ == "contextCompaction":
                    event(
                        (
                            "context_compacting"
                            if kind.endswith("started")
                            else "context_compacted"
                        ),
                        {},
                    )
                elif typ == "reasoning" and kind.endswith("started"):
                    event("thinking", {})
                elif (
                    typ == "agentMessage"
                    and kind.endswith("completed")
                    and not seen_answer
                ):
                    text = content.get("text", "")
                    answer += text
                    event("answer_delta", {"text": text})
            elif kind == "turn/started":
                turn_started = True
            elif kind == "thread/tokenUsage/updated":
                token_usage = params.get("tokenUsage", {})
                total = token_usage.get("total", {})
                for key, value in (
                    usage_delta(previous_usage, total, token_usage.get("last", {}))
                    if turn_started
                    else {}
                ).items():
                    usage[key] = usage.get(key, 0) + value
                previous_usage = total
                marker.write_text(
                    json.dumps({"id": thread_id, **isolation, "usage_total": total})
                )
                event("context_usage", token_usage)
            elif kind == "turn/plan/updated":
                event("plan_updated", params)
            elif kind == "thread/compacted":
                event("context_compacted", {})
            elif kind == "turn/completed":
                if params.get("turn", {}).get("status") != "completed":
                    raise ToolError("codex_execution_failed")
                break
            elif kind == "error":
                event("error", params)
                raise ToolError(
                    "codex_execution_failed: "
                    + str(params.get("error", {}).get("message", "provider error"))[
                        :500
                    ]
                )
            if len(answer) + len(thinking) > 500000:
                raise ToolError("codex_output_limit")

    return {
        "answer": answer,
        "thread_id": thread_id,
        "context_usage": token_usage,
        "backend": provider,
        "model": model,
        "context_strategy": "native_session",
        "project_mode": "native_cli",
        "finish_reason": "completed",
        "incomplete": False,
        "metrics": {
            "usage_scope": "turn",
            "input_tokens": usage.get("inputTokens"),
            "output_tokens": usage.get("outputTokens"),
            "ttft_seconds": first,
            "inference_seconds": time.monotonic() - started,
        },
    }
