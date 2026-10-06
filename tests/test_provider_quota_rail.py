"""The activity rail reports a non-null quota for every provider without ever fetching one (D-032)."""

import json
import time
from unittest.mock import AsyncMock

import pytest
from test_invocation_normalization import invocation_service

from adapters.claude import account as claude_account
from adapters.deepseek import account as deepseek_account
from agent_service.services import conversation_service

FRESH = 300  # D-032: the rail trusts a quota read for five minutes
ALLOWED_KEYS = {
    "available", "reason", "kind", "balance", "checked_at", "source", "shared_account",
    "provider", "rateLimits", "rateLimitsByLimitId",
}  # fmt: skip
SECRET = "sk-" + "k" * 24


@pytest.fixture
def rail(tmp_path, monkeypatch):
    """A service with every provider enabled, whose quota fetchers fail the test when called."""
    service, identity = invocation_service(tmp_path, monkeypatch)
    for backend in ("claude", "gemini", "deepseek"):
        service.config["services"][backend] = {
            "enabled": True, "models": [backend + "-model"], "projects": ["p"],
        }  # fmt: skip
    for name in ("quota", "claude_quota", "deepseek_quota"):
        monkeypatch.setattr(service, name, AsyncMock(side_effect=AssertionError("fetched " + name)))
    monkeypatch.setattr(claude_account, "metadata", AsyncMock(side_effect=AssertionError("cli")))
    monkeypatch.setattr(
        deepseek_account, "fetch_balance", AsyncMock(side_effect=AssertionError("balance"))
    )
    yield service, identity
    service.db.close()


def quotas(service, identity):
    providers = service.activity(identity, "p")["providers"]
    return {provider["backend"]: provider["quota"] for provider in providers}


def usage(five_hour=30):
    limits = {"five_hour": {"utilization": five_hour, "resets_at": "2099-01-01T00:00:00Z"}}
    return {"rate_limits": limits}


def cache_claude(service, snapshot, age=0):
    service.claude_usage_cache = (
        service.claude_cache_key(), time.monotonic() - age, snapshot,
    )  # fmt: skip


def test_every_provider_has_an_object_quota_in_a_fixed_reason_set(rail):
    service, identity = rail
    result = quotas(service, identity)
    assert set(result) == {"codex", "claude", "gemini", "deepseek", "local"}
    assert all(isinstance(quota, dict) and quota["available"] is False for quota in result.values())
    assert result["codex"]["reason"] == "quota_not_read"
    assert result["claude"]["reason"] == "quota_not_read"
    assert result["gemini"]["reason"] == "quota_not_reported"
    assert result["local"]["reason"] == "local_no_quota"


def test_fresh_claude_cli_cache_reaches_activity_without_a_fetch(rail):
    service, identity = rail
    cache_claude(service, claude_account.quota_snapshot(usage(30)), age=FRESH - 5)
    claude = quotas(service, identity)["claude"]
    assert claude["available"] is True and claude["reason"] is None
    assert claude["source"] == "cli_usage"
    assert claude["rateLimitsByLimitId"]["five_hour"]["primary"]["usedPercent"] == 30
    service.claude_quota.assert_not_called()
    claude_account.metadata.assert_not_called()


def test_old_claude_cache_is_stale_not_current(rail):
    service, identity = rail
    cache_claude(service, claude_account.quota_snapshot(usage(30)), age=FRESH + 1)
    claude = quotas(service, identity)["claude"]
    assert claude["available"] is False and claude["reason"] == "quota_stale"
    assert "rateLimitsByLimitId" not in claude or not claude["rateLimitsByLimitId"]


def test_claude_cache_for_another_account_is_ignored(rail):
    service, identity = rail
    cache_claude(service, claude_account.quota_snapshot(usage(30)))
    service.claude_usage_cache = ("another login", *service.claude_usage_cache[1:])
    assert quotas(service, identity)["claude"]["reason"] == "quota_not_read"


def test_claude_that_reports_no_windows_is_not_reported(rail):
    service, identity = rail
    cache_claude(service, claude_account.quota_snapshot({"rate_limits_available": False}))
    claude = quotas(service, identity)["claude"]
    assert claude["available"] is False and claude["reason"] == "quota_not_reported"


def test_a_failed_claude_read_counts_as_not_read(rail):
    service, identity = rail
    cache_claude(service, {"available": False})
    assert quotas(service, identity)["claude"]["reason"] == "quota_not_read"


def test_claude_stream_observation_still_feeds_the_rail(rail):
    service, identity = rail
    bucket = {"primary": {"usedPercent": 40, "resetsAt": None}, "checked_at": time.time()}
    service.provider_usage = {identity[0]: {"five_hour": bucket}}
    claude = quotas(service, identity)["claude"]
    assert claude["available"] is True and claude["source"] == "cli_observation"
    service.provider_usage[identity[0]]["five_hour"]["checked_at"] = time.time() - FRESH - 1
    assert quotas(service, identity)["claude"]["reason"] == "quota_stale"


def test_codex_reasons_follow_the_last_read(rail, monkeypatch):
    import asyncio

    service, identity = rail
    assert quotas(service, identity)["codex"]["reason"] == "quota_not_read"
    del service.quota  # the real read, with the fixture's guard removed
    monkeypatch.setattr(
        conversation_service.codex_rpc, "metadata", AsyncMock(side_effect=OSError("down"))
    )
    asyncio.run(service.quota())
    assert quotas(service, identity)["codex"]["reason"] == "usage_unavailable"
    service.usage_cache = {"available": True, "checked_at": 1, "rateLimits": {}}
    service.usage_at = time.monotonic() - FRESH - 1
    assert quotas(service, identity)["codex"]["reason"] == "quota_stale"


def deepseek_with_key(service, tmp_path):
    key = tmp_path / "deepseek.key"
    key.write_text(SECRET)
    service.config["deepseek"] = {"api_provider": {"key_file": str(key)}}


def test_deepseek_without_a_key_is_not_reported(rail):
    service, identity = rail
    assert quotas(service, identity)["deepseek"]["reason"] == "quota_not_reported"


def test_deepseek_balance_not_read_fresh_stale_and_failed(rail, tmp_path):
    service, identity = rail
    deepseek_with_key(service, tmp_path)
    assert quotas(service, identity)["deepseek"] == {
        "available": False, "reason": "balance_not_read",
    }  # fmt: skip
    summary = deepseek_account.balance_summary(
        {
            "is_available": True,
            "balance_infos": [
                {
                    "currency": "USD", "total_balance": "12.40",
                    "granted_balance": "2.40", "topped_up_balance": "10.00",
                }
            ],
        }
    )  # fmt: skip
    fresh = {"provider": "deepseek", "available": True, "checked_at": 99.0, **summary}
    service.deepseek_usage_cache = (time.monotonic() - 10, fresh)
    assert quotas(service, identity)["deepseek"] == {
        "available": True, "reason": None, "kind": "balance", "checked_at": 99.0,
        "balance": {"amount": "12.40", "currency": "USD"},
    }  # fmt: skip
    service.deepseek_usage_cache = (time.monotonic() - FRESH - 1, fresh)
    assert quotas(service, identity)["deepseek"] == {
        "available": False, "reason": "quota_stale", "checked_at": 99.0,
    }  # fmt: skip
    failed = {"provider": "deepseek", "available": False, "reason": "balance_unavailable"}
    service.deepseek_usage_cache = (time.monotonic(), failed)
    assert quotas(service, identity)["deepseek"]["reason"] == "balance_not_read"
    service.deepseek_quota.assert_not_called()
    deepseek_account.fetch_balance.assert_not_called()


def test_payload_keys_are_allow_listed_and_leak_nothing(rail, tmp_path):
    service, identity = rail
    deepseek_with_key(service, tmp_path)
    snapshot = claude_account.quota_snapshot(usage(30))
    snapshot["account_email"] = "someone@example.com"
    cache_claude(service, snapshot)
    service.usage_cache = {"available": True, "checked_at": 1, "token": SECRET, "rateLimits": {}}
    service.usage_at = time.monotonic()
    summary = {"account_active": True, "balances": [
        {"currency": "USD", "total": "1.00", "granted": "0", "topped_up": "1.00"}
    ]}  # fmt: skip
    service.deepseek_usage_cache = (
        time.monotonic(), {"provider": "deepseek", "available": True, "checked_at": 1, "raw": SECRET, **summary},
    )  # fmt: skip
    result = quotas(service, identity)
    for quota in result.values():
        assert set(quota) <= ALLOWED_KEYS
    dump = json.dumps(result)
    assert SECRET not in dump and "someone@example.com" not in dump
    assert "balances" not in result["deepseek"] and "account_active" not in result["deepseek"]
