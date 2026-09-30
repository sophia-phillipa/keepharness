"""Active deadlines exclude human waits, while elapsed time never pauses."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_execution_modes import service


def test_human_wait_over_ten_minutes_preserves_active_budget():
    from agent_service.services.budgets import RuntimeBudget

    async def scenario():
        now = [0.0]
        budget = RuntimeBudget(clock=lambda: now[0])
        async with budget.limit(600):
            now[0] = 10
            with budget.human_wait():
                now[0] = 1210
                await asyncio.sleep(0)
                assert budget.active_seconds == 10
                assert budget.elapsed_seconds == 1210
            assert budget.active_seconds == 10
            assert budget.human_wait_seconds == 1200
            now[0] = 1220
            assert budget.active_seconds == 20

    asyncio.run(scenario())


def test_nested_wait_and_silent_tool_keep_distinct_budgets():
    from agent_service.services.budgets import RuntimeBudget

    async def scenario():
        now = [0.0]
        budget = RuntimeBudget(clock=lambda: now[0])
        async with budget.limit(600):
            with budget.human_wait():
                now[0] = 700
                with budget.human_wait():
                    now[0] = 800
                assert budget.active_seconds == 0
            # Silent tool runtime is active runtime, even without provider output.
            now[0] = 950
            assert budget.active_seconds == 150
            assert budget.elapsed_seconds == 950
            assert budget.human_wait_seconds == 800

    asyncio.run(scenario())


def test_active_deadline_still_expires():
    from agent_service.services.budgets import RuntimeBudget

    async def scenario():
        budget = RuntimeBudget()
        with pytest.raises(TimeoutError):
            async with budget.limit(0.01):
                await asyncio.Future()

    asyncio.run(scenario())


def test_cancel_while_waiting_clears_approval_and_budget(tmp_path):
    async def scenario():
        instance, identity = service(tmp_path)
        job = instance.submit(
            identity, dict(project_id="p", backend="codex", model="gpt-6-astra", prompt="wait")
        )["job_id"]
        row = instance.job(identity, job)
        plan = SimpleNamespace(row=row, data={"model": "gpt-6-astra"}, backend="codex")
        approve = instance._approval_handler(plan, lambda *_: None, {}, {})
        task = asyncio.create_task(approve("command", {"command": "fixture"}))
        instance.job_tasks[job] = task
        instance.active = job
        instance.task = task
        instance.conversation_repository.set_running(job)
        await asyncio.sleep(0)
        assert instance.approvals
        instance.cancel(identity, job)
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not instance.approvals
        instance.db.close()

    asyncio.run(scenario())


def test_queue_deadline_is_paused_until_human_decides(tmp_path):
    async def scenario():
        instance, identity = service(tmp_path)
        instance.config.update(active_timeout_seconds=0.03, approval_timeout_seconds=1)
        job = instance.submit(
            identity,
            dict(
                project_id="p",
                backend="codex",
                model="gpt-6-astra",
                execution_mode="scoped",
                prompt="wait",
            ),
        )["job_id"]

        async def execute(row):
            plan = SimpleNamespace(row=row, data={"model": "gpt-6-astra"}, backend="codex")
            decision = await instance._approval_handler(plan, instance.event_for_test, {}, {})(
                "command", {}
            )
            return {"answer": "allowed" if decision["approved"] else "denied"}

        instance.event_for_test = lambda kind, data: instance.event(job, kind, data)
        instance.execute = execute
        instance.quota = AsyncMock(return_value={})
        worker = asyncio.create_task(instance.worker())
        try:
            for _ in range(50):
                if instance.approvals:
                    break
                await asyncio.sleep(0.001)
            assert instance.approvals
            await asyncio.sleep(0.06)
            assert instance.conversation_repository.state(job)[0] == "running"
            next(iter(instance.approvals.values()))[1].set_result({"approved": True})
            for _ in range(50):
                if instance.conversation_repository.state(job)[0] == "completed":
                    break
                await asyncio.sleep(0.001)
            result = json.loads(instance.conversation_repository.get(job)["result"])
            assert result["answer"] == "allowed"
            assert result["human_wait_seconds"] >= 0.06
            assert result["active_seconds"] < 0.03
            assert result["elapsed_seconds"] >= 0.06
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            instance.db.close()

    asyncio.run(scenario())


def test_invalid_active_timeout_cannot_start_an_orphan_execution(tmp_path):
    async def scenario():
        instance, identity = service(tmp_path)
        instance.config["active_timeout_seconds"] = 0
        job = instance.submit(
            identity,
            dict(
                project_id="p",
                backend="codex",
                model="gpt-6-astra",
                execution_mode="scoped",
                prompt="wait",
            ),
        )["job_id"]
        instance.execute = AsyncMock()
        instance.quota = AsyncMock(return_value={})
        worker = asyncio.create_task(instance.worker())
        try:
            for _ in range(50):
                if instance.conversation_repository.state(job)[0] == "failed":
                    break
                await asyncio.sleep(0.001)
            assert instance.conversation_repository.state(job)[0] == "failed"
            instance.execute.assert_not_called()
            assert not instance.job_tasks
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            instance.db.close()

    asyncio.run(scenario())


def test_approval_pauses_only_its_own_job_budget(tmp_path):
    from test_dispatch_capacity import insert

    from agent_service.services.budgets import RuntimeBudget

    async def scenario():
        instance, identity = service(tmp_path)
        insert(instance, "parent", "running")
        insert(instance, "child", "running", execution_parent_id="parent")
        instance.runtime_budgets.update(parent=RuntimeBudget(), child=RuntimeBudget())
        plan = SimpleNamespace(
            row=instance.job(identity, "child"), data={"model": "gpt-6-astra"}, backend="codex"
        )
        approve = instance._approval_handler(plan, lambda *_: None, {}, {})
        task = asyncio.create_task(approve("command", {}))
        try:
            await asyncio.sleep(0)
            assert instance.runtime_budgets["child"].wait_depth == 1
            assert instance.runtime_budgets["parent"].wait_depth == 0
            next(iter(instance.approvals.values()))[1].set_result({"approved": True})
            await task
            assert instance.runtime_budgets["child"].wait_depth == 0
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            instance.db.close()

    asyncio.run(scenario())
