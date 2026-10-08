"""Provider/engine ownership of Codex-backed DeepSeek continuation."""

import asyncio
import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest

from adapters import run_native
from agent_service.tools import ToolError
from tests.deepseek_fixtures import SAFE_CONFIG


@pytest.mark.parametrize(
    "saved",
    [
        None,
        {"id": "prior", "adapter": "deepseek", "adapter_spec_revision": 1},
        {
            "id": "prior",
            "provider": "deepseek",
            "engine": "codex",
            "adapter": "deepseek",
            "adapter_spec_revision": 1,
        },
    ],
)
def test_deepseek_marker_identity_and_legacy_normal_write(tmp_path, saved):
    _exercise(tmp_path, saved)


@pytest.mark.parametrize(
    "saved,error",
    [
        (
            {"id": "prior", "provider": "codex", "engine": "codex"},
            "deepseek_session_identity_mismatch",
        ),
        (
            {"id": "prior", "provider": "deepseek", "engine": "dsh"},
            "deepseek_session_identity_mismatch",
        ),
        ({"id": "prior", "adapter": "local"}, "deepseek_session_identity_mismatch"),
        ({"id": "prior"}, "deepseek_session_identity_ambiguous"),
        ({"id": "prior", "provider": "deepseek"}, "deepseek_session_identity_ambiguous"),
        ({"adapter": "deepseek"}, "deepseek_session_identity_ambiguous"),
        ({}, "deepseek_session_identity_ambiguous"),
    ],
)
def test_deepseek_refuses_foreign_and_ambiguous_markers(tmp_path, saved, error):
    _exercise(tmp_path, saved, error=error)


def test_switch_to_codex_refuses_deepseek_thread(tmp_path):
    _exercise(
        tmp_path,
        {"id": "prior", "provider": "deepseek", "engine": "codex"},
        provider="codex",
        error="codex_session_identity_mismatch",
    )


@pytest.mark.parametrize("raw", ["{broken", "[]", "null"])
def test_deepseek_refuses_malformed_marker(tmp_path, raw):
    _exercise(tmp_path, raw, error="deepseek_session_identity_ambiguous")


def _exercise(tmp_path, saved, *, provider="deepseek", error=None):
    session = tmp_path / "session"
    session.mkdir()
    marker = session / "native-thread.json"
    history = session / "history.jsonl"
    history.write_text('"untouched history"\n')
    if isinstance(saved, str):
        marker.write_text(saved)
    elif saved is not None:
        saved = {**saved, "usage_total": {"totalTokens": 37}}
        marker.write_text(json.dumps(saved))
    before = marker.read_bytes() if marker.exists() else None
    requests = []

    class RPC:
        async def call(self, method, params):
            if method == "config/read":
                return SAFE_CONFIG
            requests.append((method, params))
            if method == "thread/resume":
                # Legacy enrichment happens only after successful continuation.
                assert marker.read_bytes() == before
            return {"thread": {"id": "prior" if saved else "new"}}

        async def send(self, method, params):
            requests.append((method, params))

        async def receive(self):
            return {"method": "turn/completed", "params": {"turn": {"status": "completed"}}}

    @asynccontextmanager
    async def connection(*args, **kwargs):
        yield RPC()

    key = tmp_path / "deepseek.key"
    key.write_text("fixture-only-api-key")
    (key.parent / "providers" / "deepseek").mkdir(mode=0o700, parents=True, exist_ok=True)
    (key.parent / "providers" / "home").mkdir(mode=0o700, parents=True, exist_ok=True)
    with (
        patch("adapters.codex.native.connection", connection),
        patch("adapters.codex.native.resource_inputs", AsyncMock(return_value=[])),
    ):
        turn = run_native(
            {"binary": "codex", "api_provider": {"url": "http://127.0.0.1", "key_file": str(key)}},
            "prompt",
            lambda *args: None,
            {"permissions": {}},
            "deepseek-flash",
            "high",
            session,
            provider,
            AsyncMock(),
        )
        if error:
            with pytest.raises(ToolError, match="^" + error + "$"):
                asyncio.run(turn)
            assert requests == []
            assert marker.read_bytes() == before
        else:
            result = asyncio.run(turn)
            assert result["backend"] == "deepseek"
            assert requests[0][0] == ("thread/resume" if saved else "thread/start")
            written = json.loads(marker.read_text())
            assert written["provider"] == "deepseek"
            assert written["engine"] == "codex"
            assert written["adapter"] == "deepseek"
            assert written["adapter_spec_revision"] >= 1
            assert written["id"] == ("prior" if saved else "new")
            if saved:
                assert written["usage_total"] == saved["usage_total"]
    assert history.read_text() == '"untouched history"\n'
