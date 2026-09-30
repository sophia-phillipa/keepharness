"""Publication requires one immutable human approval, independent of worker access."""

import asyncio
import json

import pytest
from test_approval_authority import ORIGIN, client_for, pending_approval

from agent_service.app import create_app
from agent_service.approval_sessions import issue_enrollment


def configure_effects(config, endpoint="http://127.0.0.1:9"):
    config["effect_integrations"] = [
        dict(
            integration="synthetic",
            operation="jira.create_issue",
            destination_allowlist=["TEST"],
            mediated=True,
            credential_binding="synthetic",
            endpoint=endpoint,
        )
    ]
    return config


def request():
    return dict(
        integration="synthetic",
        operation="jira.create_issue",
        destination="TEST",
        arguments={},
        artifact={
            "fields": {
                "project": {"key": "TEST"},
                "summary": "Synthetic publication",
                "issuetype": {"name": "Task"},
            }
        },
    )


async def enroll(app, client):
    nonce = issue_enrollment(app.state.service.config, "local")
    await client.post("/approve-device?nonce=" + nonce, headers={"Origin": ORIGIN})


async def prepared(make_harness_config):
    app = create_app(configure_effects(make_harness_config()))
    pending_approval(app, "local")
    app.state.service.effects.credentials.set(
        "synthetic", {"email": "fixture@example.invalid", "token": "synthetic-secret"}
    )
    result = await app.state.service.effects.prepare("job", request())
    return app, result


def test_prepare_returns_immutable_digest_bound_gate(make_harness_config):
    async def scenario():
        app, effect = await prepared(make_harness_config)
        service = app.state.service
        assert effect["status"] == "prepared"
        assert len(effect["artifact_digest"]) == len(effect["arguments_digest"]) == 64
        gate = json.loads(service.gates.repository.get(effect["gate_id"])["spec"])
        assert gate["artifact_digest"] == effect["artifact_digest"]
        assert gate["operation"] == "jira.create_issue"
        assert gate["enforcement"] == "unenforced"
        await service.effects.close()
        service.db.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("access", ["full", "bypassPermissions"])
def test_worker_cannot_approve_and_access_never_auto_approves(make_harness_config, access):
    async def scenario():
        app, effect = await prepared(make_harness_config)
        async with client_for(app) as client:
            response = await client.post(
                "/v1/approvals/" + effect["gate_id"],
                json={"choice": "approve", "access_mode": access},
            )
            assert response.status_code == 403
            assert app.state.service.effects.get(effect["effect_id"])["status"] == "prepared"
        await app.state.service.effects.close()
        app.state.service.db.close()

    asyncio.run(scenario())


def test_changed_artifact_rejected_after_human_approval(make_harness_config):
    async def scenario():
        app, effect = await prepared(make_harness_config)
        service = app.state.service
        changed = request()["artifact"]
        changed["fields"]["summary"] = "Changed but still valid publication"
        with service.db:
            service.db.execute(
                "UPDATE effects SET artifact=? WHERE effect_id=?",
                (json.dumps(changed), effect["effect_id"]),
            )
        async with client_for(app) as client:
            await enroll(app, client)
            reply = await client.post(
                "/v1/approvals/" + effect["gate_id"], json={"choice": "approve"}
            )
            assert reply.status_code == 200
            await service.effects.tasks[effect["effect_id"]]
            assert service.effects.get(effect["effect_id"])["status"] == "invalidated"
            assert not any(
                e["type"] == "effect_intent" for e in service.message_repository.all_events("job")
            )
        await service.effects.close()
        service.db.close()

    asyncio.run(scenario())


def test_publish_gate_remains_actionable_after_chat_turn_finishes(make_harness_config):
    async def scenario():
        app, effect = await prepared(make_harness_config)
        service = app.state.service
        service.finish("job", "done", {"answer": "Publication prepared"})
        async with client_for(app) as client:
            reply = await client.get("/v1/activity")
            assert any(
                item["effect_id"] == effect["effect_id"] for item in reply.json()["needs_you"]
            )
        await service.effects.close()
        service.db.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "mutation", ["arguments", "operation", "destination", "contract", "cancelled"]
)
def test_stale_authorization_never_dispatches(make_harness_config, mutation):
    async def scenario():
        app, effect = await prepared(make_harness_config)
        service = app.state.service
        if mutation == "contract":
            service.config["effect_integrations"][0]["endpoint"] = "http://127.0.0.1:8"
        elif mutation == "cancelled":
            service.finish("job", "cancelled", {})
        else:
            row = service.db.execute(
                "SELECT request FROM effects WHERE effect_id=?", (effect["effect_id"],)
            ).fetchone()
            altered = json.loads(row[0])
            altered[mutation] = {"changed": True} if mutation == "arguments" else "changed"
            with service.db:
                service.db.execute(
                    "UPDATE effects SET request=? WHERE effect_id=?",
                    (json.dumps(altered), effect["effect_id"]),
                )
        async with client_for(app) as client:
            await enroll(app, client)
            assert (
                await client.post("/v1/approvals/" + effect["gate_id"], json={"choice": "approve"})
            ).status_code == 200
        await service.effects.tasks[effect["effect_id"]]
        assert service.effects.get(effect["effect_id"])["status"] == "invalidated"
        assert not any(
            event["type"] == "effect_intent"
            for event in service.message_repository.all_events("job")
        )
        await service.effects.close()
        service.db.close()

    asyncio.run(scenario())


def test_publish_gate_timeout_denies_without_dispatch(make_harness_config):
    async def scenario():
        app = create_app(configure_effects(make_harness_config(approval_timeout_seconds=0.01)))
        pending_approval(app, "local")
        service = app.state.service
        service.effects.credentials.set(
            "synthetic", {"email": "fixture@example.invalid", "token": "fixture-secret"}
        )
        effect = await service.effects.prepare("job", request())
        await service.effects.tasks[effect["effect_id"]]
        assert service.effects.get(effect["effect_id"])["status"] == "denied"
        assert service.gates.repository.get(effect["gate_id"])["state"] == "expired"
        assert not any(
            event["type"] == "effect_intent"
            for event in service.message_repository.all_events("job")
        )
        await service.effects.close()
        service.db.close()

    asyncio.run(scenario())


def test_restart_invalidates_prepared_effect_and_requires_new_gate(make_harness_config):
    async def scenario():
        app, effect = await prepared(make_harness_config)
        service = app.state.service
        restarted = create_app(service.config).state.service
        assert restarted.effects.get(effect["effect_id"])["status"] == "invalidated"
        assert restarted.gates.repository.get(effect["gate_id"])["state"] == "invalidated"
        assert not restarted.effects.tasks
        await service.effects.close()
        await restarted.effects.close()
        service.db.close()
        restarted.db.close()

    asyncio.run(scenario())
