"""Codex app-server protocol: account/model metadata, without opening a model turn."""

import asyncio
import json
from contextlib import asynccontextmanager

from adapters.shared.process import IdleWatchdog, child_environment, process_diagnostics
from agent_service.log_config import redact
from agent_service.tools import MAX_ATTACHMENT_BYTES, ToolError

# Codex echoes the user message item on one stdout line, each attached image as base64 (4/3
# of its size). The margin covers the prompt and the JSON envelope.
READ_LIMIT = MAX_ATTACHMENT_BYTES * 4 // 3 + 8 * 1024 * 1024


class RPCError(RuntimeError):
    """A JSON-RPC error answer: ``str()`` stays ``codex_rpc_error``, ``error`` keeps its detail."""

    def __init__(self, error):
        super().__init__("codex_rpc_error")
        self.error = error if isinstance(error, dict) else {}


def provider_message(error):
    """The provider's own words from a JSON-RPC error or a TurnError, bounded and redacted.

    The HTTP status and error kind stay in the text, so a rejected key, an empty balance or a
    rate limit can still be told apart downstream (queue_worker.provider_condition).
    """
    if not isinstance(error, dict):
        return "provider error"
    parts = [str(error.get("message") or "provider error")]
    details = error.get("additionalDetails") or error.get("data")
    if details:
        parts.append(str(details))
    info = error.get("codexErrorInfo")
    if isinstance(info, str):
        parts.append(info)
    for kind, value in info.items() if isinstance(info, dict) else ():
        status = value.get("httpStatusCode") if isinstance(value, dict) else None
        parts.append(f"{kind} HTTP {status}" if status else kind)
    return redact("; ".join(parts))[:500]


def execution_failed(provider, error=None):
    """``<provider>_execution_failed``, followed by the provider's own words when it gave any."""
    return ToolError(
        provider + "_execution_failed" + (": " + provider_message(error) if error else "")
    )


class RPC:
    def __init__(self, process, idle_timeout_seconds=300, config=None, provider="codex", event=None):
        self.process = process
        self.watchdog = IdleWatchdog(
            {"idle_timeout_seconds": idle_timeout_seconds, **(config or {})}
        )
        self.sequence = 0
        self.provider = provider
        self.event = event

    async def send(self, method, params=None, request=True):
        self.sequence += 1
        value = {"method": method, "params": params or {}}
        if request:
            value["id"] = self.sequence
        self.process.stdin.write((json.dumps(value) + "\n").encode())
        await self.watchdog.wait(self.process.stdin.drain())
        return self.sequence

    async def receive(self):
        try:
            line = await self.watchdog.wait(self.process.stdout.readline())
        except ValueError as exc:  # one line above READ_LIMIT: the stream lost its framing
            raise ToolError(self.provider + "_output_limit") from exc
        if not line:
            raise RuntimeError("codex_connection_closed")
        item = json.loads(line)
        kind = item.get("method")
        if kind in ("warning", "configWarning") and self.event is not None:
            params = item.get("params", {})
            self.event("provider_warning", {
                "backend": self.provider,
                "message": provider_message({**params, "message": params.get("message") or params.get("summary"), "additionalDetails": params.get("details")}),
            })
        content = item.get("params", {}).get("item", {})
        if kind in ("item/started", "item/completed") and content.get("type") in (
            "mcpToolCall",
            "commandExecution",
            "fileChange",
            "webSearch",
            "dynamicToolCall",
        ):
            self.watchdog.observe(
                "tool_start" if kind == "item/started" else "tool_end",
                {"tool_id": content.get("id"), "tool": content.get("type")},
            )
        return item

    async def call(self, method, params=None):
        request_id = await self.send(method, params)
        while True:
            item = await self.receive()
            if item.get("id") == request_id:
                if "error" in item:
                    raise RPCError(item["error"])
                return item.get("result", {})

    async def initialize(self):
        await self.call(
            "initialize",
            {
                "clientInfo": {
                    "name": "local-agent",
                    "title": "KeepHarness local agent",
                    "version": "1.0",
                },
                "capabilities": {"experimentalApi": True},
            },
        )
        await self.send("initialized", request=False)


async def sync_title(rpc, thread_id, title, event):
    """Set display metadata without changing the model prompt or starting a turn."""
    if not isinstance(title, str) or not title.strip():
        return
    try:
        await asyncio.wait_for(
            rpc.call("thread/name/set", {"threadId": thread_id, "name": title}), 5
        )
    except (RuntimeError, asyncio.TimeoutError) as exc:
        if isinstance(exc, RuntimeError) and str(exc) != "codex_rpc_error":
            raise
        event("session_title_sync_failed", {"thread_id": thread_id})


@asynccontextmanager
async def connection(
    command,
    stderr=asyncio.subprocess.PIPE,
    env=None,
    event=None,
    config=None,
    provider="codex",
    pass_fds=(),
    kill_on_error=False,
):
    environment = child_environment(env, provider=provider)
    proc = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=stderr,
        env=environment,
        start_new_session=True,
        limit=READ_LIMIT,
        pass_fds=pass_fds,
    )
    async with process_diagnostics(proc, provider, event, env, kill_on_error=kill_on_error):
        rpc = RPC(proc, config=config, provider=provider, event=event)
        await rpc.initialize()
        yield rpc


async def metadata(binary, method, *, env=None):
    async with asyncio.timeout(25):
        async with connection([binary, "app-server", "--listen", "stdio://"], env=env) as rpc:
            return await rpc.call(
                method,
                ({"includeHidden": False, "limit": 100} if method == "model/list" else {}),
            )


if __name__ == "__main__":
    import shutil

    async def main():
        for method in ("account/rateLimits/read", "model/list"):
            value = await metadata(shutil.which("codex"), method)
            if method == "model/list":
                value = [
                    {
                        k: m.get(k)
                        for k in (
                            "id",
                            "model",
                            "displayName",
                            "supportedReasoningEfforts",
                            "defaultReasoningEffort",
                        )
                    }
                    for m in value.get("data", [])
                ][:12]
            print(method, json.dumps(value))

    asyncio.run(main())


def usage_delta(previous, current, last):
    """Turn counters, never charge a resumed thread's cumulative history again."""
    result = {}
    for key in (
        "inputTokens",
        "outputTokens",
        "cachedInputTokens",
        "reasoningOutputTokens",
    ):
        value = current.get(key)
        if not isinstance(value, (int, float)):
            continue
        before = (previous or {}).get(key, 0)
        delta = value - before if previous is not None and value >= before else last.get(key)
        if isinstance(delta, (int, float)) and delta >= 0:
            result[key] = delta
    return result
