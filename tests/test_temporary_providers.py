"""Temporary provider sessions never resume or write native history markers."""

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from adapters.claude import native as claude
from adapters.codex.native import RuntimeOptions, run_turn
from adapters.shared.workspace import prepare_workspace


@pytest.mark.parametrize("provider", ["codex", "deepseek", "local"])
def test_temporary_provider_starts_ephemeral_without_history_marker(tmp_path, provider):
    calls = []

    class RPC:
        async def send(self, method, params):
            calls.append((method, params))

        async def call(self, method, params):
            calls.append((method, params))
            return {"thread": {"id": "temporary-thread"}}

        async def receive(self):
            return {"method": "turn/completed", "params": {"turn": {"status": "completed"}}}

    @asynccontextmanager
    async def connection(*args, **kwargs):
        yield RPC()

    project = {"permissions": {}, "temporary_chat": True}
    workspace = prepare_workspace(project, "private-marker", tmp_path / "session")
    # A stale disk marker must not turn a temporary request into a saved resume.
    marker = workspace.home / "native-thread.json"
    marker.write_text(json.dumps({"id": "saved-thread"}))
    with patch("adapters.codex.native.connection", connection):
        asyncio.run(run_turn(
            {"temporary_chat": True}, lambda *args: None, project, "fixture",
            "configured", workspace.home, AsyncMock(), workspace,
            RuntimeOptions(["fixture"], isolated=provider == "local"), provider,
        ))
    threads = [(method, params) for method, params in calls if method.startswith("thread/")]
    assert threads[0][0] == "thread/start"
    assert threads[0][1]["ephemeral"] is True
    assert marker.read_text() == json.dumps({"id": "saved-thread"})


def test_temporary_claude_spawn_disables_persistence_and_resume(tmp_path):
    marker = tmp_path / "claude-session.json"
    marker.write_text('{"id":"saved-thread"}')
    proc = SimpleNamespace(
        stdin=SimpleNamespace(write=lambda value: None, drain=AsyncMock()),
        stdout=SimpleNamespace(readline=AsyncMock(side_effect=[
            b'{"type":"result","subtype":"success","result":"private-marker","session_id":"temporary-thread"}\n',
        ])),
    )

    @asynccontextmanager
    async def diagnostics(*args):
        yield

    with (
        patch.object(claude, "project_security", return_value={}),
        patch.object(claude, "process_diagnostics", diagnostics),
        patch.object(claude.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)) as spawn,
    ):
        asyncio.run(claude.run(
            {"binary": "fixture", "temporary_chat": True}, "private-marker",
            lambda *args: None, tmp_path, "fixture", tmp_path, {}, [], AsyncMock(),
        ))
    argv = spawn.call_args.args
    assert "--print" in argv
    assert "--no-session-persistence" in argv
    assert "--resume" not in argv
    assert marker.read_text() == '{"id":"saved-thread"}'


def test_saved_claude_command_keeps_session_persistence(tmp_path):
    command = claude.build_command({"binary": "fixture"}, "fixture", tmp_path, {}, [], "ask", [])
    assert "--no-session-persistence" not in command
