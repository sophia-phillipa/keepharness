"""Identity refusal precedes service-level history recovery and marker archival."""

import asyncio
import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest
from test_model_handoff import add_turn
from test_workspaces import config

from agent_service.app import Service
from agent_service.conversation_context import save_cursor
from agent_service.tools import ToolError
from tests.deepseek_fixtures import SAFE_CONFIG, write_deepseek_key


@pytest.fixture
def service(tmp_path):
    cfg = config(tmp_path)
    cfg["services"]["deepseek"] = {
        **cfg["services"]["local"],
        "mode": "native",
        "models": ["deepseek-flash"],
    }
    key = tmp_path / "deepseek.key"
    write_deepseek_key(key)
    cfg["deepseek"] = {
        "binary": "fixture-codex",
        "api_provider": {"url": "http://127.0.0.1:9", "key_file": str(key)},
    }
    result = Service(cfg)
    result.quota = AsyncMock(return_value={"available": False})
    yield result
    result.db.close()


def continuation(service, saved, cursor):
    previous, _ = add_turn(service, "previous", "deepseek", model="deepseek-flash")
    service.finish(previous["id"], "completed", {"answer": "prior answer", "thread_id": "prior"})
    parent = previous["id"]
    if cursor == "overflow":
        overflow, _ = add_turn(service, "overflow", "deepseek", parent, model="deepseek-flash")
        service.finish(overflow["id"], "failed", {"error": "context_limit_exceeded"})
        parent = overflow["id"]
    row, data = add_turn(service, "next", "deepseek", parent, model="deepseek-flash")
    session = service.session_folder(row, data)
    session.mkdir(parents=True, exist_ok=True)
    if saved is not None:
        (session / "native-thread.json").write_text(json.dumps(saved))
    (session / "history.jsonl").write_bytes(b'"prior local history"\n')
    if cursor in ("stale", "matching"):
        save_cursor(session, previous["id"], {"thread_id": "prior"}, "native")
        if cursor == "stale":
            path = session / "harness-context.json"
            value = json.loads(path.read_text())
            value["job_id"] = "outdated-job"
            path.write_text(json.dumps(value))
    elif cursor == "overflow":
        (session / "context-recovery.json").write_text('{"job": "older-recovery"}')
    return row, data, session


def run_service(service, row, data, calls):
    class RPC:
        def __init__(self):
            self.events = iter(
                [
                    {"method": "item/agentMessage/delta", "params": {"delta": "fixture answer"}},
                    {"method": "turn/completed", "params": {"turn": {"status": "completed"}}},
                ]
            )

        async def call(self, method, params):
            calls.append((method, params))
            if method == "config/read":
                return SAFE_CONFIG
            if method in ("thread/start", "thread/resume"):
                return {"thread": {"id": params.get("threadId", "new-thread")}}
            return {}

        async def send(self, method, params):
            calls.append((method, params))

        async def receive(self):
            return next(self.events)

    @asynccontextmanager
    async def connection(*args, **kwargs):
        yield RPC()

    with (
        patch("adapters.codex.native.connection", connection),
        patch("adapters.codex.native.resource_inputs", AsyncMock(return_value=[])),
    ):
        return asyncio.run(service.infer(row, data))


@pytest.mark.parametrize(
    "saved,error",
    [
        (
            {"id": "prior", "provider": "deepseek", "engine": "dsh"},
            "deepseek_session_identity_mismatch",
        ),
        ({"id": "prior"}, "deepseek_session_identity_ambiguous"),
    ],
)
@pytest.mark.parametrize("cursor", ["missing", "stale", "matching", "overflow"])
def test_service_refuses_deepseek_identity_before_context_archival(service, saved, error, cursor):
    row, data, session = continuation(service, saved, cursor)
    before = {path.name: path.read_bytes() for path in session.iterdir() if path.is_file()}
    calls = []
    refused = None
    try:
        run_service(service, row, data, calls)
    except ToolError as exc:
        refused = str(exc)
    assert refused == error, (
        f"Expected identity refusal; got {refused}; RPC calls: {[method for method, _ in calls]}"
    )
    assert calls == []
    assert {path.name: path.read_bytes() for path in session.iterdir() if path.is_file()} == before
    assert not list(session.glob("*.before-*"))


@pytest.mark.parametrize(
    "saved",
    [
        {"id": "prior", "adapter": "deepseek"},
        {"id": "prior", "provider": "deepseek", "engine": "codex"},
    ],
)
@pytest.mark.parametrize("cursor", ["missing", "stale", "matching", "overflow"])
def test_service_keeps_compatible_deepseek_context_recovery(service, saved, cursor):
    row, data, session = continuation(service, saved, cursor)
    marker = session / "native-thread.json"
    before = marker.read_bytes()
    calls = []
    result = run_service(service, row, data, calls)
    assert result["answer"] == "fixture answer"
    opening = [method for method, _ in calls if method in ("thread/start", "thread/resume")]
    assert opening == ["thread/resume" if cursor == "matching" else "thread/start"]
    written = json.loads(marker.read_text())
    assert (written["provider"], written["engine"]) == ("deepseek", "codex")
    sent = next(params for method, params in calls if method == "turn/start")["input"][0]["text"]
    assert ("prior answer" in sent) is (cursor != "matching")
    assert (session / "history.jsonl").read_bytes() == b'"prior local history"\n'
    if cursor != "matching":
        suffix = "context-recovery" if cursor == "overflow" else "context-transfer"
        assert marker.with_name(marker.name + ".before-" + suffix).read_bytes() == before
    else:
        assert not list(session.glob("*.before-*"))


def test_service_starts_deepseek_without_native_marker(service):
    row, data, _ = continuation(service, None, "missing")
    calls = []
    result = run_service(service, row, data, calls)
    assert result["answer"] == "fixture answer"
    assert [method for method, _ in calls if method in ("thread/start", "thread/resume")] == [
        "thread/start"
    ]
