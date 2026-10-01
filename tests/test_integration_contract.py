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


def test_generic_binding_scope_precedence_and_mediation(tmp_path, monkeypatch):
    from agent_service.integrations import integration_environment
    from agent_service.secret_vault import SecretVault

    path = tmp_path / "vault"
    SecretVault(path).set("demo", {"token": "fake-binding-value"})
    config = {
        "secret_vault_path": str(path),
        "state_dir": str(tmp_path),
        "integrations": [
            {
                "integration": "demo",
                "consumers": ["codex"],
                "environment": {"DEMO_TOKEN": "token"},
                "precedence": "vault",
                "mediated": False,
            }
        ],
        "integration_bindings": [
            {
                "integration": "demo",
                "project_id": "p",
                "catalog_id": "c",
                "credential_binding": "demo",
            }
        ],
    }
    monkeypatch.setenv("DEMO_TOKEN", "host-value")
    assert integration_environment(config, "p", ["c"], "codex") == {
        "DEMO_TOKEN": "fake-binding-value"
    }
    assert integration_environment(config, "other", ["c"], "codex") == {}
    assert integration_environment(config, "p", ["other"], "codex") == {}
    config["integrations"][0]["precedence"] = "environment"
    assert integration_environment(config, "p", ["c"], "codex") == {"DEMO_TOKEN": "host-value"}
    config["integrations"][0]["mediated"] = True
    assert integration_environment(config, "p", ["c"], "codex") == {}


@pytest.mark.parametrize(
    "name", ["HARNESS_SESSION", "TAIL_HARNESS_API_KEY", "LD_PRELOAD", "PATH", "PYTHONPATH"]
)
def test_contract_rejects_authority_and_loader_environment(name):
    from agent_service.integrations import validate_integration

    with pytest.raises(APIError):
        validate_integration(
            {
                "integration": "demo",
                "consumers": ["codex"],
                "environment": {name: "token"},
                "precedence": "vault",
                "mediated": False,
            }
        )


def test_effect_scope_checks_project_and_current_catalog():
    config = configure_effects({"projects": {"p": {}, "q": {}}, "catalogs": []})
    config["integration_bindings"] = [
        {"integration": "synthetic", "project_id": "p", "credential_binding": "project-binding"}
    ]
    assert integration_contract(config, "synthetic", "p")["credential_binding"] == "project-binding"
    with pytest.raises(APIError, match="effect_integration_scope_denied"):
        integration_contract(config, "synthetic", "q")
    config["integration_bindings"][0]["catalog_id"] = "missing"
    with pytest.raises(APIError, match="effect_integration_scope_denied"):
        integration_contract(config, "synthetic", "p")


def test_runtime_dispatch_injects_and_redacts_result(tmp_path):
    from types import SimpleNamespace

    from test_execution_modes import service

    from adapters.shared.process import child_environment

    async def scenario():
        instance, _ = service(tmp_path)
        instance.vault.set("demo", {"token": "fake-runtime-value"})
        instance.config.update(
            integrations=[
                {
                    "integration": "demo",
                    "consumers": ["claude"],
                    "environment": {"DEMO_TOKEN": "token"},
                    "precedence": "vault",
                    "mediated": False,
                }
            ],
            integration_bindings=[
                {"integration": "demo", "project_id": "p", "credential_binding": "demo"}
            ],
        )
        instance.conversation_repository.insert(
            "job", "p", "a", "running", 0, "{}", None, None, "job"
        )

        async def execute(plan, capability):
            assert child_environment()["DEMO_TOKEN"] == "fake-runtime-value"
            instance.event("job", "answer_delta", {"text": "fake-runtime-value"})
            return {"answer": "fake-runtime-value"}

        instance._run_transport_inference = execute
        plan = SimpleNamespace(
            row={"id": "job", "project": "p"},
            data={},
            backend="claude",
            execution_mode="native",
            prompt="hello",
        )
        assert await instance._run_inference(plan) == {"answer": "[redacted]"}
        assert "DEMO_TOKEN" not in child_environment()
        assert "fake-runtime-value" not in str(
            [dict(row) for row in instance.message_repository.all_events("job")]
        )
        plan.execution_mode = "scoped"
        with pytest.raises(APIError, match="integration_environment_unsupported"):
            await instance._run_inference(plan)
        instance.db.close()

    asyncio.run(scenario())


def test_reload_revokes_binding_scope_and_refreshes_credential_path(tmp_path):
    import copy

    from test_execution_modes import service

    from agent_service.secret_vault import SecretVault

    async def scenario():
        instance, _ = service(tmp_path)
        instance.config["services"]["codex"]["models"] = ["fixture"]
        instance.config["projects"]["q"] = dict(instance.config["projects"]["p"])
        instance.config["clients"]["a"]["projects"].append("q")
        instance.config["services"]["codex"]["projects"].append("q")
        instance.config["integration_bindings"] = [
            {"integration": "demo", "project_id": "p", "credential_binding": "demo"}
        ]
        for job, project in [("affected", "p"), ("unrelated", "q")]:
            instance.conversation_repository.insert(
                job,
                project,
                "a",
                "running",
                0,
                json.dumps({"backend": "codex", "model": "fixture"}),
                None,
                None,
                job,
            )
        first = asyncio.create_task(asyncio.sleep(30))
        second = asyncio.create_task(asyncio.sleep(30))
        instance.job_tasks.update(affected=first, unrelated=second)
        candidate = copy.deepcopy(instance.config)
        candidate["integration_bindings"] = []
        await instance.apply_runtime_config(candidate)
        await asyncio.sleep(0)
        assert first.cancelled()
        assert not second.done()
        second.cancel()
        await asyncio.gather(first, second, return_exceptions=True)
        path = tmp_path / "new-vault"
        SecretVault(path).set("demo", {"email": "fake@example.invalid", "token": "fake-new-value"})
        candidate = copy.deepcopy(instance.config)
        candidate["secret_vault_path"] = str(path)
        await instance.apply_runtime_config(candidate)
        assert instance.effects.credentials.get("demo")["token"] == "fake-new-value"
        instance.db.close()

    asyncio.run(scenario())
