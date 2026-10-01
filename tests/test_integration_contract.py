"""Contracts fail closed and secret bindings do not become worker configuration."""

import asyncio
import json
import os

import pytest
from test_effect_executor import configure_effects, prepared, request

from agent_service.errors import APIError
from agent_service.integrations import CredentialStore, integration_contract, validate_request


@pytest.mark.parametrize(
    "change",
    [
        {"destination": "OTHER"},
        {"operation": "jira.update_issue"},
        {"arguments": {"url": "https://elsewhere.invalid"}},
        {
            "artifact": {
                "fields": {
                    "project": {"key": "OTHER"},
                    "summary": "x",
                    "issuetype": {"name": "Task"},
                }
            }
        },
        {
            "artifact": {
                "fields": {
                    "project": {"key": "TEST"},
                    "summary": "x",
                    "issuetype": {"name": "Task"},
                    "labels": ["harness-effect-forged"],
                }
            }
        },
    ],
)
def test_request_contract_rejects_scope_escape(change):
    config = configure_effects({})
    with pytest.raises(APIError):
        validate_request(integration_contract(config, "synthetic"), {**request(), **change})


@pytest.mark.parametrize(
    "change",
    [
        {"mediated": False},
        {"endpoint": "https://secret@jira.invalid"},
        {"endpoint": "https://jira.invalid/path"},
        {"endpoint": "http://jira.invalid"},
        {"operation": "jira.transition_issue"},
        {"destination_allowlist": ['TEST" OR project=OTHER']},
        {"token": "forbidden"},
    ],
)
def test_invalid_integration_contract(change):
    config = configure_effects({})
    config["effect_integrations"][0].update(change)
    with pytest.raises(APIError):
        integration_contract(config, "synthetic")


def test_credentials_private_separate_and_symlink_refused(tmp_path):
    store = CredentialStore(tmp_path / "private" / "harness.effect_credentials.json")
    store.set("test", {"email": "fixture@example.invalid", "token": "synthetic-secret"})
    assert store.path.stat().st_mode & 0o777 == 0o600
    assert store.get("test")["token"] == "synthetic-secret"
    store.path.chmod(0o644)
    with pytest.raises(APIError, match="effect_credentials_not_private"):
        store.get("test")
    target = tmp_path / "target"
    target.write_text("{}")
    store.path.unlink()
    store.path.symlink_to(target)
    with pytest.raises(APIError, match="effect_credentials_unavailable"):
        store.set("test", {})
    assert target.read_text() == "{}"


def test_credentials_absent_from_config_events_and_logs(make_harness_config, caplog):
    async def scenario():
        before = dict(os.environ)
        app, effect = await prepared(make_harness_config)
        service = app.state.service
        assert "synthetic-secret" not in json.dumps(service.config)
        assert "synthetic-secret" not in json.dumps(effect)
        assert "synthetic-secret" not in str(
            [dict(event) for event in service.message_repository.all_events("job")]
        )
        assert "synthetic-secret" not in caplog.text
        assert dict(os.environ) == before
        await service.effects.close()
        service.db.close()

    asyncio.run(scenario())
