"""Provider failures must be actionable without exposing provider payloads."""

import json

import pytest

from adapters.claude.stream import Stream
from agent_service.tools import ToolError


def failure():
    return {
        "type": "result",
        "subtype": "success",
        "is_error": True,
        "result": "private provider details",
    }


@pytest.mark.parametrize(
    "code,expected",
    [
        ("authentication_failed", "claude_authentication_failed"),
        ("rate_limit", "claude_rate_limit"),
        ("unknown_private_error", "claude_execution_failed"),
    ],
)
def test_provider_failure_uses_safe_code(code, expected):
    events = []
    state = Stream(lambda *args: events.append(args))
    state.consume(
        {
            "type": "assistant",
            "error": code,
            "message": {"content": [{"type": "text", "text": "secret=private"}]},
        }
    )
    with pytest.raises(ToolError, match="^" + expected + "$"):
        state.consume(failure())
    assert not events


def test_success_after_transient_auth_error_is_not_rejected():
    state = Stream(lambda *_: None)
    state.consume({"type": "assistant", "error": "authentication_failed"})
    state.consume({"type": "result", "subtype": "success", "result": "Recovered"})
    assert state.finish("sonnet")["answer"] == "Recovered"


def test_failure_does_not_contaminate_next_turn():
    old = Stream(lambda *_: None)
    old.consume({"type": "assistant", "error": "authentication_failed"})
    new = Stream(lambda *_: None)
    with pytest.raises(ToolError, match="^claude_execution_failed$"):
        new.consume(failure())


def test_incomplete_stream_keeps_its_distinct_error():
    with pytest.raises(ToolError, match="^claude_stream_incomplete$"):
        Stream(lambda *_: None).finish("sonnet")


CODEX_QUOTA = (
    "codex_execution_failed: You've hit your usage limit. Upgrade to Pro "
    "or try again in 2 hours 13 minutes."
)


@pytest.mark.parametrize(
    "backend,error,condition",
    [
        # The old Claude adapter codes stay accepted as aliases of the generic conditions.
        ("claude", "claude_authentication_failed", "provider_authentication_required"),
        ("claude", "claude_rate_limit", "provider_quota_exhausted"),
        ("codex", CODEX_QUOTA, "provider_quota_exhausted"),
        (
            "codex",
            "codex_execution_failed: exceeded retry limit, last status: 429 Too Many Requests",
            "provider_rate_limit",
        ),
        (
            "codex",
            "codex_execution_failed: unexpected status 401 Unauthorized: token expired",
            "provider_authentication_required",
        ),
        ("codex", "provider_authentication_failed", "provider_authentication_failed"),
    ],
)
def test_worker_records_account_conditions_without_execution_failure(
    tmp_path, backend, error, condition
):
    import asyncio
    from unittest.mock import AsyncMock

    from agent_service.app import Service
    from tests.test_shared_projects import config

    async def exercise():
        cfg = config(tmp_path)
        cfg["services"]["claude"]["models"] = ["sonnet"]
        cfg["claude_models"] = ["sonnet"]
        service = Service(cfg)
        service.execute = AsyncMock(side_effect=ToolError(error))
        # A failed Codex run refreshes the quota; never call the real CLI here.
        service.quota = AsyncMock(return_value={})
        identity = ("a", service.config["clients"]["a"])
        job = service.submit(
            identity,
            {
                "project_id": "sem-projeto",
                "backend": backend,
                "model": "sonnet" if backend == "claude" else "codex-test",
                "effort": "configured" if backend == "claude" else "low",
                "prompt": "hello",
            },
        )
        worker = asyncio.create_task(service.worker())
        try:
            async with asyncio.timeout(2):
                while service.job(identity, job["job_id"])["state"] in ("queued", "running"):
                    await asyncio.sleep(0.01)
            row = service.job(identity, job["job_id"])
            data = json.loads(row["result"])
            assert row["state"] == "interrupted"
            assert data["condition"] == condition
            assert data["backend"] == backend
            assert "error" not in data
            # A provider message is kept as detail (it may carry the reset time).
            assert data.get("error_detail") == (error if ": " in error else None)
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            service.db.close()

    asyncio.run(exercise())


def test_unrelated_codex_failure_stays_a_failed_run(tmp_path):
    from agent_service.services.queue_worker import provider_condition

    assert provider_condition("codex_execution_failed: model not found") is None
    assert provider_condition("codex_execution_failed") is None
    assert provider_condition("cli_missing") is None
