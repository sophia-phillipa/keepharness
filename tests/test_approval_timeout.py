"""Expired approval is denied, audited and never remembered."""

import asyncio
from types import SimpleNamespace

from test_execution_modes import service


def test_pending_approval_expires_and_denies(tmp_path):
    async def scenario():
        instance, identity = service(tmp_path)
        instance.config["approval_timeout_seconds"] = 0.01
        job = instance.submit(
            identity, dict(project_id="p", backend="codex", model="gpt-6-astra", prompt="wait")
        )["job_id"]
        events = []
        plan = SimpleNamespace(
            row=instance.job(identity, job), data={"model": "gpt-6-astra"}, backend="codex"
        )
        approve = instance._approval_handler(
            plan, lambda kind, data: events.append((kind, data)), {}, {}
        )
        reply = await asyncio.wait_for(approve("command", {"command": "fixture"}), 0.2)
        assert reply == {"approved": False, "reason": "approval_expired"}
        assert not instance.approvals
        required = next(data for kind, data in events if kind == "approval_required")
        assert required["expires_at"] > 0
        assert [kind for kind, _ in events] == ["approval_required", "approval_expired"]
        instance.db.close()

    asyncio.run(scenario())
