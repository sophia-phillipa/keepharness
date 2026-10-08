"""Codex app-server turns; provider adapters supply endpoint and isolation policy."""

import hashlib
import json
import os
import re
import time
import tomllib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from adapters.shared.provider_setup import instructions
from adapters.shared.workspace import readable_roots
from agent_service.reader_mcp import SERVER_NAME as READER
from agent_service.reader_mcp import server_spec as reader_spec
from agent_service.tool_metadata import event_metadata, item_markers, item_target
from agent_service.tools import ToolError
from control.integrations import configurations, inventory

from .rpc import (
    RPCError,
    connection,
    execution_failed,
    provider_message,
    sync_title,
    usage_delta,
)

# codex-cli 0.157.1 answers thread/resume for a thread whose rollout is gone (a restored or
# moved state, a cleaned sessions folder) with -32600 "no rollout found for thread id <id>".
MISSING_THREAD = re.compile(r"no rollout found|thread not found", re.I)


@dataclass
class RuntimeOptions:
    command: list[str]
    environment: dict | None = None
    model_provider: str | None = None
    isolated: bool = False
    pass_fds: tuple[int, ...] = ()  # descriptors the child inherits (see ``WrappedCommand``)
    session_metadata: dict = field(default_factory=dict)
    thread_instructions: dict = field(default_factory=dict)
    developer_instructions: str = ""
    check_configuration: Callable[[object, Path], Awaitable[None]] | None = None


def build_command(binary, permissions, hosted_search=True, *, host_config=True):
    command = [binary, "app-server", "--listen", "stdio://"]
    if not host_config:
        return command + [
            "-c", "features.hooks=" + str(bool(permissions.get("hooks"))).lower(),
            "-c", "features.apps=false",
            "-c", "features.shell_tool=" + str(bool(permissions.get("shell"))).lower(),
            "-c", "features.unified_exec=" + str(bool(permissions.get("shell"))).lower(),
            "-c", 'web_search="' + ("live" if permissions.get("internet") and hosted_search else "disabled") + '"',
        ]
    # Never synthesize a disabled server without a transport. Only existing host
    # entries are disabled, using the home the CLI actually receives.
    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    try:
        configured = tomllib.loads((home / "config.toml").read_text()).get("mcp_servers", {})
    except (OSError, ValueError):
        configured = {}
    for name in configured:
        if re.fullmatch(r"harness_effects[\w-]*", name, re.ASCII):
            command += ["-c", "mcp_servers." + name + ".enabled=false"]
    return command


def thread_parameters(config, project, model, workspace, runtime, unrestricted):
    """Translate harness permissions and integrations to app-server settings."""
    cwd, permissions = workspace.cwd, workspace.permissions
    # Ask: the read-only sandbox makes every write escalate to an approval card.
    ask = project.get("access_mode", "ask") == "ask" and not runtime.isolated
    # Automatic: the workspace sandbox on the project; anything beyond asks (decision D11).
    automatic = project.get("access_mode") == "auto" and not runtime.isolated
    local_provider = runtime.model_provider
    params = {
        "model": model,
        "cwd": str(cwd),
        "sandbox": (
            "danger-full-access"
            if unrestricted
            else "workspace-write"
            if permissions.get("write") and not ask
            else "read-only"
        ),
        "approvalPolicy": (
            "never"
            if project.get("access_mode") in ("full", "read_only") and not runtime.isolated
            else "on-request"
        ),
        "approvalsReviewer": "user",
        "developerInstructions": "Use the native CLI tools and only the configured integrations. Follow the selected project instructions. Ask approval for actions that exceed the configured permissions. Do not claim a tool succeeded without evidence. "
        + instructions(config, provider="deepseek" if local_provider == "tail_api" else "codex"),
    }
    params["developerInstructions"] += (
        " Effective permissions for this turn: "
        + json.dumps(permissions)
        + ". Use already authorized network and folder access without requesting escalation preemptively."
    )
    params.update(runtime.thread_instructions)
    params["developerInstructions"] += runtime.developer_instructions
    params["config"] = {"mcp_servers": {}}
    if runtime.isolated:
        params["config"]["plugins"] = {}
    if local_provider and config.get("personal_setup") is True and not runtime.isolated:
        selected = [] if project.get("access_mode") == "read_only" else config.get("integrations", [])
        params["config"]["mcp_servers"] = host_servers(selected, ask or automatic)
        plugins = config["plugin_inventory"] if "plugin_inventory" in config else [
            item["id"] for item in inventory()["codex"] if item["kind"] == "plugin"
        ]
        params["config"]["plugins"] = {
            plugin.split(":", 1)[1]: {"enabled": plugin in selected} for plugin in plugins
        }
    if not runtime.isolated:
        add_reader(params, workspace, restrict=bool(local_provider))
    if config.get("_effect_capability"):
        from agent_service.effect_transport import server_spec

        params["config"]["mcp_servers"][config["_effect_capability"]["server_name"]] = {
            **server_spec(config["_effect_capability"]),
            "enabled": True,
        }
    if local_provider:
        params["modelProvider"] = local_provider
    return params


def host_servers(selected, ask):
    """The host's connectors, enabled only when selected; ``ask`` shows a card before every call."""
    return {
        name: {
            **spec,
            "enabled": not name.startswith("harness_effects") and "mcp:" + name in selected,
            **({"default_tools_approval_mode": "prompt"} if ask else {}),
        }
        for name, spec in configurations()["codex"].items()
    }


def add_reader(params, workspace, *, restrict=False):
    """Without the native shell, read through the harness reader over the authorized roots.

    The reader enforces the roots itself: Codex 0.157.1 has no sandbox read allow-list.
    """
    permissions = workspace.permissions
    if not permissions.get("read") or permissions.get("shell"):
        return
    if not restrict:
        home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
        for path in (home / "config.toml", Path(workspace.cwd) / ".codex/config.toml"):
            try:
                configured = tomllib.loads(path.read_text()).get("mcp_servers", {})
            except (OSError, ValueError):
                continue
            if READER in configured:
                return
    roots = readable_roots(workspace)
    if not roots:
        return
    params["config"]["mcp_servers"][READER] = reader_spec(roots)
    if not restrict:
        return
    params["developerInstructions"] += (
        " Read files only with the " + READER + " tools (read_file, list_directory,"
        " search_files); they accept paths inside the authorized folders: " + ", ".join(roots) + "."
    )


async def respond_to_interaction(rpc, item, approve, project, permissions, unrestricted, isolated):
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
    # A denial carries no typed answers to the provider.
    answers = reply.get("answers") if decision else None
    if "requestUserInput" in kind:
        result = {"answers": answers or {}}
    elif "elicitation" in kind:
        result = {
            "action": "accept" if decision else "decline",
            "content": answers or None,
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
    rpc.process.stdin.write((json.dumps({"id": item["id"], "result": result}) + "\n").encode())
    await rpc.process.stdin.drain()
    return


async def resource_inputs(rpc, project, cwd):
    """Reload the engine catalog and bind explicitly selected skills by path."""
    selected = [item for item in project.get("_resources", []) if item["kind"] == "skill"]
    if not selected:
        return []
    available = await rpc.call("skills/list", {"cwds": [str(cwd)], "forceReload": True})
    skills = [skill for group in available.get("data", []) for skill in group.get("skills", [])]
    result = []
    for item in selected:
        path = Path(item["source"])
        match = next(
            (
                skill
                for skill in skills
                if skill.get("path")
                and Path(skill["path"]).resolve() == path.resolve()
                and skill.get("name") == item["name"]
            ),
            None,
        )
        if not match or match.get("enabled") is not True:
            raise ToolError("resource_unavailable_in_engine")
        try:
            with path.open("rb") as stream:
                from agent_service.resources import MAX_BODY_BYTES

                content = stream.read(MAX_BODY_BYTES + 1)
        except OSError:
            raise ToolError("resource_unavailable_in_engine") from None
        if len(content) > MAX_BODY_BYTES or hashlib.sha256(content).hexdigest() != item["revision"]:
            raise ToolError("resource_changed")
        result.append({"type": "skill", "name": item["name"], "path": str(path)})
    return result


async def open_thread(rpc, method, params, marker, provider):
    """Start or resume the thread; a resume whose rollout is gone is ``native_session_missing``."""
    try:
        return await rpc.call(method, params)
    except RPCError as exc:
        if method == "thread/resume" and MISSING_THREAD.search(str(exc.error.get("message", ""))):
            # The caller replays the harness history on a fresh thread, as for Claude and Gemini.
            marker.replace(marker.with_name(marker.name + ".before-session-missing"))
            raise ToolError("native_session_missing") from exc
        raise execution_failed(provider, exc.error) from exc


def session_marker(marker, provider):
    """A DeepSeek thread belongs to its provider and engine, including on handoff."""
    if not marker.exists():
        return {}
    try:
        saved = json.loads(marker.read_text())
    except (OSError, ValueError) as exc:
        if provider == "deepseek":
            raise ToolError("deepseek_session_identity_ambiguous") from exc
        raise
    if not isinstance(saved, dict):
        raise ToolError(provider + "_session_identity_ambiguous")
    expected = {"provider": provider, "engine": "codex"}
    if any(key in saved and saved[key] != value for key, value in expected.items()):
        raise ToolError(provider + "_session_identity_mismatch")
    if provider == "deepseek":
        if "adapter" in saved and saved["adapter"] != "deepseek":
            raise ToolError("deepseek_session_identity_mismatch")
        identified = all(saved.get(key) == value for key, value in expected.items())
        if not isinstance(saved.get("id"), str) or not saved["id"].strip() or (
            not identified and saved.get("adapter") != "deepseek"
        ):
            raise ToolError("deepseek_session_identity_ambiguous")
    elif saved.get("adapter") == "deepseek":
        raise ToolError(provider + "_session_identity_mismatch")
    return saved


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
    marker = home / "native-thread.json"
    saved = session_marker(marker, provider)
    permissions, images = workspace.permissions, workspace.images
    access_mode = project.get("access_mode", "ask")
    ask = access_mode == "ask" and not runtime.isolated
    unrestricted = (
        not runtime.isolated
        and config.get("unrestricted") is True
        and access_mode == "full"
        and bool(permissions.get("shell"))
    )
    command, environment, _local_provider = (
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
    answer_item = None
    file_changes = {}
    markers = {}  # skill/agent names from each tool start, kept for its end
    async with connection(
        command,
        env=environment,
        event=event,
        config=config,
        provider=provider,
        pass_fds=runtime.pass_fds,
    ) as rpc:
        if runtime.check_configuration:
            await runtime.check_configuration(rpc, cwd)
        selected_inputs = await resource_inputs(rpc, project, cwd)
        turn_started = False
        resumable = bool(saved) and (
            not runtime.isolated
            or all(saved.get(key) == value for key, value in runtime.session_metadata.items())
        )
        previous_usage = saved.get("usage_total") if resumable else {}
        isolation = runtime.session_metadata
        params = thread_parameters(config, project, model, workspace, runtime, unrestricted)
        if resumable:
            # Thread metadata only: the stored turns (attached images included) can outgrow
            # any line limit, and the harness never reads them back.
            params.update(threadId=saved["id"], excludeTurns=True)
            thread = await open_thread(rpc, "thread/resume", params, marker, provider)
            event("session_resumed", {"thread_id": params["threadId"]})
        else:
            params["ephemeral"] = not bool(session_dir)
            thread = await open_thread(rpc, "thread/start", params, marker, provider)
        thread_id = thread["thread"]["id"]
        marker.write_text(
            json.dumps(
                {
                    "id": thread_id,
                    **isolation,
                    **({"usage_total": previous_usage} if previous_usage is not None else {}),
                }
            )
        )
        await sync_title(rpc, thread_id, project.get("_conversation_title"), event)
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
                "approvalPolicy": params["approvalPolicy"],
                "approvalsReviewer": params["approvalsReviewer"],
                "sandboxPolicy": (
                    {"type": "dangerFullAccess"}
                    if unrestricted
                    else {
                        "type": "workspaceWrite"
                        if permissions.get("write") and not ask
                        else "readOnly",
                        "networkAccess": bool(permissions.get("internet")),
                        **(
                            {
                                "writableRoots": list(dict.fromkeys(writable)),
                                "excludeSlashTmp": True,
                                "excludeTmpdirEnvVar": True,
                            }
                            if permissions.get("write") and not ask
                            else {}
                        ),
                    }
                ),
                "input": [{"type": "text", "text": prompt}]
                + selected_inputs
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
                raise execution_failed(provider, item["error"])
            if "id" in item and "method" in item:
                if params.get("itemId") in file_changes:
                    # The approval request has no diff; the card shows the started patch.
                    item = {**item, "params": {**params, "changes": file_changes[params["itemId"]]}}
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
                item_id = params.get("itemId")
                # Distinct agent messages (progress commentary, final answer) must not run together.
                if item_id and answer_item and item_id != answer_item and answer and not answer.endswith("\n"):
                    answer += "\n\n"
                    event("answer_delta", {"text": "\n\n"})
                answer_item = item_id or answer_item
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
                    ("reasoning_delta" if kind.endswith("/textDelta") else "reasoning_summary"),
                    {"text": text},
                )
            elif kind in ("item/started", "item/completed"):
                content = params.get("item", {})
                typ = content.get("type", "")
                if typ == "fileChange" and kind.endswith("started"):
                    file_changes[content.get("id")] = content.get("changes", [])
                if typ in (
                    "mcpToolCall",
                    "commandExecution",
                    "fileChange",
                    "webSearch",
                ):
                    metadata = event_metadata(
                        content,
                        command=(content.get("command") if typ == "commandExecution" else None),
                    )
                    started = kind.endswith("started")
                    tool_id = content.get("id")
                    if started:
                        markers[tool_id] = item_markers(content)
                        found = item_target(content, cwd)
                        extra = {**({"target": found} if found else {}), **markers[tool_id]}
                    else:
                        extra = markers.pop(tool_id, {})
                    event(
                        "tool_start" if started else "tool_end",
                        {
                            "tool": content.get("tool") or typ,
                            "status": content.get("status"),
                            "result": content.get("result"),
                            **metadata,
                            **extra,
                        },
                    )
                elif typ == "contextCompaction":
                    event(
                        ("context_compacting" if kind.endswith("started") else "context_compacted"),
                        {},
                    )
                elif typ == "reasoning" and kind.endswith("started"):
                    event("thinking", {})
                elif typ == "agentMessage" and kind.endswith("completed") and not seen_answer:
                    text = content.get("text", "")
                    answer += text
                    event("answer_delta", {"text": text})
            elif kind == "turn/started":
                turn_started = True
                event("session_turn_started", {"thread_id": thread_id})
            elif kind == "thread/tokenUsage/updated":
                if params.get("threadId", thread_id) != thread_id:
                    continue
                token_usage = params.get("tokenUsage", {})
                total = token_usage.get("total", {})
                for key, value in (
                    usage_delta(previous_usage, total, token_usage.get("last", {}))
                    if turn_started
                    else {}
                ).items():
                    usage[key] = usage.get(key, 0) + value
                previous_usage = total
                marker.write_text(json.dumps({"id": thread_id, **isolation, "usage_total": total}))
                event(
                    "context_usage",
                    {
                        **token_usage,
                        "metrics": {
                            "usage_scope": "turn",
                            "output_tokens": usage.get("outputTokens"),
                            "inference_seconds": time.monotonic() - started,
                        },
                    },
                )
            elif kind == "turn/plan/updated":
                event("plan_updated", params)
            elif kind == "thread/compacted":
                event("context_compacted", {})
            elif kind == "turn/completed":
                turn = params.get("turn", {})
                if turn.get("status") != "completed":
                    raise execution_failed(provider, turn.get("error"))
                break
            elif kind == "error" and params.get("willRetry") is True:
                # Codex retries on its own (a dropped stream, a busy server); its next message
                # arrives within the idle watchdog, so the run keeps waiting for it.
                event("provider_retrying", {"message": provider_message(params.get("error"))})
            elif kind == "error":
                event("error", params)
                raise execution_failed(provider, params.get("error"))
            if len(answer) + len(thinking) > 500000:
                raise ToolError(provider + "_output_limit")

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
