"""Option gates require enrolled humans and retain a single durable resolution."""

import asyncio
import json
from types import SimpleNamespace

from test_approval_authority import ORIGIN, client_for, pending_approval

from agent_service.app import create_app
from agent_service.approval_sessions import issue_enrollment


def gate_request(**overrides):
    return dict(
        question="Choose a color?",
        options=[{"id": "blue", "label": "Blue"}, {"id": "red", "label": "Red"}],
        **overrides,
    )


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
