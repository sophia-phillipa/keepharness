"""Provider reads have bounded idle waits without charging human or tool time."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from adapters.codex.rpc import RPC
from agent_service.tools import ToolError


def test_codex_silent_read_times_out():
    async def scenario():
        process = SimpleNamespace(stdout=asyncio.StreamReader())
        rpc = RPC(process, idle_timeout_seconds=0.02)
        with pytest.raises(ToolError, match="provider_idle_timeout"):
            await asyncio.wait_for(rpc.receive(), 0.3)

    asyncio.run(scenario())


def test_codex_silent_tool_remains_alive_and_idle_resumes_after_completion():
    async def scenario():
        reader = asyncio.StreamReader()
        rpc = RPC(SimpleNamespace(stdout=reader), idle_timeout_seconds=0.02)
        started = {
            "method": "item/started",
            "params": {"item": {"id": "slow", "type": "commandExecution"}},
        }
        completed = {**started, "method": "item/completed"}
        reader.feed_data((json.dumps(started) + "\n").encode())
        await rpc.receive()
        pending = asyncio.create_task(rpc.receive())
        await asyncio.sleep(0.05)
        assert not pending.done()
        reader.feed_data((json.dumps(completed) + "\n").encode())
        assert await pending == completed
        with pytest.raises(ToolError, match="provider_idle_timeout"):
            await asyncio.wait_for(rpc.receive(), 0.3)

    asyncio.run(scenario())


def test_codex_read_cancellation_releases_reader():
    async def scenario():
        rpc = RPC(SimpleNamespace(stdout=asyncio.StreamReader()), idle_timeout_seconds=30)
        pending = asyncio.create_task(rpc.receive())
        await asyncio.sleep(0)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert rpc.process.stdout._waiter is None

    asyncio.run(scenario())


def test_claude_idle_and_silent_tool(monkeypatch):
    from adapters.claude.stream import stream

    async def scenario():
        reader = asyncio.StreamReader()
        process = SimpleNamespace(
            stdout=reader,
            stderr=None,
            stdin=SimpleNamespace(write=lambda _: None, drain=AsyncMock(), close=lambda: None),
            returncode=None,
            terminate=lambda: None,
            kill=lambda: None,
            wait=AsyncMock(return_value=0),
        )
        monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
        events = []
        task = asyncio.create_task(
            stream(
                ["fake"],
                "hello",
                lambda *event: events.append(event),
                "fake",
                config={"idle_timeout_seconds": 0.02},
            )
        )
        tool = {
            "type": "stream_event",
            "event": {
                "type": "content_block_start",
                "content_block": {"type": "tool_use", "id": "slow", "name": "Bash"},
            },
        }
        reader.feed_data((json.dumps(tool) + "\n").encode())
        await asyncio.sleep(0.05)
        assert not task.done()
        reader.feed_data(
            (
                json.dumps(
                    {
                        "type": "user",
                        "message": {"content": [{"type": "tool_result", "tool_use_id": "slow"}]},
                    }
                )
                + "\n"
            ).encode()
        )
        with pytest.raises(ToolError, match="provider_idle_timeout"):
            await asyncio.wait_for(task, 0.3)
        assert [kind for kind, _ in events].count("tool_end") == 1

    asyncio.run(scenario())


def test_codex_missing_tool_completion_is_still_bounded():
    async def scenario():
        reader = asyncio.StreamReader()
        rpc = RPC(
            SimpleNamespace(stdout=reader),
            config={"idle_timeout_seconds": 0.01, "tool_idle_timeout_seconds": 0.04},
        )
        reader.feed_data(
            b'{"method":"item/started","params":{"item":{"type":"mcpToolCall","id":"stuck"}}}\n'
        )
        await rpc.receive()
        with pytest.raises(ToolError, match="provider_idle_timeout"):
            await asyncio.wait_for(rpc.receive(), 0.3)

    asyncio.run(scenario())


def test_codex_initialization_is_bounded_and_child_is_reaped(monkeypatch):
    from adapters.codex.rpc import connection

    async def scenario():
        process = SimpleNamespace(
            stdout=asyncio.StreamReader(),
            stderr=None,
            stdin=SimpleNamespace(write=lambda _: None, drain=AsyncMock()),
            returncode=None,
            terminate=AsyncMock(),
            kill=lambda: None,
            wait=AsyncMock(return_value=0),
        )
        # Synchronous termination, like asyncio.subprocess.Process.
        stopped = []
        process.terminate = lambda: stopped.append(True)
        monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
        with pytest.raises(ToolError, match="provider_idle_timeout"):
            async with connection(["fake"], config={"idle_timeout_seconds": 0.02}):
                pytest.fail("A silent initialization must never yield a connection")
        assert stopped and process.wait.await_count

    asyncio.run(scenario())


def test_claude_native_approval_wait_exceeds_idle_and_cancellation_works(tmp_path, monkeypatch):
    from adapters.claude import native

    async def scenario(cancel):
        reader = asyncio.StreamReader()
        messages, stopped = [], []

        def write(value):
            messages.append(json.loads(value))
            if messages[-1]["type"] == "control_response":
                reader.feed_data(b'{"type":"result","subtype":"success","result":"ok"}\n')

        process = SimpleNamespace(
            stdout=reader,
            stderr=None,
            stdin=SimpleNamespace(write=write, drain=AsyncMock()),
            returncode=None,
            terminate=lambda: stopped.append(True),
            kill=lambda: None,
            wait=AsyncMock(return_value=0),
        )
        monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
        monkeypatch.setattr(native, "build_command", lambda *args: ["fake"])
        waiting = asyncio.Event()

        async def approve(*args):
            waiting.set()
            await asyncio.sleep(0.05 if not cancel else 100)
            return {"approved": True}

        reader.feed_data(
            b'{"type":"control_request","request_id":"ask","request":{"subtype":"permission"}}\n'
        )
        task = asyncio.create_task(
            native.run(
                {"idle_timeout_seconds": 0.01},
                "hello",
                lambda *_: None,
                tmp_path,
                "fake",
                tmp_path,
                {},
                [],
                approve,
            )
        )
        await asyncio.wait_for(waiting.wait(), 0.3)
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert len(messages) == 1
        else:
            assert (await asyncio.wait_for(task, 0.3))["answer"] == "ok"
            assert messages[-1]["type"] == "control_response"
        assert stopped

    asyncio.run(scenario(False))
    asyncio.run(scenario(True))
