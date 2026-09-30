"""Inference capacity and execution ancestry do not create concurrent queue lanes."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_execution_modes import service


def test_dispatch_capacity_covers_planner_steps_and_releases_on_cancel(tmp_path):
    async def scenario():
        instance, _ = service(tmp_path)
        entered = []
        release = asyncio.Event()
        instance._prepare_inference = AsyncMock(
            side_effect=lambda row, data: SimpleNamespace(
                row=row, data=data, backend=data["backend"]
            )
        )
        instance._finalize_inference = lambda plan, result: result

        async def dispatch(plan):
            entered.append(plan.data["_maestro_stage"])
            await release.wait()
            return {"answer": "ok"}

        instance._run_inference = dispatch
        first = asyncio.create_task(
            instance.infer(
                {"id": "planner"}, {"backend": "codex", "model": "m", "_maestro_stage": "plan"}
            )
        )
        second = asyncio.create_task(
            instance.infer(
                {"id": "step"}, {"backend": "codex", "model": "m", "_maestro_stage": "step"}
            )
        )
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert entered == ["plan"]
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        await asyncio.sleep(0)
        assert entered == ["plan", "step"]
        release.set()
        await second
        assert not instance.active_executors
        instance.db.close()

    asyncio.run(scenario())


def insert(instance, job, state="queued", **payload):
    import json

    instance.conversation_repository.insert(
        job, "p", "a", state, 0, json.dumps(payload), None, None, job
    )


def test_execution_child_is_ready_but_conversation_turn_waits(tmp_path):
    instance, _ = service(tmp_path)
    try:
        insert(instance, "parent", "running")
        insert(instance, "execution", parent_job_id="parent", execution_parent_id="parent")
        insert(instance, "turn", parent_job_id="parent")
        assert [row["id"] for row in instance.conversation_repository.ready()] == ["execution"]
        instance.conversation_repository.set_result("parent", "queued", None)
        assert [row["id"] for row in instance.conversation_repository.ready()] == ["parent"]
    finally:
        instance.db.close()


def test_cancel_execution_lineage_preserves_followup_and_other_jobs(tmp_path):
    async def scenario():
        instance, identity = service(tmp_path)
        insert(instance, "parent", "running")
        insert(instance, "child", "running", execution_parent_id="parent")
        insert(instance, "grandchild", execution_parent_id="child")
        insert(instance, "turn", parent_job_id="parent")
        insert(instance, "other")
        parent = asyncio.create_task(asyncio.sleep(60))
        child = asyncio.create_task(asyncio.sleep(60))
        instance.job_tasks.update(parent=parent, child=child)
        await asyncio.sleep(0)
        instance.cancel(identity, "parent")
        results = await asyncio.gather(parent, child, return_exceptions=True)
        assert all(isinstance(result, asyncio.CancelledError) for result in results)
        assert instance.conversation_repository.state("grandchild")[0] == "cancelled"
        assert instance.conversation_repository.state("child")[0] == "cancelled"
        assert instance.conversation_repository.state("turn")[0] == "queued"
        assert instance.conversation_repository.state("other")[0] == "queued"
        instance.db.close()

    asyncio.run(scenario())


def test_public_submission_cannot_forge_execution_ancestry(tmp_path):
    from agent_service.errors import APIError

    instance, identity = service(tmp_path)
    try:
        with pytest.raises(APIError, match="invalid_internal_field"):
            instance.submit(
                identity,
                dict(
                    project_id="p",
                    backend="codex",
                    model="gpt-6-astra",
                    prompt="fake",
                    execution_parent_id="other",
                ),
            )
    finally:
        instance.db.close()


def test_capacity_decrease_applies_to_future_dispatches(tmp_path):
    async def scenario():
        instance, _ = service(tmp_path)
        instance.config["services"]["codex"]["max_concurrent"] = 2
        instance._prepare_inference = AsyncMock(
            side_effect=lambda row, data: SimpleNamespace(row=row, data=data, backend="codex")
        )
        instance._finalize_inference = lambda plan, result: result
        instance._run_inference = AsyncMock(return_value={"answer": "warm"})
        await instance.infer({"id": "warm"}, {"model": "m"})
        instance.config["services"]["codex"]["max_concurrent"] = 1
        entered = []
        release = asyncio.Event()

        async def dispatch(plan):
            entered.append(plan.row["id"])
            await release.wait()
            return {}

        instance._run_inference = dispatch
        tasks = [
            asyncio.create_task(instance.infer({"id": job}, {"model": "m"}))
            for job in ("one", "two")
        ]
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert entered == ["one"]
        release.set()
        await asyncio.gather(*tasks)
        assert entered == ["one", "two"]
        assert instance.provider_inflight["codex"] == 0
        instance.db.close()

    asyncio.run(scenario())
