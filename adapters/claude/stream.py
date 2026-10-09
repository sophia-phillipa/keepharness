"""Claude Code stream-json adapter. Only the scoped MCP server supplies tools."""

import asyncio
import json
import math
import time

from adapters.shared.process import (
    IdleWatchdog,
    child_environment,
    process_diagnostics,
    provider_message,
)
from agent_service.tool_metadata import command_name, tool_markers, tool_target
from agent_service.tools import ToolError
from agent_service.turn_edits import CLAUDE_EDIT_TOOLS, Budget, normalize_claude

# The error kinds Claude Code puts on an assistant message, as the harness code the worker maps
# to an account condition (queue_worker.PROVIDER_CONDITIONS) or to UI copy. Any other kind stays
# a bare claude_execution_failed and its text is never shown.
PROVIDER_ERRORS = {
    "authentication_failed": "claude_authentication_failed",
    "oauth_org_not_allowed": "claude_authentication_failed",
    "account_on_hold": "claude_authentication_failed",
    "verification_required": "claude_authentication_failed",
    "cloud_credential_error": "claude_authentication_failed",
    "rate_limit": "claude_rate_limit",
    "billing_error": "provider_quota_exhausted",
    "overloaded": "provider_rate_limit",
    "server_error": "provider_unavailable",
    "model_not_found": "model_or_effort_unavailable",
}


def token_count(value):
    """A usable token count, or ``None``: a missing or malformed figure is never estimated."""
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def prompt_usage(usage):
    """The whole prompt of one model call, in the shape of Codex's per-call token usage.

    Anthropic's ``input_tokens`` leaves out cache reads and writes, so on its own it reads "2"
    for a 57k-token prompt.
    """
    fresh = token_count(usage.get("input_tokens"))
    if fresh is None:
        return None
    cached = token_count(usage.get("cache_read_input_tokens")) or 0
    total = fresh + cached + (token_count(usage.get("cache_creation_input_tokens")) or 0)
    return {"inputTokens": total, "cachedInputTokens": cached, "totalTokens": total}


def rate_limit_update(item):
    """Normalize CLI quota fractions without guessing missing percentages."""
    info = item.get("rate_limit_info", {})
    if not isinstance(info, dict):
        return None
    kind = info.get("rateLimitType", info.get("rate_limit_type"))
    durations = {
        "five_hour": 300,
        "seven_day": 10080,
        "seven_day_opus": 10080,
        "seven_day_sonnet": 10080,
        "overage": None,
    }
    if kind not in durations:
        return None
    value = info.get("utilization")
    valid = type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1
    reset = info.get("resetsAt", info.get("resets_at"))
    reset = reset if type(reset) in (int, float) and math.isfinite(reset) else None
    return {
        "provider": "claude",
        "available": valid,
        "checked_at": time.time(),
        "rateLimitsByLimitId": {
            kind: {
                "limitId": kind,
                "limitName": kind,
                "primary": {
                    "usedPercent": value * 100 if valid else None,
                    "windowDurationMins": durations[kind],
                    "resetsAt": reset,
                },
            }
        },
    }


class Stream:
    def __init__(self, event, config=None, root=None):
        self.root = root
        self.watchdog = IdleWatchdog(config)
        self.parent_tool_use_id = None

        def emit(kind, data):
            if self.parent_tool_use_id:
                data = {**data, "parent_tool_use_id": self.parent_tool_use_id}
            self.watchdog.observe(kind, data)
            event(kind, data)

        self.event = emit
        self.answer = ""
        self.thinking = ""
        self.result = None
        self.provider_error = None
        self.tools = {}
        self.message_id = None
        self.output_usage = {}
        self.context = None
        self.started = time.monotonic()
        self.first = None
        self.edits = Budget()  # turn_edit records of this run
        self.edit_inputs = {}  # full input of a pending edit tool call, until its tool_result

    def consume(self, item):
        parent = item.get("parent_tool_use_id")
        self.parent_tool_use_id = parent if isinstance(parent, str) and parent else None
        kind = item.get("type")
        if kind == "assistant":
            # Keep only known codes, never credential-bearing provider text.
            error = item.get("error")
            self.provider_error = PROVIDER_ERRORS.get(error) if isinstance(error, str) else None
            self.add_targets(item.get("message", {}).get("content"))
        elif kind == "rate_limit_event":
            update = rate_limit_update(item)
            if update:
                self.event("quota_update", update)
        elif kind == "stream_event":
            value = item.get("event", {})
            if value.get("type") == "message_start":
                self.message_id = value.get("message", {}).get("id")
            if value.get("type") in ("message_start", "message_delta"):
                self.track_usage(value)
            block = value.get("content_block", {})
            if value.get("type") == "content_block_start" and block.get("type") == "tool_use":
                tool_id = block.get("id")
                tool = block.get("name", "tool")
                metadata = {"tool": tool}
                if isinstance(tool_id, str) and tool_id:
                    metadata["tool_id"] = tool_id
                    metadata["tool_call_id"] = tool_id
                if tool == "Bash" and isinstance(block.get("input"), dict):
                    name = command_name(block["input"].get("command"))
                    if name:
                        metadata["command_name"] = name
                if found := tool_target(tool, block.get("input"), self.root):
                    metadata["target"] = found
                metadata.update(tool_markers(tool, block.get("input")))
                self.tools[tool_id] = metadata
                self.event("tool_start", metadata)
            delta = value.get("delta", {})
            if delta.get("type") == "text_delta":
                text = delta.get("text", "")
                if not self.parent_tool_use_id:
                    self.answer += text
                if text and self.first is None:
                    self.first = time.monotonic() - self.started
                self.event("answer_delta", {"text": text})
            elif delta.get("type") == "thinking_delta":
                text = delta.get("thinking", "")
                if not self.parent_tool_use_id:
                    self.thinking += text
                self.event("reasoning_delta", {"text": text})
        elif kind == "user":
            for block in item.get("message", {}).get("content", []):
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    tid = block.get("tool_use_id")
                    metadata = self.tools.pop(tid, {"tool": "tool"})
                    if isinstance(tid, str) and tid:
                        metadata = {**metadata, "tool_id": tid, "tool_call_id": tid}
                    self.event(
                        "tool_end",
                        {
                            **metadata,
                            "status": ("failed" if block.get("is_error") else "completed"),
                        },
                    )
                    # Only a successful edit changed files; errors and shell commands record nothing.
                    name, args = self.edit_inputs.pop(tid, (None, None))
                    if name and not block.get("is_error"):
                        records = normalize_claude(name, args, self.root)
                        self.edits.emit(self.event, [{**r, "tool_id": tid} for r in records])
        elif kind == "result":
            if item.get("is_error") or item.get("subtype") != "success":
                raise self.failure(item.get("result"))
            self.result = item
        if len(self.answer) + len(self.thinking) > 500000:
            raise ToolError("claude_output_limit")

    def add_targets(self, blocks):
        """Streamed tool input arrives in fragments; the full message carries it for the tool end."""
        for block in blocks if isinstance(blocks, list) else []:
            tool_id = block.get("id") if isinstance(block, dict) else None
            metadata = self.tools.get(tool_id)
            if metadata is not None and "target" not in metadata:
                found = tool_target(block.get("name"), block.get("input"), self.root)
                extra = {"target": found} if found else {}
                extra.update(tool_markers(block.get("name"), block.get("input")))
                if extra:
                    self.tools[tool_id] = {**metadata, **extra}
            if (
                metadata is not None
                and self.root is not None
                and block.get("name") in CLAUDE_EDIT_TOOLS
            ):
                self.edit_inputs[tool_id] = (block["name"], block.get("input"))

    def track_usage(self, value):
        """Report usage from a ``message_start`` or ``message_delta`` stream event."""
        usage = (value.get("message", {}) if value["type"] == "message_start" else value).get(
            "usage", {}
        )
        # The context is the last main-thread call's prompt, never a sum over calls.
        if value["type"] == "message_start" and not self.parent_tool_use_id:
            self.context = prompt_usage(usage) or self.context
        count = token_count(usage.get("output_tokens"))
        if not isinstance(self.message_id, str) or count is None:
            return
        self.output_usage[self.message_id] = count
        metrics = {
            "usage_scope": "turn",
            "output_tokens": sum(self.output_usage.values()),
            "inference_seconds": time.monotonic() - self.started,
        }
        if self.context:
            self.event("context_usage", {"last": self.context, "metrics": metrics})
        else:
            self.event("usage_metrics", metrics)

    def failure(self, text=None):
        """The error to raise; a recognised kind also carries the provider's own message."""
        error = ToolError(self.provider_error or "claude_execution_failed")
        if self.provider_error and isinstance(text, str) and text.strip():
            error.error_detail = provider_message(text)
        return error

    def finish(self, model, effort="configured"):
        if self.result is None:
            raise ToolError("claude_stream_incomplete")
        usage = self.result.get("usage", {})
        answer = self.result.get("result", self.answer)
        if not self.answer and answer:
            self.event("answer_delta", {"text": answer})
        prompt = prompt_usage(usage)
        return {
            "answer": answer,
            "backend": "claude",
            "model": model,
            "effort": effort,
            "cloud_inference": True,
            "finish_reason": "completed",
            "incomplete": False,
            "context_strategy": "replayed_history",
            **({"context_usage": {"last": self.context}} if self.context else {}),
            "metrics": {
                "usage_scope": "turn",
                # The whole prompt across the turn's calls, cache included, as Codex counts it.
                "input_tokens": prompt["inputTokens"] if prompt else None,
                "output_tokens": usage.get("output_tokens"),
                "cached_tokens": usage.get("cache_read_input_tokens"),
                "cache_creation_tokens": usage.get("cache_creation_input_tokens"),
                "ttft_seconds": self.first,
                "inference_seconds": time.monotonic() - self.started,
                "billing": "Claude account; quota unavailable",
            },
        }


async def stream(command, prompt, event, model, effort="configured", *, config=None):
    state = Stream(event, config)
    proc = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=child_environment(),
        start_new_session=True,
        limit=1024 * 1024,
    )

    async def write_prompt():
        proc.stdin.write(prompt.encode())
        await state.watchdog.wait(proc.stdin.drain())
        proc.stdin.close()

    writer = asyncio.create_task(write_prompt())
    event("planning", {"backend": "claude", "model": model, "effort": effort})
    size = 0
    async with process_diagnostics(proc, "claude", event):
        try:
            while line := await state.watchdog.wait(proc.stdout.readline()):
                size += len(line)
                if size > 8 * 1024 * 1024:
                    raise ToolError("claude_output_limit")
                try:
                    item = json.loads(line)
                    if not isinstance(item, dict):
                        raise ValueError()
                except ValueError:
                    raise ToolError("claude_invalid_stream") from None
                state.consume(item)
            await state.watchdog.wait(writer)
            if await state.watchdog.wait(proc.wait()) != 0:
                raise state.failure()
            return state.finish(model, effort)
        finally:
            writer.cancel()
            await asyncio.gather(writer, return_exceptions=True)
