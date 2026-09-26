"""MCP bridge authentication and error shapes against the real harness app (P5-19, F-10)."""

import asyncio
import hashlib

import httpx
import pytest

from agent_service import mcp_bridge
from agent_service.app import create_app

REAL_CLIENT = httpx.AsyncClient


def bridge_to(monkeypatch, transport, key_file=None):
    cfg = {"url": "http://127.0.0.1:18095"}
    if key_file:
        cfg["key_file"] = str(key_file)
    monkeypatch.setattr(mcp_bridge, "config", lambda: dict(cfg))
    monkeypatch.setattr(
        mcp_bridge.httpx,
        "AsyncClient",
        lambda **kwargs: REAL_CLIENT(transport=transport, **kwargs),
    )


@pytest.fixture
def harness(make_harness_config):
    cfg = make_harness_config(
        local_access=False,
        clients={"mcp": {"sha256": hashlib.sha256(b"right").hexdigest(), "projects": []}},
    )
    app = create_app(cfg)
    yield httpx.ASGITransport(app=app, client=("127.0.0.1", 40000))
    app.state.service.db.close()


@pytest.mark.parametrize("token", [None, "wrong"])
def test_missing_or_wrong_token_is_reported_as_401(monkeypatch, tmp_path, harness, token):
    key_file = None
    if token:
        key_file = tmp_path / "key"
        key_file.write_text(token + "\n")
    bridge_to(monkeypatch, harness, key_file)
    result = asyncio.run(mcp_bridge.local_capabilities())
    assert result["http_status"] == 401
    assert result["error"]["code"] == "authentication_required"


def test_right_token_reads_capabilities(monkeypatch, tmp_path, harness):
    key_file = tmp_path / "key"
    key_file.write_text("right\n")
    bridge_to(monkeypatch, harness, key_file)
    result = asyncio.run(mcp_bridge.local_capabilities())
    assert "http_status" not in result
    assert "backends" in result


@pytest.mark.parametrize("status", [502, 200])
def test_non_json_body_becomes_a_structured_error(monkeypatch, status):
    page = "<html><body>Bad gateway</body></html>"
    transport = httpx.MockTransport(
        lambda request: httpx.Response(status, text=page, headers={"content-type": "text/html"})
    )
    bridge_to(monkeypatch, transport)
    result = asyncio.run(mcp_bridge.local_capabilities())
    assert result == {
        "error": {"code": "invalid_response", "message": page},
        "http_status": status,
    }
