"""Codex scoped MCP turns with persistent app-server sessions."""

import json
import time
from Adapters.shared.scoped import prepare_scoped, collect_changes
from agent_service.tools import ToolError
from agent_service.tool_metadata import event_metadata
from .rpc import connection, usage_delta


async def run(
    config,
    prompt,
    event,
    project=None,
    model="gpt-6-astra",
    effort="low",
    staged=None,
    session_dir=None,
):
    with prepare_scoped(
        config, project, staged, session_dir, "codex", "auth.json"
    ) as workspace:
        command, home = workspace.command, workspace.home
        command += [
            "--",
            "/codex-cli",
            "app-server",
            "--listen",
            "stdio://",
            "-c",
            "features.shell_tool=false",
            "-c",
            "features.unified_exec=false",
            "-c",
            "features.multi_agent=false",
            "-c",
            "features.apps=false",
            "-c",
            'web_search="disabled"',
        ]
        started = time.monotonic()
        first = None
        answer = ""
        thinking = ""
        usage = {}
        token_usage = {}
        seen_answer = False
        async with connection(command) as rpc:
            marker = home / "remote-thread.json"
            turn_started = False
            previous_usage = (
                json.loads(marker.read_text()).get("usage_total")
                if marker.exists()
                else {}
            )
            params = {
                "model": model,
                "cwd": "/work",
                "sandbox": "read-only",
                "approvalPolicy": "never",
                "developerInstructions": "Use only selected_project MCP tools within authorized roots. File proposals are applied automatically after validation when this project enables apply_changes; do not refuse authorized local edits or local deployment. Do not publish to Git remotes, access credentials, or external tools.",
            }
            if marker.exists():
                params["threadId"] = json.loads(marker.read_text())["id"]
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
                        **(
                            {"usage_total": previous_usage}
                            if previous_usage is not None
                            else {}
                        ),
                    }
                )
            )
            await rpc.send(
                "turn/start",
                {
                    "threadId": thread_id,
                    "model": model,
                    "effort": effort,
                    "input": [{"type": "text", "text": prompt}],
                },
            )
            event("planning", {"backend": "codex", "model": model, "effort": effort})
            while True:
                item = await rpc.receive()
                kind = item.get("method", "")
                params = item.get("params", {})
                if "error" in item:
                    raise ToolError("codex_rpc_error")
                if "id" in item and "method" in item:
                    # No user approval or dynamic permission can be silently granted by the remote service.
                    rpc.process.stdin.write(
                        (
                            json.dumps(
                                {
                                    "id": item["id"],
                                    "error": {
                                        "code": -32601,
                                        "message": "Interactive approvals disabled",
                                    },
                                }
                            )
                            + "\n"
                        ).encode()
                    )
                    await rpc.process.stdin.drain()
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
                    if typ == "mcpToolCall":
                        metadata = event_metadata(content)
                        event(
                            "tool_start" if kind.endswith("started") else "tool_end",
                            {
                                "tool": content.get("tool"),
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
                        json.dumps({"id": thread_id, "usage_total": total})
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
                    raise ToolError("codex_execution_failed")
                if len(answer) + len(thinking) > 500000:
                    raise ToolError("codex_output_limit")
        return {
            "answer": answer,
            "context_usage": token_usage,
            "thread_id": thread_id,
            **collect_changes(workspace),
            "backend": "codex",
            "model": model,
            "effort": effort,
            "cloud_inference": True,
            "finish_reason": "completed",
            "incomplete": False,
            "metrics": {
                "usage_scope": "turn",
                "input_tokens": usage.get("inputTokens"),
                "output_tokens": usage.get("outputTokens"),
                "cached_tokens": usage.get("cachedInputTokens"),
                "thinking_tokens": usage.get("reasoningOutputTokens"),
                "answer_tokens": None,
                "ttft_seconds": first,
                "inference_seconds": time.monotonic() - started,
                "generated_tokens_per_second": None,
                "billing": "ChatGPT account quota; monetary amount unavailable",
            },
        }
