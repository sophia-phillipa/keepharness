"""D15: a scheduled run never waits for a person; it denies the action and goes on, flagged."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from test_execution_modes import service
from test_gate_lifecycle import gate_request


def scheduled_job(instance, identity, **extra):
    return instance.submit(
        identity,
        dict(project_id="p", backend="codex", model="gpt-6-astra", prompt="nightly", **extra),
        schedule={"schedule_id": "f" * 32, "schedule_title": "Nightly"},
    )["job_id"]


@pytest.mark.parametrize("access_mode", ["ask", "read_only"])
@pytest.mark.parametrize("kind", ["command", "mcpServer/elicitation/request", "gate"])
def test_an_unattended_run_denies_at_once_and_goes_on(tmp_path, access_mode, kind):
    async def scenario():
        instance, identity = service(tmp_path)
        job = scheduled_job(instance, identity, access_mode=access_mode)
        row = instance.job(identity, job)
        events = []
        plan = SimpleNamespace(row=row, data=json.loads(row["payload"]), backend="codex")
        approve = instance._approval_handler(
            plan, lambda name, data: events.append((name, data)), {}, {}
        )
        params = gate_request() if kind == "gate" else {"command": "fixture"}
        reply = await asyncio.wait_for(approve(kind, params), 0.5)
        assert reply == {"approved": False, "reason": "unattended"}
        assert events == [("approval_denied", {"scope": "unattended", "kind": kind})]
        assert not instance.approvals
        assert instance.db.execute("SELECT count(*) FROM gates").fetchone()[0] == 0
        instance.db.close()

    asyncio.run(scenario())


def test_a_maestro_gate_in_an_unattended_run_is_denied(tmp_path):
    async def scenario():
        instance, identity = service(tmp_path)
        job = scheduled_job(instance, identity)
        events = []
        reply = await asyncio.wait_for(
            instance.gates.ask(job, gate_request(), lambda name, data: events.append(name)), 0.5
        )
        assert reply == {"approved": False, "reason": "unattended"}
        assert events == ["approval_denied"]
        instance.db.close()

    asyncio.run(scenario())


def test_a_run_started_by_hand_still_asks(tmp_path):
    async def scenario():
        instance, identity = service(tmp_path)
        instance.config["approval_timeout_seconds"] = 0.01
        job = instance.submit(
            identity, dict(project_id="p", backend="codex", model="gpt-6-astra", prompt="hi")
        )["job_id"]
        row = instance.job(identity, job)
        plan = SimpleNamespace(row=row, data=json.loads(row["payload"]), backend="codex")
        events = []
        approve = instance._approval_handler(plan, lambda name, data: events.append(name), {}, {})
        reply = await asyncio.wait_for(approve("command", {"command": "fixture"}), 0.5)
        assert reply == {"approved": False, "reason": "approval_expired"}
        assert events[0] == "approval_required"
        instance.db.close()

    asyncio.run(scenario())
