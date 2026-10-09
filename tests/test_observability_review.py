"""Regressions from the WP-18 S4 observability review."""

import asyncio
import json
import logging
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest
from starlette.testclient import TestClient

from agent_service import log_config, tools
from agent_service.errors import ToolError
from control import local_models, persistence, remote_models
from control.manager import Manager
from control.server import create_app
from tests.owner_session import sign_in


def test_missing_bwrap_preserves_wire_code(tmp_path, monkeypatch):
    monkeypatch.setattr(tools.shutil, "which", lambda _: None)
    pdf = tmp_path / "sample.pdf"
    pdf.write_bytes(b"%PDF-1.7\n")
    with pytest.raises(ToolError) as raised:
        asyncio.run(tools.extract(pdf, pdf.name))
    assert str(raised.value) == "document_tools_unavailable"


def test_missing_bwrap_route_preserves_wire_code(client, monkeypatch):
    monkeypatch.setattr(tools.shutil, "which", lambda _: None)
    config = client.app.state.service.config
    config["uploads_enabled"] = True
    config["services"]["codex"] = {
        "enabled": True,
        "models": ["fixture"],
        "projects": ["p"],
        "permissions": {"upload": True, "read": True},
    }
    config["codex_models"] = {"fixture": ["low"]}
    response = client.post(
        "/v1/files?project_id=p", headers={"X-Filename": "sample.pdf"}, content=b"%PDF-1.7\n"
    )
    assert response.status_code == 422
    assert response.json()["code"] == "document_tools_unavailable"


def crash_start(manager, monkeypatch, output):
    monkeypatch.setattr(manager, "refresh", AsyncMock())
    monkeypatch.setattr(manager, "validate", lambda value: value)
    monkeypatch.setattr(manager, "build_runtime_config", AsyncMock(return_value={}))

    async def spawn(*args, **kwargs):
        kwargs["stderr"].write(output)
        kwargs["stderr"].flush()
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr("control.manager.asyncio.create_subprocess_exec", spawn)

    async def start():
        with patch("control.manager.socket.socket"):
            await manager.start()

    with pytest.raises(ValueError, match="exited while starting"):
        asyncio.run(start())


def test_prelogging_crash_is_in_owner_log_tail(tmp_path, monkeypatch):
    with (
        patch("control.manager.Manager.refresh", AsyncMock()),
        TestClient(create_app(tmp_path), base_url="http://127.0.0.1") as client,
    ):
        manager = client.app.state.manager
        with monkeypatch.context() as child:
            crash_start(manager, child, b"Traceback: invalid AGENT_CONFIG Bearer crash-secret\n")
        sign_in(client).get("/")
        response = client.get("/api/logs")
        assert response.status_code == 200
        lines = response.json()["lines"]
        assert any("[process output]" in line and "invalid AGENT_CONFIG" in line for line in lines)
        assert "crash-secret" not in response.text
        manager.proc = None


def test_crash_stays_in_log_tail_after_a_successful_restart(tmp_path):
    (tmp_path / "harness.log").write_bytes(b"Traceback: boom Bearer crash-secret\n")
    with log_config.open_process_log(tmp_path) as restarted:  # supervised restart succeeds
        restarted.write(b"serving\n")
    lines = log_config.log_tail(tmp_path)
    assert lines == [
        "[process output] Traceback: boom Bearer [redacted]",
        "[process output] serving",
    ]
    assert "crash-secret" not in str(lines)


def test_process_output_is_capped_across_many_starts(tmp_path, monkeypatch):
    monkeypatch.setattr(log_config, "MAX_LOG_BYTES", 512)
    path = tmp_path / "harness.log"
    path.write_bytes(b"old" * 1000)
    manager = Manager(tmp_path)
    for _ in range(12):
        with monkeypatch.context() as child:
            crash_start(manager, child, b"crash\n" * 30)
        files = list(tmp_path.glob("harness.log*"))
        assert all(item.stat().st_size <= 512 for item in files)
        assert len(files) <= 2
        assert all(item.stat().st_mode & 0o077 == 0 for item in files)


@pytest.mark.parametrize("status", [200, 301, 302, 307, 400, 401, 403, 404, 405, 422, 500, 501])
def test_responses_probe_uses_status_not_vendor_wording(monkeypatch, status):
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(status, json={"error": "No prompt provided"})
            ),
            **kwargs,
        ),
    )
    if status in (301, 302, 307, 404, 405, 501):  # a redirect is not followed, so not evidence
        with pytest.raises(ValueError, match="responses_api_unavailable"):
            asyncio.run(remote_models.require_responses_api("http://model.test"))
    else:
        asyncio.run(remote_models.require_responses_api("http://model.test"))


@pytest.mark.parametrize("status", [200, 400, 422])
def test_responses_probe_refuses_non_json(monkeypatch, status):
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(
            transport=httpx.MockTransport(lambda _: httpx.Response(status, text="not JSON")),
            **kwargs,
        ),
    )
    with pytest.raises(ValueError, match="responses_api_unavailable"):
        asyncio.run(remote_models.require_responses_api("http://model.test"))


def test_local_discovery_keeps_runtime_with_vendor_validation(monkeypatch):
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        local_models, "processes", lambda: [{"url": "http://local.test", "key_file": ""}]
    )
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200 if request.url.path.endswith("/models") else 400,
                    json={"data": [{"id": "working-model"}]}
                    if request.url.path.endswith("/models")
                    else {"error": "No prompt provided"},
                )
            ),
            **kwargs,
        ),
    )
    errors = []
    assert [row["id"] for row in asyncio.run(local_models.discover(errors=errors))] == [
        "working-model"
    ]
    assert errors == []


def test_log_tail_marks_a_single_oversized_record(tmp_path):
    folder = tmp_path / "logs"
    folder.mkdir()
    (folder / "harness.log").write_bytes(b"secret" * log_config.LOG_TAIL_BYTES)
    assert log_config.log_tail(tmp_path) == ["[line truncated]"]


def test_rotating_handler_formats_record_once(tmp_path):
    handler = log_config.PrivateRotatingHandler(tmp_path / "log", maxBytes=100, backupCount=1)
    formatter = Mock(wraps=logging.Formatter())
    handler.setFormatter(formatter)
    try:
        handler.emit(logging.makeLogRecord({"msg": "first"}))
        formatter.format.reset_mock()
        handler.emit(logging.makeLogRecord({"msg": "second"}))
        assert formatter.format.call_count == 1
    finally:
        handler.close()


def test_rotating_handler_preserves_nonregular_file_guard(tmp_path, monkeypatch):
    handler = log_config.PrivateRotatingHandler(tmp_path / "log", maxBytes=10, backupCount=1)
    try:
        handler.stream.write("existing\n")
        handler.stream.flush()
        monkeypatch.setattr(os.path, "isfile", lambda _: False)
        assert handler.shouldRollover(logging.makeLogRecord({"msg": "overflow"})) is False
    finally:
        handler.close()


def test_audit_fsync_precedes_replace(tmp_path, monkeypatch):
    events = []
    real_replace = type(tmp_path).replace

    def sync(fd):
        temporary = tmp_path / "audit.tmp"
        assert os.fstat(fd).st_ino == temporary.stat().st_ino
        assert json.loads(temporary.read_text())["action"] == "saved"
        events.append("fsync")

    def replace(path, target):
        events.append("replace")
        return real_replace(path, target)

    monkeypatch.setattr(persistence.os, "fsync", sync)
    monkeypatch.setattr(type(tmp_path), "replace", replace)
    persistence.ControlStateRepository(tmp_path).audit("saved")
    assert events == ["fsync", "replace"]


def test_rotating_handler_isolates_formatter_failure(tmp_path, monkeypatch):
    handler = log_config.PrivateRotatingHandler(tmp_path / "log", maxBytes=100, backupCount=1)
    formatter = Mock()
    formatter.format.side_effect = ValueError("invalid log format")
    handler.setFormatter(formatter)
    error = Mock()
    monkeypatch.setattr(handler, "handleError", error)
    record = logging.makeLogRecord({"msg": "entry"})
    try:
        handler.emit(record)
        error.assert_called_once_with(record)
    finally:
        handler.close()


def test_log_tail_reads_the_active_log_and_its_backup_within_one_budget(tmp_path, monkeypatch):
    budget = log_config.LOG_TAIL_BYTES
    (tmp_path / "harness.log.1").write_bytes(
        b"".join(b"old %06d\n" % n for n in range(budget // 8))
    )
    (tmp_path / "harness.log").write_bytes(b"".join(b"new %06d\n" % n for n in range(budget // 8)))
    reads = []
    real = log_config._file_tail

    def spy(path, lines, byte_limit):
        if path.exists():
            reads.append(min(path.stat().st_size, byte_limit))
        return real(path, lines, byte_limit)

    monkeypatch.setattr(log_config, "_file_tail", spy)
    lines = log_config.log_tail(tmp_path)
    assert sum(reads) <= budget
    assert lines[-1] == "[process output] new %06d" % (budget // 8 - 1)
    assert not any("old" in line for line in lines)
