"""Gemini CLI ACP adapter with turn-scoped permissions."""

import asyncio
import json
import time
from pathlib import Path

from adapters.shared.process import process_diagnostics
from agent_service.tools import ToolError
from control.product import PRODUCT

from .policy import prepare

MAX_OUTPUT_BYTES = 8 * 1024 * 1024
MAX_TEXT_CHARS = 500000


async def run(
    config,
    prompt,
    event,
    cwd,
    model,
    home,
    permissions,
    access_mode,
    images=None,
    approve=None,
    additional_roots=None,
):
    return await run_acp(
        config,
        prompt,
        event,
        cwd,
        model,
        home,
        permissions,
        access_mode,
        images or [],
        approve,
        additional_roots or [],
    )


class AcpStream:
    """Map ACP session/update notifications to the harness event stream."""

    def __init__(self, event):
        self.event, self.answer, self.thinking = event, "", ""
        self.first, self.started = None, time.monotonic()
        self.suppressed = False

    def consume(self, params):
        if self.suppressed:
            return
        update = params.get("update", {}) if isinstance(params, dict) else {}
        kind = update.get("sessionUpdate")
        if kind == "agent_message_chunk":
            content = update.get("content", {})
            text = content.get("text", "") if isinstance(content, dict) else ""
            if text:
                self.answer += text
                self.first = (
                    self.first if self.first is not None else time.monotonic() - self.started
                )
                self.event("answer_delta", {"text": text})
        elif kind == "agent_thought_chunk":
            content = update.get("content", {})
            text = content.get("text", "") if isinstance(content, dict) else ""
            if text:
                self.thinking += text
                self.event("reasoning_delta", {"text": text})
        elif kind in ("tool_call", "tool_call_update"):
            # Titles can contain a command or file content; the protocol kind is
            # the only safe activity label for the public timeline.
            tool_kind = update.get("kind")
            metadata = {
                "tool": tool_kind
                if tool_kind
                in {
                    "read",
                    "edit",
                    "delete",
                    "move",
                    "search",
                    "fetch",
                    "execute",
                    "think",
                    "other",
                }
                else "tool"
            }
            if isinstance(update.get("toolCallId"), str):
                metadata["tool_id"] = update["toolCallId"]
            self.event(
                "tool_start"
                if kind == "tool_call" and update.get("status") == "in_progress"
                else "tool_end",
                {**metadata, "status": update.get("status", "completed")},
            )
        elif kind == "usage_update":
            self.event("context_usage", update)
        if len(self.answer) + len(self.thinking) > MAX_TEXT_CHARS:
            raise ToolError("gemini_output_limit")


class AcpConnection:
    """Minimal JSON-RPC ACP client, including the agent's permission requests."""

    def __init__(self, proc, state, approve, permissions, access_mode):
        self.proc, self.state, self.approve, self.sequence = proc, state, approve, 0
        self.permissions, self.access_mode = permissions, access_mode
        self.bytes = 0
        self.mcp_selected = False

    async def _send(self, item):
        self.proc.stdin.write((json.dumps(item) + "\n").encode())
        await self.proc.stdin.drain()

    async def _handle_request(self, item):
        method, params = item.get("method"), item.get("params", {})
        if method == "session/request_permission":
            call = params.get("toolCall", {}) if isinstance(params, dict) else {}
            kind = call.get("kind")
            allowed = {
                "read": self.permissions.get("read", False),
                "edit": self.permissions.get("write", False),
                "delete": self.permissions.get("write", False),
                "move": self.permissions.get("write", False),
                "search": self.permissions.get("read", False)
                or self.permissions.get("internet", False),
                "fetch": self.permissions.get("internet", False),
                "execute": self.permissions.get("shell", False),
                "think": True,
                "other": self.mcp_selected and self.permissions.get("internet", False),
            }.get(kind, False)
            if self.access_mode == "read_only" and kind in ("read", "search", "fetch") and allowed:
                approved = True
            elif self.access_mode in ("auto", "full") and allowed:
                approved = True
            elif self.access_mode == "ask" and allowed and self.approve:
                reply = await self.approve("gemini/" + str(call.get("title", "permission")), params)
                approved = bool(reply.get("approved"))
            else:
                approved = False
            # Never choose allow_always: Tail Harness authorization is turn-scoped.
            wanted = "allow_once" if approved else "reject_once"
            option = next(
                (x.get("optionId") for x in params.get("options", []) if x.get("kind") == wanted),
                None,
            )
            outcome = (
                {"outcome": "selected", "optionId": option} if option else {"outcome": "cancelled"}
            )
            await self._send({"jsonrpc": "2.0", "id": item["id"], "result": {"outcome": outcome}})
        elif method == "session/update":
            self.state.consume(params)
        else:
            await self._send(
                {
                    "jsonrpc": "2.0",
                    "id": item.get("id"),
                    "error": {"code": -32601, "message": "Unsupported ACP client method"},
                }
            )

    async def call(self, method, params):
        self.sequence += 1
        request_id = self.sequence
        await self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        while True:
            line = await self.proc.stdout.readline()
            if not line:
                raise ToolError("gemini_acp_incomplete")
            self.bytes += len(line)
            if self.bytes > MAX_OUTPUT_BYTES:
                raise ToolError("gemini_output_limit")
            try:
                item = json.loads(line)
                if not isinstance(item, dict):
                    raise ValueError()
            except ValueError:
                raise ToolError("gemini_acp_invalid") from None
            if item.get("method"):
                await self._handle_request(item)
            elif item.get("id") == request_id:
                if item.get("error"):
                    raise ToolError("gemini_execution_failed")
                return item.get("result", {})


async def run_acp(
    config,
    prompt,
    event,
    cwd,
    model,
    home,
    permissions,
    access_mode,
    images,
    approve,
    additional_roots,
):
    command, environment = prepare(config, home, permissions, access_mode)
    if additional_roots:
        command += ["--include-directories", *additional_roots]
    proc = await asyncio.create_subprocess_exec(
        *command,
        cwd=cwd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        limit=1024 * 1024,
        start_new_session=True,
        env=environment,
    )
    state, marker = AcpStream(event), Path(home) / "gemini-session.json"
    rpc = AcpConnection(proc, state, approve, permissions, access_mode)
    rpc.mcp_selected = bool(config.get("integrations")) and access_mode != "read_only"
    event("planning", {"backend": "gemini", "model": model, "effort": "configured"})
    async with process_diagnostics(proc, "gemini", event, environment):
        initialized = await asyncio.wait_for(
            rpc.call(
                "initialize",
                {
                    "protocolVersion": 1,
                    "clientInfo": {"name": PRODUCT.mcp_name, "version": "0.13.8"},
                    # Files and terminals are intentionally not proxied in this revision;
                    # admin policy routes the enabled native tools through ACP approval.
                    "clientCapabilities": {
                        "auth": {"terminal": False},
                        "fs": {},
                        "terminal": False,
                    },
                },
            ),
            timeout=15,
        )
        if not initialized.get("agentCapabilities", {}).get("loadSession"):
            raise ToolError("gemini_acp_unavailable")
        saved = json.loads(marker.read_text()) if marker.exists() else {}
        session_id = saved.get("id")
        if isinstance(session_id, str) and session_id:
            state.suppressed = True
            await asyncio.wait_for(
                rpc.call(
                    "session/load", {"sessionId": session_id, "cwd": str(cwd), "mcpServers": []}
                ),
                timeout=15,
            )
            state.suppressed = False
            state.answer, state.thinking, state.first, state.started = (
                "",
                "",
                None,
                time.monotonic(),
            )
            event("session_resumed", {"backend": "gemini"})
        else:
            created = await asyncio.wait_for(
                rpc.call("session/new", {"cwd": str(cwd), "mcpServers": []}), timeout=15
            )
            session_id = created.get("sessionId")
        if not isinstance(session_id, str) or not session_id:
            raise ToolError("gemini_acp_incomplete")
        await asyncio.wait_for(
            rpc.call("session/set_model", {"sessionId": session_id, "modelId": model}), timeout=15
        )
        content = [{"type": "text", "text": prompt}] + [
            {"type": "image", "data": item["data"], "mimeType": item["media_type"]}
            for item in images
        ]
        result = await rpc.call("session/prompt", {"sessionId": session_id, "prompt": content})
        if result.get("stopReason") not in ("end_turn", "max_tokens", "max_turn_requests"):
            raise ToolError("gemini_execution_failed")
        marker.write_text(json.dumps({"id": session_id}))
        marker.chmod(0o600)
        quota = result.get("_meta", {}).get("quota", {}).get("token_count", {})
        return {
            "answer": state.answer,
            "thread_id": session_id,
            "backend": "gemini",
            "model": model,
            "effort": "configured",
            "cloud_inference": True,
            "finish_reason": result.get("stopReason"),
            "incomplete": result.get("stopReason") != "end_turn",
            "context_strategy": "native_session",
            "metrics": {
                "input_tokens": quota.get("input_tokens"),
                "output_tokens": quota.get("output_tokens"),
                "ttft_seconds": state.first,
                "inference_seconds": time.monotonic() - state.started,
                "billing": "Google account quota; monetary amount unavailable",
            },
        }
