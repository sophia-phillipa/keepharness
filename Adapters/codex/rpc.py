"""Codex app-server protocol: account/model metadata, without opening a model turn."""

import asyncio
import json
import os
import signal
import time
from contextlib import asynccontextmanager


class RPC:
    def __init__(self, process):
        self.process = process
        self.sequence = 0

    async def send(self, method, params=None, request=True):
        self.sequence += 1
        value = {"method": method, "params": params or {}}
        if request:
            value["id"] = self.sequence
        self.process.stdin.write((json.dumps(value) + "\n").encode())
        await self.process.stdin.drain()
        return self.sequence

    async def receive(self):
        line = await self.process.stdout.readline()
        if not line:
            raise RuntimeError("codex_connection_closed")
        return json.loads(line)

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


@asynccontextmanager
async def connection(command, stderr=asyncio.subprocess.DEVNULL, env=None):
    proc = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=stderr,
        env=env,
        start_new_session=True,
        limit=2 * 1024 * 1024,
    )
    try:
        rpc = RPC(proc)
        await rpc.initialize()
        yield rpc
    finally:
        if proc.returncode is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(proc.wait(), 5)
            except asyncio.TimeoutError:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await proc.wait()


async def metadata(binary, method):
    async with asyncio.timeout(25):
        async with connection([binary, "app-server", "--listen", "stdio://"]) as rpc:
            return await rpc.call(
                method,
                (
                    {"includeHidden": False, "limit": 100}
                    if method == "model/list"
                    else {}
                ),
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
        delta = (
            value - before
            if previous is not None and value >= before
            else last.get(key)
        )
        if isinstance(delta, (int, float)) and delta >= 0:
            result[key] = delta
    return result
