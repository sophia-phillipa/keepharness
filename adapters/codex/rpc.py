"""Codex app-server protocol: account/model metadata, without opening a model turn."""

import asyncio
import json
from contextlib import asynccontextmanager

from adapters.shared.process import IdleWatchdog, child_environment, process_diagnostics


class RPC:
    def __init__(self, process, idle_timeout_seconds=300, config=None):
        self.process = process
        self.watchdog = IdleWatchdog(
            {"idle_timeout_seconds": idle_timeout_seconds, **(config or {})}
        )
        self.sequence = 0

    async def send(self, method, params=None, request=True):
        self.sequence += 1
        value = {"method": method, "params": params or {}}
        if request:
            value["id"] = self.sequence
        self.process.stdin.write((json.dumps(value) + "\n").encode())
        await self.watchdog.wait(self.process.stdin.drain())
        return self.sequence

    async def receive(self):
        line = await self.watchdog.wait(self.process.stdout.readline())
        if not line:
            raise RuntimeError("codex_connection_closed")
        item = json.loads(line)
        kind = item.get("method")
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
                    raise RuntimeError("codex_rpc_error")
                return item.get("result", {})

    async def initialize(self):
        await self.call(
            "initialize",
            {
                "clientInfo": {
                    "name": "local-agent",
                    "title": "Tail Harness local agent",
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
):
    environment = child_environment(env, provider=provider)
    proc = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=stderr,
        env=environment,
        start_new_session=True,
        limit=2 * 1024 * 1024,
        pass_fds=pass_fds,
    )
    async with process_diagnostics(proc, provider, event, env):
        rpc = RPC(proc, config=config)
        await rpc.initialize()
        yield rpc


async def metadata(binary, method):
    async with asyncio.timeout(25):
        async with connection([binary, "app-server", "--listen", "stdio://"]) as rpc:
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
