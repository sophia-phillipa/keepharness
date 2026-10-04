"""D14: a run waiting for a person frees its provider lane and inference slot, then takes them back."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from test_execution_modes import service
from test_queue_lanes import enqueue

from agent_service.services import capacity


async def settle(rounds=30):
    for _ in range(rounds):
        await asyncio.sleep(0)


def parking_service(tmp_path, backend="claude"):
    """One run at a time on ``backend``; ``first`` waits for an approval inside its inference."""
    instance, _ = service(tmp_path)
    instance.config["projects"] = {key: {"root": str(tmp_path / key)} for key in ("p", "q", "r")}
    instance.config["services"].setdefault(backend, {})["max_concurrent"] = 1
    instance.quota = AsyncMock(return_value={})
    instance._prepare_inference = AsyncMock(
        side_effect=lambda row, data: SimpleNamespace(row=row, data=data, backend=data["backend"])
    )
    instance._finalize_inference = lambda plan, result: result
    approved = asyncio.Event()
    resumed = []
    gates = {}

    async def run_inference(plan):
        job = plan.row["id"]
        if job == "first":
            async with capacity.parked(instance, job):
                await approved.wait()
            resumed.append(job)
        elif job in gates:
            await gates[job].wait()
        return {"answer": job}

    instance._run_inference = run_inference

    async def execute(row):
        return await instance.infer(row, json.loads(row["payload"]))

    instance.execute = execute
    return SimpleNamespace(
        instance=instance, approved=approved, resumed=resumed, gates=gates, backend=backend
    )


def state(instance, job):
    return instance.conversation_repository.state(job)[0]


def test_a_run_waiting_for_approval_frees_both_counters(tmp_path):
    async def scenario():
        world = parking_service(tmp_path)
        instance = world.instance
        enqueue(instance, "first", "p", "claude")
        enqueue(instance, "second", "q", "claude")
        worker = asyncio.create_task(instance.worker())
        try:
            await settle()
            # The second run got the lane and the slot while the first waits for a person.
            assert state(instance, "second") == "completed"
            assert state(instance, "first") == "running"
            assert instance.provider_lanes["claude"] == 0
            assert instance.provider_inflight["claude"] == 0
            world.approved.set()
            await settle()
            assert world.resumed == ["first"]
            assert state(instance, "first") == "completed"
            assert instance.provider_lanes["claude"] == 0
            assert instance.provider_inflight["claude"] == 0
            assert not instance.parked_capacity and not instance.job_lanes
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            instance.db.close()

    asyncio.run(scenario())


def test_after_the_decision_the_run_waits_for_room_ahead_of_new_runs(tmp_path):
    async def scenario():
        world = parking_service(tmp_path)
        instance = world.instance
        world.gates["second"] = asyncio.Event()
        enqueue(instance, "first", "p", "claude")
        enqueue(instance, "second", "q", "claude")
        worker = asyncio.create_task(instance.worker())
        try:
            await settle()
            assert state(instance, "second") == "running"
            world.approved.set()
            await settle()
            # Approved, but the capacity is taken: the run takes it back only when it frees.
            assert world.resumed == []
            assert instance.lane_resumers["claude"] == 1
            enqueue(instance, "third", "r", "claude")
            instance.wake.set()
            await settle()
            assert state(instance, "third") == "queued"
            world.gates["second"].set()
            await settle()
            assert world.resumed == ["first"]
            assert [state(instance, job) for job in ("first", "second", "third")] == [
                "completed",
                "completed",
                "completed",
            ]
            assert instance.provider_lanes["claude"] == 0
            assert instance.provider_inflight["claude"] == 0
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            instance.db.close()

    asyncio.run(scenario())


def test_cancelling_a_parked_run_leaves_the_counters_balanced(tmp_path):
    async def scenario():
        world = parking_service(tmp_path)
        instance = world.instance
        world.gates["second"] = asyncio.Event()
        enqueue(instance, "first", "p", "claude")
        enqueue(instance, "second", "q", "claude")
        worker = asyncio.create_task(instance.worker())
        try:
            await settle()
            instance.job_tasks["first"].cancel()
            await settle()
            assert state(instance, "first") == "cancelled"
            assert instance.provider_lanes["claude"] == 1  # only the second run
            assert instance.provider_inflight["claude"] == 1
            world.gates["second"].set()
            await settle()
            assert instance.provider_lanes["claude"] == 0
            assert instance.provider_inflight["claude"] == 0
            assert not instance.parked_capacity
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            instance.db.close()

    asyncio.run(scenario())


def test_a_local_model_keeps_its_capacity_while_it_waits(tmp_path):
    async def scenario():
        world = parking_service(tmp_path, backend="local")
        instance = world.instance
        enqueue(instance, "first", "p", "local")
        enqueue(instance, "second", "q", "local")
        worker = asyncio.create_task(instance.worker())
        try:
            await settle()
            assert state(instance, "second") == "queued"
            assert instance.provider_inflight["local"] == 1
            world.approved.set()
            await settle()
            assert [state(instance, job) for job in ("first", "second")] == [
                "completed",
                "completed",
            ]
            assert instance.provider_lanes["local"] == 0
            assert instance.provider_inflight["local"] == 0
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            instance.db.close()

    asyncio.run(scenario())


def test_cloud_providers_run_two_at_once_by_default():
    config = {"services": {"claude": {}, "local": {}, "codex": {"max_concurrent": 3}}}
    assert [
        capacity.maximum(config, backend)
        for backend in ("claude", "deepseek", "gemini", "codex", "local", "maestro")
    ] == [2, 2, 2, 3, 1, 1]
