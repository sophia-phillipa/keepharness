"""Codex scoped MCP turns with persistent app-server sessions."""

import json
import time

from adapters.shared.provider_setup import LANGUAGE_RULE
from adapters.shared.scoped import (
    collect_changes,
    prepare_scoped,
    scoped_home_read,
    scoped_home_write,
)
from agent_service.tool_metadata import event_metadata, item_markers, item_target
from agent_service.tools import ToolError

from .rpc import connection, execution_failed, provider_message, sync_title, usage_delta


async def run(
    config,
    prompt,
    event,
    project=None,
    model=None,
    effort="low",
    staged=None,
    session_dir=None,
):
    with prepare_scoped(config, project, staged, session_dir, "codex", "auth.json") as workspace:
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
        markers = {}  # skill/agent names from each tool start, kept for its end
        answer_item = None
        async with connection(command, event=event, config=config) as rpc:
            marker = "remote-thread.json"
            saved = scoped_home_read(home, marker)
            turn_started = False
            previous_usage = json.loads(saved).get("usage_total") if saved else {}
            params = {
                "model": model,
                "cwd": "/work",
                "sandbox": "read-only",
                "approvalPolicy": "never",
                "developerInstructions": "Use only selected_project MCP tools within authorized roots. File proposals are applied automatically after validation when this project enables apply_changes; do not refuse authorized local edits or local deployment. Do not publish to Git remotes, access credentials, or external tools. "
                + LANGUAGE_RULE,
            }
            if config.get("_effect_capability"):
                params["developerInstructions"] = params["developerInstructions"].replace(
                    "Use only selected_project MCP tools",
                    "Use only selected_project MCP tools and harness_effects.prepare",
                )
                params["developerInstructions"] += (
                    " Prepare only stages a Jira create-issue request for a human gate; the harness alone publishes after approval."
                )
            if saved:
                # Thread metadata only: a long stored history can outgrow any line limit.
                params.update(threadId=json.loads(saved)["id"], excludeTurns=True)
                thread = await rpc.call("thread/resume", params)
                event("session_resumed", {"thread_id": params["threadId"]})
            else:
                params["ephemeral"] = not bool(session_dir)
                thread = await rpc.call("thread/start", params)
            thread_id = thread["thread"]["id"]
            scoped_home_write(
                home,
                marker,
                json.dumps(
                    {
                        "id": thread_id,
                        **({"usage_total": previous_usage} if previous_usage is not None else {}),
                    }
                ),
            )
            await sync_title(rpc, thread_id, (project or {}).get("_conversation_title"), event)
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
                    raise execution_failed("codex", item["error"])
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
                    if typ == "mcpToolCall":
                        metadata = event_metadata(content)
                        if kind.endswith("started"):
                            markers[content.get("id")] = item_markers(content)
                            if found := item_target(content):
                                metadata["target"] = found
                            metadata.update(markers[content.get("id")])
                        else:
                            metadata.update(markers.pop(content.get("id"), {}))
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
                    elif typ == "agentMessage" and kind.endswith("completed") and not seen_answer:
                        text = content.get("text", "")
                        answer += text
                        event("answer_delta", {"text": text})
                elif kind == "turn/started":
                    turn_started = True
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
                    scoped_home_write(
                        home, marker, json.dumps({"id": thread_id, "usage_total": total})
                    )
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
                        raise execution_failed("codex", turn.get("error"))
                    break
                elif kind == "error" and params.get("willRetry") is True:
                    # Codex retries on its own; its next message arrives within the watchdog.
                    event("provider_retrying", {"message": provider_message(params.get("error"))})
                elif kind == "error":
                    raise execution_failed("codex", params.get("error"))
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
