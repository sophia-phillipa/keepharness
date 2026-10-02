"""Inference capacity and conversation turns retain their dispatch limits."""

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


def test_conversation_turn_waits_even_with_legacy_execution_parent(tmp_path):
    instance, _ = service(tmp_path)
    try:
        insert(instance, "parent", "running")
        insert(instance, "execution", parent_job_id="parent", execution_parent_id="parent")
        insert(instance, "turn", parent_job_id="parent")
        assert not instance.conversation_repository.ready()
        instance.conversation_repository.set_result("parent", "queued", None)
        assert [row["id"] for row in instance.conversation_repository.ready()] == ["parent"]
        instance.conversation_repository.set_result("parent", "completed", None)
        assert [row["id"] for row in instance.conversation_repository.ready()] == [
            "execution",
            "turn",
        ]
    finally:
        instance.db.close()


def test_cancel_job_preserves_other_jobs_and_followup(tmp_path):
    async def scenario():
        instance, identity = service(tmp_path)
        insert(instance, "parent", "running")
        insert(instance, "child", "running", execution_parent_id="parent")
        insert(instance, "turn", parent_job_id="parent")
        insert(instance, "other")
        parent = asyncio.create_task(asyncio.sleep(60))
        child = asyncio.create_task(asyncio.sleep(60))
        instance.job_tasks.update(parent=parent, child=child)
        instance.active = "parent"
        instance.task = parent
        await asyncio.sleep(0)
        try:
            instance.cancel(identity, "parent")
            with pytest.raises(asyncio.CancelledError):
                await parent
            assert not child.done()
            assert instance.conversation_repository.state("child")[0] == "running"
            assert instance.conversation_repository.state("turn")[0] == "queued"
            assert instance.conversation_repository.state("other")[0] == "queued"
        finally:
            child.cancel()
            await asyncio.gather(child, return_exceptions=True)
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
