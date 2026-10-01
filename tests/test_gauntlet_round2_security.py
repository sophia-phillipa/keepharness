"""Round-two boundaries for authority, credential aliases and destination identity."""

import asyncio
import json
import logging
import os
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_approval_sessions_lifecycle import browser, enroll
from test_effect_executor import prepared, request

from agent_service.approval_sessions import revoke_sessions
from agent_service.effect_transport import scoped_enforcement
from agent_service.log_config import RedactingFilter
from agent_service.persistence.db import migrate
from agent_service.secret_vault import SecretVault


@pytest.mark.parametrize("store", ["default", "generic-default", "effect_credentials_path", "secret_vault_path"])
@pytest.mark.parametrize("copied", [False, True])
def test_linked_private_store_cannot_claim_mediation(tmp_path, store, copied):
    root = tmp_path / "state"
    root.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    path = root / ("harness.effect_credentials.json" if store == "default" else "harness.secrets.json") if store in ("default", "generic-default") else tmp_path / (store + ".json")
    vault = SecretVault(path)
    vault.set("fixture", {"token": "round-two-synthetic-secret"})
    alias = project / "alias.json"
    os.link(path, alias)
    service = SimpleNamespace(root=root, config={} if store in ("default", "generic-default") else {store: str(path)})
    command = ["bwrap"] if copied else ["bwrap", "--ro-bind", str(project), "/source"]
    # A same-owner alias really has the bytes; the classification must not promise isolation.
    assert "round-two-synthetic-secret" in alias.read_text()
    assert scoped_enforcement(service, command, copied_paths=[alias] if copied else []) == "unenforced"


@pytest.mark.parametrize("revocation", ["owner", "logout"])
def test_reconcile_revalidates_after_delayed_body(make_harness_config, revocation):
    async def scenario():
        app, effect = await prepared(make_harness_config)
        service = app.state.service
        service.effects._status(effect["effect_id"], "unknown")
        service.effects.driver.reconcile = AsyncMock(return_value=("done", {"id": "fixture"}))
        token = enroll(app)
        reading, release = asyncio.Event(), asyncio.Event()

        async def delayed():
            reading.set()
            await release.wait()
            yield b'{"decision":"check"}'

        try:
            async with browser(app, token) as client:
                task = asyncio.create_task(client.post("/v1/effects/" + effect["effect_id"] + "/reconcile", content=delayed()))
                await asyncio.wait_for(reading.wait(), 2)
                if revocation == "owner":
                    revoke_sessions(service.config, "local")
                else:
                    assert (await client.post("/v1/logout", headers={"Origin": str(client.base_url).rstrip("/")})).status_code == 200
                release.set()
                response = await task
                assert response.status_code == 403
                service.effects.driver.reconcile.assert_not_awaited()
                assert service.effects.get(effect["effect_id"])["status"] == "unknown"
        finally:
            await service.effects.close()
            service.db.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("alias", [False, True, "slash", "default-port"])
def test_publication_identity_uses_effective_endpoint(make_harness_config, alias):
    async def scenario():
        app, first = await prepared(make_harness_config)
        service = app.state.service
        original = service.config["effect_integrations"][0]
        endpoint = original["endpoint"] if alias else "http://127.0.0.1:8"
        if alias == "slash":
            endpoint += "/"
        if alias == "default-port":
            original["endpoint"] = "https://EXAMPLE.invalid:443/"
            service.db.execute("UPDATE effects SET status='invalidated'")
            first = await service.effects.prepare("job", request())
            endpoint = "https://example.invalid"
        service.config["effect_integrations"].append({**original, "integration": "second", "endpoint": endpoint})
        second = await service.effects.prepare("job", {**request(), "integration": "second"})
        service.effects.driver.create = AsyncMock(return_value=("done", {"id": "fixture"}))
        try:
            for effect in (first, second):
                service.gates.repository.resolve(effect["gate_id"], "approve", "local", time.time())
                result = await service.effects.execute(effect["effect_id"])
            assert result["status"] == ("invalidated" if alias else "done")
            assert service.effects.driver.create.await_count == (1 if alias else 2)
        finally:
            await service.effects.close()
            service.db.close()

    asyncio.run(scenario())


def test_legacy_binding_migration_uses_retained_contract(make_harness_config):
    async def scenario():
        app, effect = await prepared(make_harness_config)
        service = app.state.service
        try:
            row = service.db.execute("SELECT binding,contract FROM effects").fetchone()
            legacy = json.loads(row["binding"])
            legacy.pop("endpoint", None)
            with service.db:
                service.db.execute("UPDATE effects SET binding=?,status='done'", (json.dumps(legacy),))
                service.db.execute("UPDATE schema_version SET version=5")
            migrate(service.db)
            binding = json.loads(service.db.execute("SELECT binding FROM effects").fetchone()[0])
            assert binding["endpoint"] == json.loads(row["contract"])["endpoint"]
            assert service.effects.get(effect["effect_id"])["status"] == "done"
        finally:
            await service.effects.close()
            service.db.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("text", ["Cookie: harness_session=synthetic-session; ordinary=keep", "/approve-device?nonce=synthetic-nonce&keep=yes", '{"harness_session":"synthetic-session", "nonce":"synthetic-nonce"}'])
def test_authority_fields_redacted_in_logs_and_events(make_harness_config, text):
    async def scenario():
        app, _ = await prepared(make_harness_config)
        service = app.state.service
        try:
            record = logging.LogRecord("fixture", logging.ERROR, "", 1, text, (), None)
            RedactingFilter().filter(record)
            assert "synthetic-session" not in record.getMessage()
            assert "synthetic-nonce" not in record.getMessage()
            service.event("job", "tool_end", {"output": text})
            output = json.dumps([dict(row) for row in service.message_repository.all_events("job")])
            assert "synthetic-session" not in output
            assert "synthetic-nonce" not in output
        finally:
            await service.effects.close()
            service.db.close()

    asyncio.run(scenario())
