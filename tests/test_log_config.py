import asyncio
import logging
import sys
import time
from unittest.mock import AsyncMock, patch

import pytest

from agent_service.errors import ToolError
from agent_service.log_config import HANDLER_NAME, RedactingFilter, configure_logging, redact


@pytest.fixture
def root_logger(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    root = logging.getLogger()
    level, handlers = root.level, list(root.handlers)
    yield root
    for handler in root.handlers:
        if handler not in handlers:
            handler.close()
    root.setLevel(level)
    root.handlers[:] = handlers


def test_default_level_is_warning_with_one_redacting_stderr_handler(root_logger, monkeypatch):
    monkeypatch.delenv("KEEPHARNESS_LOG_LEVEL", raising=False)
    configure_logging()
    configure_logging()
    ours = [handler for handler in root_logger.handlers if handler.name == HANDLER_NAME]
    assert root_logger.level == logging.WARNING
    assert len(ours) == 1
    assert ours[0].stream is sys.stderr
    assert any(isinstance(item, RedactingFilter) for item in ours[0].filters)


@pytest.mark.parametrize(
    "value,level",
    [("debug", logging.DEBUG), (" INFO ", logging.INFO), ("nonsense", logging.WARNING)],
)
def test_level_comes_from_the_environment(root_logger, monkeypatch, value, level):
    monkeypatch.setenv("KEEPHARNESS_LOG_LEVEL", value)
    configure_logging()
    assert root_logger.level == level


def test_configuring_is_left_to_entry_points(root_logger):
    names = [handler.name for handler in logging.getLogger().handlers]
    assert HANDLER_NAME not in names


@pytest.mark.parametrize(
    "text,secret",
    [
        ("Authorization: Bearer abc.def-123", "abc.def-123"),
        ("cookie harness_token=s3cret; other=1", "s3cret"),
        ("Cookie: admin=0123abcd", "0123abcd"),
        ('{"token": "hunter2", "project": "p"}', "hunter2"),
        ("key sk-ant-api03-XYZ_1 used", "ant-api03-XYZ_1"),
    ],
)
def test_redact_masks_secrets(text, secret):
    masked = redact(text)
    assert secret not in masked
    assert "[redacted]" in masked


def test_redact_masks_a_url_password_containing_an_at_sign():
    assert redact("clone https://user:p@ss@host/x") == "clone https://[redacted]@host/x"


def test_filter_masks_arguments_and_tracebacks():
    try:
        raise RuntimeError("Bearer leaked-token")
    except RuntimeError:
        record = logging.LogRecord(
            "x", logging.ERROR, __file__, 1, "token %s", ("sk-secret",), sys.exc_info()
        )
    assert RedactingFilter().filter(record)
    formatted = logging.Formatter().format(record)
    assert "sk-secret" not in formatted
    assert "leaked-token" not in formatted
    assert "RuntimeError" in formatted


def test_internal_error_is_logged_with_its_request_id(client, caplog):
    service = client.app.state.service
    with (
        patch.object(service, "conversation_rows", side_effect=RuntimeError("boom")),
        caplog.at_level(logging.ERROR),
    ):
        response = client.get("/v1/conversations")
    assert response.status_code == 500
    request_id = response.json()["request_id"]
    records = [record for record in caplog.records if request_id in record.getMessage()]
    assert records and records[0].exc_info[0] is RuntimeError


@pytest.mark.parametrize(
    "error,logged", [(RuntimeError("boom"), True), (ToolError("known"), False)]
)
def test_worker_logs_only_unexpected_failures(tmp_path, caplog, error, logged):
    from agent_service.app import Service
    from tests.test_shared_projects import config

    async def exercise():
        cfg = config(tmp_path)
        cfg["services"]["claude"]["models"] = ["sonnet"]
        cfg["claude_models"] = ["sonnet"]
        service = Service(cfg)
        service.execute = AsyncMock(side_effect=error)
        identity = ("a", service.config["clients"]["a"])
        job = service.submit(
            identity,
            {
                "project_id": "sem-projeto",
                "backend": "claude",
                "model": "sonnet",
                "effort": "configured",
                "prompt": "hello",
            },
        )
        worker = asyncio.create_task(service.worker())
        try:
            async with asyncio.timeout(2):
                while service.job(identity, job["job_id"])["state"] in ("queued", "running"):
                    await asyncio.sleep(0.01)
            assert service.job(identity, job["job_id"])["state"] == "failed"
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            service.db.close()
        return job["job_id"]

    with caplog.at_level(logging.ERROR):
        job_id = asyncio.run(exercise())
    records = [record for record in caplog.records if job_id in record.getMessage()]
    assert bool(records) is logged


def test_runtime_reload_failure_is_logged_once(make_harness_config, tmp_path, caplog):
    from starlette.testclient import TestClient

    from agent_service.app import create_app

    runtime = tmp_path / "runtime.json"
    runtime.write_text("{not json")
    with (
        caplog.at_level(logging.WARNING),
        TestClient(create_app(make_harness_config(), runtime)) as client,
    ):
        service = client.app.state.service
        deadline = time.monotonic() + 2
        while service.config_reload_error is None and time.monotonic() < deadline:
            time.sleep(0.05)
        time.sleep(0.6)
    assert service.config_reload_error == "runtime_config_invalid"
    records = [
        record for record in caplog.records if "Runtime config reload failed" in record.getMessage()
    ]
    assert len(records) == 1
