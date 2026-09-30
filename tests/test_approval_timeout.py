"""Expired approval is denied, audited and never remembered."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
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


@pytest.mark.parametrize("maximum", [None, 3])
@pytest.mark.parametrize("separate_callback_task", [False, True])
def test_consecutive_expirations_cancel_job_and_release_queue(
    tmp_path, maximum, separate_callback_task
):
    async def scenario():
        instance, identity = service(tmp_path)
        instance.config["approval_timeout_seconds"] = 0.001
        if maximum is not None:
            instance.config["approval_max_consecutive_expirations"] = maximum
        limit = maximum or 2
        jobs = [
            instance.submit(
                identity,
                dict(project_id="p", backend="codex", model="gpt-6-astra", prompt=prompt),
            )["job_id"]
            for prompt in ("wait", "next")
        ]
        calls = []

        async def execute(row):
            if row["id"] == jobs[1]:
                return {"answer": "next job completed"}
            # Each inference stage may create its own approval callback for this job.
            for _ in range(limit + 1):
                plan = SimpleNamespace(row=row, data={"model": "gpt-6-astra"}, backend="codex")
                approve = instance._approval_handler(
                    plan, lambda kind, data: instance.event(row["id"], kind, data), {}, {}
                )
                calls.append("approval")
                decision = approve("command", {"command": "fixture"})
                if separate_callback_task:
                    await asyncio.shield(decision)
                else:
                    await decision
            return {"answer": "must not complete"}

        instance.execute = execute
        instance.quota = AsyncMock(return_value={})
        worker = asyncio.create_task(instance.worker())
        try:
            async with asyncio.timeout(1):
                while instance.conversation_repository.state(jobs[1])[0] != "completed":
                    await asyncio.sleep(0.001)
            row = instance.conversation_repository.get(jobs[0])
            assert row["state"] == "cancelled"
            result = json.loads(row["result"])
            assert result["error"] == "approval_expiration_limit"
            assert result["human_wait_seconds"] > 0
            assert len(calls) == limit
            events = instance.message_repository.events_after(jobs[0], 0)
            assert sum(event["type"] == "approval_expired" for event in events) == limit
            assert sum(event["type"] == "cancelled" for event in events) == 1
            assert not instance.approvals
            assert not instance.approval_expirations
            assert not instance.job_tasks
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            instance.db.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("approved", [True, False])
def test_human_reply_resets_consecutive_expirations(tmp_path, approved):
    async def scenario():
        instance, identity = service(tmp_path)
        instance.config["approval_timeout_seconds"] = 0.01
        job = instance.submit(
            identity, dict(project_id="p", backend="codex", model="gpt-6-astra", prompt="wait")
        )["job_id"]
        plan = SimpleNamespace(
            row=instance.job(identity, job), data={"model": "gpt-6-astra"}, backend="codex"
        )
        approve = instance._approval_handler(plan, lambda *_: None, {}, {})
        try:
            await approve("command", {})
            decision = asyncio.create_task(approve("command", {}))
            await asyncio.sleep(0)
            next(iter(instance.approvals.values()))[1].set_result({"approved": approved})
            assert await decision == {"approved": approved}
            assert await approve("command", {}) == {
                "approved": False, "reason": "approval_expired"
            }
            with pytest.raises(asyncio.CancelledError):
                await approve("command", {})
            assert instance.cancellation_reasons[job] == "approval_expiration_limit"
            assert not instance.approvals
        finally:
            instance.db.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("maximum", [0, -1, True, 1.5, "2"])
def test_invalid_expiration_limit_never_opens_approval(tmp_path, maximum):
    async def scenario():
        instance, identity = service(tmp_path)
        instance.config.update(
            approval_timeout_seconds=0.001, approval_max_consecutive_expirations=maximum
        )
        job = instance.submit(
            identity, dict(project_id="p", backend="codex", model="gpt-6-astra", prompt="wait")
        )["job_id"]
        plan = SimpleNamespace(
            row=instance.job(identity, job), data={"model": "gpt-6-astra"}, backend="codex"
        )
        events = []
        approve = instance._approval_handler(plan, lambda *event: events.append(event), {}, {})
        try:
            with pytest.raises(ValueError, match="invalid_approval_max_consecutive_expirations"):
                await approve("command", {})
            assert not instance.approvals
            assert not events
        finally:
            instance.db.close()

    asyncio.run(scenario())
