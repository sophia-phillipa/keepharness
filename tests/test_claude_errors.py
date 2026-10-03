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


@pytest.mark.parametrize(
    "kind,expected",
    [
        # Every kind Claude Code 2.1 reports, as the harness code the worker can map.
        ("authentication_failed", "claude_authentication_failed"),
        ("oauth_org_not_allowed", "claude_authentication_failed"),
        ("account_on_hold", "claude_authentication_failed"),
        ("verification_required", "claude_authentication_failed"),
        ("cloud_credential_error", "claude_authentication_failed"),
        ("rate_limit", "claude_rate_limit"),
        ("billing_error", "provider_quota_exhausted"),
        ("overloaded", "provider_rate_limit"),
        ("server_error", "provider_unavailable"),
        ("model_not_found", "model_or_effort_unavailable"),
        ("invalid_request", "claude_execution_failed"),
        ("max_output_tokens", "claude_execution_failed"),
        ("unknown", "claude_execution_failed"),
    ],
)
def test_every_claude_error_kind_maps_to_a_code_with_guidance(kind, expected):
    state = Stream(lambda *_: None)
    state.consume({"type": "assistant", "error": kind})
    with pytest.raises(ToolError, match="^" + expected + "$"):
        state.consume(failure())


def test_a_recognised_kind_carries_the_providers_message_as_one_bounded_redacted_line():
    state = Stream(lambda *_: None)
    state.consume({"type": "assistant", "error": "billing_error"})
    text = "Bearer abc123secret you've hit your limit, resets 3pm\n(America/Sao_Paulo) " + "x" * 400
    with pytest.raises(ToolError) as caught:
        state.consume({**failure(), "result": text})
    detail = caught.value.error_detail
    assert detail.startswith("Bearer [redacted] you've hit your limit, resets 3pm (America/Sao")
    assert "abc123secret" not in detail and "\n" not in detail and len(detail) <= 300


def test_an_unrecognised_kind_never_exposes_the_providers_text():
    state = Stream(lambda *_: None)
    state.consume({"type": "assistant", "error": "unknown_private_error"})
    with pytest.raises(ToolError) as caught:
        state.consume(failure())
    assert not hasattr(caught.value, "error_detail")


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
        ("claude", "provider_quota_exhausted", "provider_quota_exhausted"),
        ("claude", "provider_rate_limit", "provider_rate_limit"),
        ("claude", "provider_unavailable", "provider_unavailable"),
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


@pytest.mark.parametrize(
    "code,state,field",
    [
        ("provider_quota_exhausted", "interrupted", "condition"),
        ("model_or_effort_unavailable", "failed", "error"),
    ],
)
def test_worker_keeps_the_claude_message_for_the_ui_to_show(tmp_path, code, state, field):
    import asyncio
    from unittest.mock import AsyncMock

    from agent_service.app import Service
    from tests.test_shared_projects import config

    async def exercise():
        cfg = config(tmp_path)
        cfg["services"]["claude"]["models"] = ["sonnet"]
        cfg["claude_models"] = ["sonnet"]
        service = Service(cfg)
        failure = ToolError(code)
        failure.error_detail = "You've hit your limit, resets 3pm"
        service.execute = AsyncMock(side_effect=failure)
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
            row = service.job(identity, job["job_id"])
            data = json.loads(row["result"])
            assert row["state"] == state
            assert data[field] == code
            assert data["error_detail"] == "You've hit your limit, resets 3pm"
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            service.db.close()

    asyncio.run(exercise())
