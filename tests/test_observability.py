"""WP-18 S4: bounded diagnostics, environment admission and owner-only logs."""

import asyncio
import json
import logging
import re
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from starlette.testclient import TestClient

from agent_service import log_config, tools
from agent_service.errors import ToolError
from control import discovery, local_models, persistence, remote_models
from control.server import create_app
from tests.owner_session import sign_in


def test_rotating_file_is_bounded_redacted_and_iso(tmp_path, monkeypatch):
    monkeypatch.setattr(log_config, "MAX_LOG_BYTES", 1024)
    root = logging.getLogger()
    before, level = list(root.handlers), root.level
    try:
        log_config.configure_logging(tmp_path)
        for i in range(80):
            root.warning("entry %s Bearer secret-token %s", i, "é" * 150)
        root.warning("oversize %s sk-secret", "x" * 8000)
        files = list((tmp_path / "logs").glob("harness.log*"))
        assert len(files) == log_config.LOG_BACKUP_COUNT + 1
        assert all(0 < p.stat().st_size <= 1024 for p in files)
        content = "".join(p.read_text() for p in files)
        assert "secret-token" not in content and "sk-secret" not in content
        assert "[redacted]" in content
        assert re.search(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ WARNING", content)
        assert all(p.stat().st_mode & 0o077 == 0 for p in files)
    finally:
        for handler in root.handlers:
            if handler not in before:
                handler.close()
        root.handlers[:] = before
        root.setLevel(level)


def test_audit_retains_newest_entries_and_legacy_jsonl(tmp_path, monkeypatch):
    monkeypatch.setattr(persistence, "AUDIT_MAX_ENTRIES", 12)
    path = tmp_path / "audit.jsonl"
    legacy = {"time": 1, "action": "legacy"}
    path.write_text(json.dumps(legacy) + "\n")
    repo = persistence.ControlStateRepository(tmp_path)
    repo.audit("new")
    assert json.loads(path.read_text().splitlines()[0]) == legacy
    for i in range(40):
        repo.audit(str(i))
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert [row["action"] for row in rows] == [str(i) for i in range(28, 40)]
    assert path.stat().st_mode & 0o077 == 0
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("present", [True, False])
def test_dependency_inventory_with_fake_path(tmp_path, monkeypatch, present):
    monkeypatch.setenv("PATH", str(tmp_path))
    if present:
        for name in ("ffmpeg", "bwrap", "prlimit"):
            path = tmp_path / name
            path.write_text("#!/bin/sh\nexit 0\n")
            path.chmod(0o700)
    rows = discovery.tool_inventory()
    assert {row["name"] for row in rows} == {"ffmpeg", "bwrap", "prlimit"}
    for row in rows:
        assert row["present"] is present
        hints = " ".join(row["package_hints"].values())
        assert all(word in hints for word in ("apt", "pacman", "rpm-ostree", "distrobox"))
    assert "bubblewrap" in str(next(row for row in rows if row["name"] == "bwrap"))


def test_missing_bwrap_has_named_document_error(tmp_path, monkeypatch):
    monkeypatch.setattr(
        tools.shutil, "which", lambda name: None if name == "bwrap" else "/bin/" + name
    )
    pdf = tmp_path / "sample.pdf"
    pdf.write_bytes(b"%PDF-1.7\n")
    with pytest.raises(ToolError) as raised:
        asyncio.run(tools.extract(pdf, pdf.name))
    assert raised.value.code == "document_tools_unavailable"
    assert "bwrap" in str(raised.value)


@pytest.mark.parametrize("status", [404, 405, 501])
def test_model_server_without_responses_is_refused(monkeypatch, status):
    real_client = httpx.AsyncClient
    requests = []

    def answer(request):
        requests.append(request)
        return (
            httpx.Response(200, json={"data": [{"id": "model"}]})
            if request.url.path.endswith("/models")
            else httpx.Response(status)
        )

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(answer), **kwargs),
    )
    with pytest.raises(ValueError, match="responses_api_unavailable.*Responses API"):
        asyncio.run(remote_models.probe("http://model.test"))
    assert requests[-1].method == "POST"
    assert json.loads(requests[-1].content) == {}


def test_log_tail_is_bounded_redacted_and_owner_local_only(tmp_path):
    folder = tmp_path / "logs"
    folder.mkdir()
    (folder / "harness.log").write_text("old\n" * 30000 + "Bearer tail-secret\n" + "end\n")
    with (
        patch("control.manager.Manager.refresh", AsyncMock()),
        TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8094") as client,
    ):
        assert client.get("/api/logs").status_code == 401
        sign_in(client).get("/")
        response = client.get("/api/logs?lines=2")
        assert response.status_code == 200
        assert response.json()["lines"] == ["Bearer [redacted]", "end"]
        assert len(client.get("/api/logs?lines=999999").json()["lines"]) <= 200
        assert len(client.get("/api/logs").content) < 100000
        assert client.get("/api/logs?lines=bad").status_code == 400
        assert client.get("/api/logs", headers={"Host": "evil.test"}).status_code == 403
        assert (
            client.get(
                "/api/logs", headers={"Tailscale-User-Login": "owner@example.com"}
            ).status_code
            == 403
        )
        (folder / "harness.log").unlink()
        assert client.get("/api/logs").json()["lines"] == []


@pytest.mark.parametrize(
    "status,payload,accepted",
    [
        (400, {"error": {"message": "Missing required parameter: model"}}, True),
        (422, {"detail": [{"type": "missing", "loc": ["body", "input"]}]}, True),
        (400, {"error": {"message": "Bad request"}}, False),
        (422, {"error": "route not found"}, False),
        (200, {"data": [{"id": "model"}]}, False),
    ],
)
def test_responses_preflight_requires_validation_evidence(monkeypatch, status, payload, accepted):
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(
            transport=httpx.MockTransport(lambda _: httpx.Response(status, json=payload)), **kwargs
        ),
    )
    if accepted:
        asyncio.run(remote_models.require_responses_api("http://model.test"))
    else:
        with pytest.raises(ValueError, match="responses_api_unavailable"):
            asyncio.run(remote_models.require_responses_api("http://model.test"))


def test_log_tail_discards_partial_secret_line(tmp_path):
    folder = tmp_path / "logs"
    folder.mkdir()
    (folder / "harness.log").write_text("Bearer " + "secret" * 14000 + "\nsafe\n")
    assert log_config.log_tail(tmp_path) == ["safe"]


def test_audit_failed_replace_preserves_prior_entries(tmp_path, monkeypatch):
    repo = persistence.ControlStateRepository(tmp_path)
    repo.audit("original")
    path = tmp_path / "audit.jsonl"
    previous = path.read_bytes()
    monkeypatch.setattr(
        type(path), "replace", lambda *args: (_ for _ in ()).throw(OSError("disk full"))
    )
    with pytest.raises(OSError):
        repo.audit("not saved")
    assert path.read_bytes() == previous
    assert not list(tmp_path.glob("*.tmp"))


def test_local_model_without_responses_is_refused_with_diagnostic(monkeypatch):
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        local_models, "processes", lambda: [{"url": "http://local.test", "key_file": ""}]
    )
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(
            transport=httpx.MockTransport(
                lambda request: (
                    httpx.Response(200, json={"data": [{"id": "model"}]})
                    if request.url.path.endswith("/models")
                    else httpx.Response(404)
                )
            ),
            **kwargs,
        ),
    )
    errors = []
    assert asyncio.run(local_models.discover(errors=errors)) == []
    assert errors and "responses_api_unavailable" in errors[0]["error"]
