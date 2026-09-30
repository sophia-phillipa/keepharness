"""Option gates require enrolled humans and retain a single durable resolution."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from test_approval_authority import ORIGIN, client_for, pending_approval

from agent_service.app import create_app
from agent_service.approval_sessions import issue_enrollment


def gate_request(**overrides):
    return dict(
        question="Choose a color?",
        options=[{"id": "blue", "label": "Blue"}, {"id": "red", "label": "Red"}],
        **overrides,
    )


@pytest.mark.parametrize("enrolled", [False, True])
def test_gate_authorizes_human_once_before_resolution(make_harness_config, monkeypatch, enrolled):
    from unittest.mock import Mock

    from agent_service.routes import conversations

    checked = Mock(wraps=conversations.require_approval_session)
    monkeypatch.setattr(conversations, "require_approval_session", checked)

    async def scenario():
        app = create_app(make_harness_config())
        service = app.state.service
        pending_approval(app, "local")
        task = asyncio.create_task(
            service.gates.ask("job", gate_request(), lambda kind, data: service.event("job", kind, data))
        )
        await asyncio.sleep(0)
        gate = service.db.execute("SELECT gate_id FROM gates").fetchone()[0]
        try:
            async with client_for(app) as client:
                if enrolled:
                    nonce = issue_enrollment(service.config, "local")
                    await client.post("/approve-device?nonce=" + nonce, headers={"Origin": ORIGIN})
                response = await client.post("/v1/approvals/" + gate, json={"choice": "blue"})
                assert response.status_code == (200 if enrolled else 403)
                checked.assert_called_once()
                if enrolled:
                    assert (await task)["choice"] == "blue"
                else:
                    assert not task.done()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            service.db.close()

    asyncio.run(scenario())


def test_choice_validation_first_wins_and_audit(make_harness_config):
    async def scenario():
        app = create_app(make_harness_config())
        service = app.state.service
        pending_approval(app, "local")
        task = asyncio.create_task(
            service.gates.ask(
                "job", gate_request(), lambda kind, data: service.event("job", kind, data)
            )
        )
        await asyncio.sleep(0)
        gate = service.db.execute("SELECT gate_id FROM gates").fetchone()[0]
        async with client_for(app) as client:
            assert (
                await client.post("/v1/approvals/" + gate, json={"choice": "blue"})
            ).status_code == 403
            nonce = issue_enrollment(service.config, "local")
            await client.post("/approve-device?nonce=" + nonce, headers={"Origin": ORIGIN})
            bad = await client.post("/v1/approvals/" + gate, json={"choice": "missing"})
            assert bad.status_code == 422
            assert not task.done()
            responses = await asyncio.gather(
                *(
                    client.post("/v1/approvals/" + gate, json={"choice": color})
                    for color in ("blue", "red")
                )
            )
            assert sorted(r.status_code for r in responses) == [200, 409]
            assert (
                next(r for r in responses if r.status_code == 409).json()["code"]
                == "gate_already_resolved"
            )
            result = await task
            assert result["choice"] in ("blue", "red")
            stored = service.db.execute("SELECT * FROM gates WHERE gate_id=?", (gate,)).fetchone()
            assert stored["resolved_by"] == "local"
            assert stored["resolved_at"] > 0
            assert json.loads(stored["choice"]) == result["choice"]
        service.db.close()

    asyncio.run(scenario())


def test_restart_invalidates_pending_gate(make_harness_config):
    async def scenario():
        config = make_harness_config()
        app = create_app(config)
        service = app.state.service
        pending_approval(app, "local")
        task = asyncio.create_task(
            service.gates.ask(
                "job", gate_request(), lambda kind, data: service.event("job", kind, data)
            )
        )
        await asyncio.sleep(0)
        restarted = create_app(config).state.service
        assert restarted.db.execute("SELECT state FROM gates").fetchone()[0] == "invalidated"
        events = restarted.message_repository.events_after("job", 0)
        assert any(row["type"] == "gate_invalidated" for row in events)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        restarted.db.close()
        service.db.close()

    asyncio.run(scenario())


def test_full_mode_does_not_automatically_answer_gate(make_harness_config):
    async def scenario():
        app = create_app(make_harness_config())
        service = app.state.service
        pending_approval(app, "local")
        service.config["approval_timeout_seconds"] = 0.01
        plan = SimpleNamespace(
            row=service.conversation_repository.get("job"),
            data={"model": "haiku", "access_mode": "full"},
            backend="claude",
        )
        callback = service._approval_handler(plan, lambda *_: None, {}, {"unrestricted": True})
        reply = await callback("gate", gate_request())
        assert reply == {"approved": False, "reason": "gate_expired"}
        assert service.db.execute("SELECT state FROM gates").fetchone()[0] == "expired"
        service.db.close()

    asyncio.run(scenario())


def test_committed_resolution_releases_waiter_when_event_delivery_fails(make_harness_config):
    async def scenario():
        app = create_app(make_harness_config())
        service = app.state.service
        pending_approval(app, "local")

        def progress(kind, data):
            if kind == "gate_resolved":
                raise RuntimeError("synthetic_event_failure")

        waiting = asyncio.create_task(service.gates.ask("job", gate_request(), progress))
        await asyncio.sleep(0)
        gate_id = service.db.execute("SELECT gate_id FROM gates").fetchone()[0]
        identity = ("local", service.config["clients"]["local"])
        with pytest.raises(RuntimeError, match="synthetic_event_failure"):
            service.gates.resolve(gate_id, identity, {"choice": "blue"})
        # Audit commit is authoritative even when the optional progress sink failed.
        result = await asyncio.wait_for(waiting, 0.2)
        assert result["approved"] is True and result["choice"] == "blue"
        assert service.gates.repository.get(gate_id)["state"] == "resolved"
        service.db.close()

    asyncio.run(scenario())
