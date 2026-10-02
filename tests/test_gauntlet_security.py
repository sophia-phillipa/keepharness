"""Round-one regressions for private data and delayed human decisions."""

import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_approval_authority import pending_approval
from test_approval_sessions_lifecycle import browser, enroll
from test_approval_sessions_lifecycle import session_app as session_app
from test_effect_executor import prepared, request

from agent_service.approval_sessions import revoke_sessions
from agent_service.effect_transport import scoped_enforcement
from agent_service.secret_vault import SecretVault, redact_secrets


def test_redacts_literal_credentials_in_nested_object_keys(tmp_path):
    vault = SecretVault(tmp_path / "vault.json")
    vault.set("fixture", {"token": "synthetic-key-secret"})
    output = {
        "nested": [{"prefix-synthetic-key-secret": "synthetic-key-secret"}],
        "tuple": ("synthetic-key-secret",),
    }
    assert redact_secrets(output) == {
        "nested": [{"prefix-[redacted]": "[redacted]"}],
        "tuple": ("[redacted]",),
    }


def test_scoped_enforcement_checks_both_credential_stores(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    service = SimpleNamespace(
        root=tmp_path / "state",
        config={
            "effect_credentials_path": str(tmp_path / "effects.json"),
            "secret_vault_path": str(project / "vault.json"),
        },
    )
    assert (
        scoped_enforcement(service, ["bwrap", "--ro-bind", str(project), "/sources/project"])
        == "unenforced"
    )
    assert (
        scoped_enforcement(service, ["bwrap", "--ro-bind", str(tmp_path / "safe"), "/source"])
        == "mediated"
    )


@pytest.mark.parametrize("kind", ["native", "gate"])
@pytest.mark.parametrize("revocation", ["owner", "logout"])
def test_approval_revalidates_session_after_body_read(session_app, kind, revocation):
    async def scenario():
        service = session_app.state.service
        pending_approval(session_app, "local")
        token = enroll(session_app)
        task = None
        if kind == "gate":
            task = asyncio.create_task(
                service.gates.ask(
                    "job",
                    {"question": "Continue?", "options": [{"id": "yes", "label": "Yes"}]},
                    lambda *_: None,
                )
            )
            await asyncio.sleep(0)
            approval_id = service.db.execute("SELECT gate_id FROM gates").fetchone()[0]
        else:
            approval_id = next(iter(service.approvals))
        pending = service.approvals[approval_id][1]
        reading, release = asyncio.Event(), asyncio.Event()

        async def delayed_body():
            reading.set()
            await release.wait()
            yield json.dumps({"choice": "yes", "approved": True}).encode()

        try:
            async with browser(session_app, token) as client:
                inflight = asyncio.create_task(
                    client.post("/v1/approvals/" + approval_id, content=delayed_body())
                )
                await asyncio.wait_for(reading.wait(), 2)
                if revocation == "owner":
                    revoke_sessions(service.config, "local")
                else:
                    response = await client.post(
                        "/v1/logout", headers={"Origin": str(client.base_url).rstrip("/")}
                    )
                    assert response.status_code == 200
                release.set()
                response = await asyncio.wait_for(inflight, 2)
                assert response.status_code == 403
                assert not pending.done()
        finally:
            if task:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


@pytest.mark.parametrize("status", ["unknown", "executing", "done"])
def test_prepared_equivalent_effect_cannot_dispatch_after_conflict(make_harness_config, status):
    async def scenario():
        app, first = await prepared(make_harness_config)
        service = app.state.service
        try:
            second = await service.effects.prepare("job", request())
            service.effects._status(first["effect_id"], status)
            service.gates.repository.resolve(second["gate_id"], "approve", "local", time.time())
            service.effects.driver.create = AsyncMock(return_value=("done", {"id": "fixture"}))
            result = await service.effects.execute(second["effect_id"])
            assert result["status"] == "invalidated"
            service.effects.driver.create.assert_not_awaited()
        finally:
            await service.effects.close()
            service.db.close()

    asyncio.run(scenario())
