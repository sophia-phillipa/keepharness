"""Claude Code stream-json adapter. Only the scoped MCP server supplies tools."""

import asyncio
import json
import math
import time
from agent_service.tools import ToolError
from agent_service.tool_metadata import command_name


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
    def __init__(self, event):
        self.event = event
        self.answer = ""
        self.thinking = ""
        self.result = None
        self.provider_error = None
        self.tools = {}
        self.started = time.monotonic()
        self.first = None

    def consume(self, item):
        kind = item.get("type")
        if kind == "assistant":
            # Keep only known codes, never credential-bearing provider text.
            error = item.get("error")
            self.provider_error = (
                {"authentication_failed": "claude_authentication_failed",
                 "rate_limit": "claude_rate_limit"}.get(error)
                if isinstance(error, str) else None
            )
        elif kind == "rate_limit_event":
            update = rate_limit_update(item)
            if update:
                self.event("quota_update", update)
        elif kind == "stream_event":
            value = item.get("event", {})
            block = value.get("content_block", {})
            if (
                value.get("type") == "content_block_start"
                and block.get("type") == "tool_use"
            ):
                tool_id = block.get("id")
                tool = block.get("name", "tool")
                metadata = {"tool": tool}
                if isinstance(tool_id, str) and tool_id:
                    metadata["tool_id"] = tool_id
                if tool == "Bash" and isinstance(block.get("input"), dict):
                    name = command_name(block["input"].get("command"))
                    if name:
                        metadata["command_name"] = name
                self.tools[tool_id] = metadata
                self.event("tool_start", metadata)
            delta = value.get("delta", {})
            if delta.get("type") == "text_delta":
                text = delta.get("text", "")
                self.answer += text
                if text and self.first is None:
                    self.first = time.monotonic() - self.started
                self.event("answer_delta", {"text": text})
            elif delta.get("type") == "thinking_delta":
                text = delta.get("thinking", "")
                self.thinking += text
                self.event("reasoning_delta", {"text": text})
        elif kind == "user":
            for block in item.get("message", {}).get("content", []):
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    tid = block.get("tool_use_id")
                    metadata = self.tools.pop(tid, {"tool": "tool"})
                    if isinstance(tid, str) and tid:
                        metadata = {**metadata, "tool_id": tid}
                    self.event(
                        "tool_end",
                        {
                            **metadata,
                            "status": (
                                "failed" if block.get("is_error") else "completed"
                            ),
                        },
                    )
        elif kind == "result":
            if item.get("is_error") or item.get("subtype") != "success":
                raise ToolError(self.provider_error or "claude_execution_failed")
            self.result = item
        if len(self.answer) + len(self.thinking) > 500000:
            raise ToolError("claude_output_limit")

    def finish(self, model):
        if self.result is None:
            raise ToolError("claude_stream_incomplete")
        usage = self.result.get("usage", {})
        answer = self.result.get("result", self.answer)
        if not self.answer and answer:
            self.event("answer_delta", {"text": answer})
        return {
            "answer": answer,
            "backend": "claude",
            "model": model,
            "effort": "configured",
            "cloud_inference": True,
            "finish_reason": "completed",
            "incomplete": False,
            "context_strategy": "replayed_history",
            "metrics": {
                "input_tokens": usage.get("input_tokens"),
                "output_tokens": usage.get("output_tokens"),
                "cached_tokens": usage.get("cache_read_input_tokens"),
                "cache_creation_tokens": usage.get("cache_creation_input_tokens"),
                "ttft_seconds": self.first,
                "inference_seconds": time.monotonic() - self.started,
                "billing": "Claude account; quota unavailable",
            },
        }


async def stream(command, prompt, event, model):
    state = Stream(event)
    proc = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        limit=1024 * 1024,
    )

    async def write_prompt():
        proc.stdin.write(prompt.encode())
        await proc.stdin.drain()
        proc.stdin.close()

    writer = asyncio.create_task(write_prompt())
    event("planning", {"backend": "claude", "model": model, "effort": "configured"})
    size = 0
    try:
        async for line in proc.stdout:
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
        await writer
        if await proc.wait() != 0:
            raise ToolError(state.provider_error or "claude_execution_failed")
        return state.finish(model)
    finally:
        writer.cancel()
        await asyncio.gather(writer, return_exceptions=True)
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), 3)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
