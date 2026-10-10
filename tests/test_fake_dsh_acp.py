"""The dsh ACP profile against an offline fixture: no dsh binary, no network, no real home."""

import asyncio
import json
import subprocess
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from adapters.gemini.native import AcpConnection, AcpStream
from agent_service.tools import ToolError

FIXTURE = Path(__file__).parent / "fixtures" / "fake-dsh-acp" / "dsh"
TIMEOUT_SECONDS = 15
CRASH_STATUS = 3
METHOD_NOT_FOUND = -32601
RESOURCE_NOT_FOUND = -32002
INITIALIZE = {
    "protocolVersion": 1,
    "clientInfo": {"name": "keepharness-test", "version": "0"},
    "clientCapabilities": {"fs": {}, "terminal": False},
}
PROMPT = [{"type": "text", "text": "hello"}]

Scenario = Callable[[asyncio.subprocess.Process], Awaitable[Any]]


def fixture_env(tmp_path: Path, **extra: str) -> dict[str, str]:
    """A child environment that can reach only the test's own homes."""
    return {
        "HOME": str(tmp_path / "home"),
        "DSH_HOME": str(tmp_path / "dsh-home"),
        "DSH_AGENTS_HOME": str(tmp_path / "agents-home"),
        **extra,
    }


async def spawn(env: dict[str, str]) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
        sys.executable,
        str(FIXTURE),
        "--profile",
        "acp",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        limit=1024 * 1024,
        env=env,
    )


async def close(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is None:
        if proc.stdin is not None:
            proc.stdin.close()
        try:
            await asyncio.wait_for(proc.wait(), TIMEOUT_SECONDS)
        except TimeoutError:
            proc.kill()
            await proc.wait()


def run_fixture(env: dict[str, str], scenario: Scenario) -> Any:
    async def main() -> Any:
        proc = await spawn(env)
        try:
            async with asyncio.timeout(TIMEOUT_SECONDS):
                return await scenario(proc)
        finally:
            await close(proc)

    return asyncio.run(main())


def connect(
    proc: asyncio.subprocess.Process,
    state: AcpStream,
    approve: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
    permissions: dict[str, bool] | None = None,
) -> AcpConnection:
    return AcpConnection(proc, state, approve, permissions or {}, "ask")


async def open_session(rpc: AcpConnection, cwd: Path) -> str:
    await rpc.call("initialize", INITIALIZE)
    created = await rpc.call("session/new", {"cwd": str(cwd), "mcpServers": []})
    return created["sessionId"]


def silent_stream() -> AcpStream:
    return AcpStream(lambda *_: None)


def test_handshake_negotiates_acp_v1_and_lists_the_new_session(tmp_path):
    async def scenario(proc):
        rpc = connect(proc, silent_stream())
        initialized = await rpc.call("initialize", INITIALIZE)
        created = await rpc.call("session/new", {"cwd": str(tmp_path), "mcpServers": []})
        listed = await rpc.call("session/list", {})
        return initialized, created, listed

    initialized, created, listed = run_fixture(fixture_env(tmp_path), scenario)
    assert initialized["protocolVersion"] == 1
    assert initialized["agentCapabilities"]["loadSession"] is False
    capabilities = initialized["agentCapabilities"]["sessionCapabilities"]
    assert {"list", "resume", "close"} <= set(capabilities)
    assert [item["sessionId"] for item in listed["sessions"]] == [created["sessionId"]]


def test_prompt_streams_message_chunks_then_the_stop_reason(tmp_path):
    events: list[tuple[str, dict[str, Any]]] = []
    state = AcpStream(lambda kind, payload: events.append((kind, payload)))

    async def scenario(proc):
        rpc = connect(proc, state)
        session_id = await open_session(rpc, tmp_path)
        return await rpc.call("session/prompt", {"sessionId": session_id, "prompt": PROMPT})

    result = run_fixture(fixture_env(tmp_path), scenario)
    assert result == {"stopReason": "end_turn"}
    assert state.answer == "chunk-1 chunk-2 chunk-3 "
    assert [kind for kind, _ in events] == ["answer_delta"] * 3


def test_owner_allow_is_relayed_as_allow_once(tmp_path):
    asked: list[dict[str, Any]] = []
    state = silent_stream()

    async def approve(_label: str, params: dict[str, Any]) -> dict[str, Any]:
        asked.append(params)
        return {"approved": True}

    async def scenario(proc):
        rpc = connect(proc, state, approve, {"shell": True})
        session_id = await open_session(rpc, tmp_path)
        return await rpc.call("session/prompt", {"sessionId": session_id, "prompt": PROMPT})

    result = run_fixture(fixture_env(tmp_path, FAKE_DSH_PERMISSION="1"), scenario)
    assert result["stopReason"] == "end_turn"
    assert state.answer == "tool:allow_once"
    assert len(asked) == 1
    assert asked[0]["toolCall"]["kind"] == "execute"


def test_owner_deny_is_relayed_as_reject_once(tmp_path):
    asked: list[dict[str, Any]] = []
    state = silent_stream()

    async def approve(_label: str, params: dict[str, Any]) -> dict[str, Any]:
        asked.append(params)
        return {"approved": False}

    async def scenario(proc):
        rpc = connect(proc, state, approve, {"shell": True})
        session_id = await open_session(rpc, tmp_path)
        return await rpc.call("session/prompt", {"sessionId": session_id, "prompt": PROMPT})

    result = run_fixture(fixture_env(tmp_path, FAKE_DSH_PERMISSION="1"), scenario)
    assert result["stopReason"] == "end_turn"
    assert state.answer == "tool:reject_once"
    assert len(asked) == 1


def test_ungranted_scope_is_rejected_without_asking_the_owner(tmp_path):
    asked: list[dict[str, Any]] = []
    state = silent_stream()

    async def approve(_label: str, params: dict[str, Any]) -> dict[str, Any]:
        asked.append(params)
        return {"approved": True}

    async def scenario(proc):
        rpc = connect(proc, state, approve, {})
        session_id = await open_session(rpc, tmp_path)
        return await rpc.call("session/prompt", {"sessionId": session_id, "prompt": PROMPT})

    run_fixture(fixture_env(tmp_path, FAKE_DSH_PERMISSION="1"), scenario)
    assert state.answer == "tool:reject_once"
    assert asked == []


class CancelOnFirstChunk(AcpStream):
    """Sends session/cancel as soon as the first chunk arrives, like a stop button."""

    def __init__(self, proc: asyncio.subprocess.Process) -> None:
        super().__init__(lambda *_: None)
        self.proc = proc
        self.chunks = 0

    def consume(self, params: Any) -> None:
        super().consume(params)
        self.chunks += 1
        if self.chunks == 1 and self.proc.stdin is not None:
            cancel = {
                "jsonrpc": "2.0",
                "method": "session/cancel",
                "params": {"sessionId": params["sessionId"]},
            }
            self.proc.stdin.write((json.dumps(cancel) + "\n").encode())


def test_cancel_stops_the_stream_and_reports_cancelled(tmp_path):
    async def scenario(proc):
        state = CancelOnFirstChunk(proc)
        rpc = connect(proc, state)
        session_id = await open_session(rpc, tmp_path)
        result = await rpc.call("session/prompt", {"sessionId": session_id, "prompt": PROMPT})
        return state, result

    env = fixture_env(tmp_path, FAKE_DSH_CHUNKS="50", FAKE_DSH_CHUNK_DELAY="0.02")
    state, result = run_fixture(env, scenario)
    assert result["stopReason"] == "cancelled"
    assert 1 <= state.chunks < 50


def test_a_process_that_exits_mid_stream_surfaces_as_an_acp_error(tmp_path):
    async def scenario(proc):
        state = silent_stream()
        rpc = connect(proc, state)
        session_id = await open_session(rpc, tmp_path)
        with pytest.raises(ToolError, match="^gemini_acp_incomplete$"):
            await rpc.call("session/prompt", {"sessionId": session_id, "prompt": PROMPT})
        status = await asyncio.wait_for(proc.wait(), TIMEOUT_SECONDS)
        return state.answer, status

    env = fixture_env(tmp_path, FAKE_DSH_CRASH_MID_STREAM="1")
    answer, status = run_fixture(env, scenario)
    assert answer == "chunk-1 "
    assert status == CRASH_STATUS


def test_session_load_is_refused_with_a_json_rpc_error(tmp_path):
    async def scenario(proc):
        rpc = connect(proc, silent_stream())
        session_id = await open_session(rpc, tmp_path)
        with pytest.raises(ToolError) as refused:
            await rpc.call(
                "session/load", {"sessionId": session_id, "cwd": str(tmp_path), "mcpServers": []}
            )
        resumed = await rpc.call(
            "session/resume", {"sessionId": session_id, "cwd": str(tmp_path), "mcpServers": []}
        )
        return refused.value, resumed

    error, resumed = run_fixture(fixture_env(tmp_path), scenario)
    assert getattr(error, "rpc_code", None) == METHOD_NOT_FOUND
    assert resumed == {}


def test_resume_survives_a_restart_and_unknown_sessions_fail(tmp_path):
    env = fixture_env(tmp_path)

    async def create(proc):
        return await open_session(connect(proc, silent_stream()), tmp_path)

    session_id = run_fixture(env, create)

    async def resume(proc):
        state = silent_stream()
        rpc = connect(proc, state)
        await rpc.call("initialize", INITIALIZE)
        await rpc.call(
            "session/resume", {"sessionId": session_id, "cwd": str(tmp_path), "mcpServers": []}
        )
        result = await rpc.call("session/prompt", {"sessionId": session_id, "prompt": PROMPT})
        with pytest.raises(ToolError) as missing:
            await rpc.call(
                "session/resume",
                {"sessionId": "no-such-session", "cwd": str(tmp_path), "mcpServers": []},
            )
        return state.answer, result, getattr(missing.value, "rpc_code", None)

    answer, result, missing_code = run_fixture(env, resume)
    assert answer == "chunk-1 chunk-2 chunk-3 "
    assert result["stopReason"] == "end_turn"
    assert missing_code == RESOURCE_NOT_FOUND


def test_the_fixture_refuses_to_run_without_an_isolated_dsh_home(tmp_path):
    completed = subprocess.run(
        [sys.executable, str(FIXTURE), "--profile", "acp"],
        input=b"",
        capture_output=True,
        env={"HOME": str(tmp_path / "home")},
        timeout=TIMEOUT_SECONDS,
        check=False,
    )
    assert completed.returncode == 2
    assert b"DSH_HOME is required" in completed.stderr
