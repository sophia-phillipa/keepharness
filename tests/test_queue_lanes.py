import asyncio
import json

from test_execution_modes import service


def enqueue(instance, job, project, backend, parent=None, work_item=None):
    instance.conversation_repository.insert(
        job,
        project,
        "a",
        "queued",
        0,
        json.dumps({"backend": backend, **({"parent_job_id": parent} if parent else {})}),
        None,
        None,
        job,
        work_item,
    )


def test_distinct_lanes_run_concurrently_and_same_provider_waits(tmp_path):
    async def scenario():
        instance, _ = service(tmp_path)
        # Pins one run per provider; the default for cloud providers is 2 (D14).
        for backend in ("claude", "gemini"):
            instance.config["services"].setdefault(backend, {})["max_concurrent"] = 1
        instance.config["projects"] = {
            key: {"root": str(tmp_path / key)} for key in ("p", "q", "r")
        }
        entered = []
        release = asyncio.Event()

        async def execute(row):
            entered.append(row["id"])
            await release.wait()
            return {"answer": "done"}

        instance.execute = execute
        enqueue(instance, "one", "p", "claude")
        enqueue(instance, "two", "q", "gemini")
        enqueue(instance, "three", "r", "claude")
        worker = asyncio.create_task(instance.worker())
        for _ in range(10):
            await asyncio.sleep(0)
        assert entered == ["one", "two"]
        assert instance.conversation_repository.state("three")[0] == "queued"
        release.set()
        for _ in range(20):
            await asyncio.sleep(0)
        assert entered == ["one", "two", "three"]
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        assert not instance.write_ownership.leases
        instance.db.close()

    asyncio.run(scenario())


def test_shared_root_blocks_other_provider_and_cancel_releases(tmp_path):
    async def scenario():
        instance, identity = service(tmp_path)
        instance.config["projects"]["p"]["root"] = str(tmp_path / "shared")
        instance.config["projects"]["q"] = dict(instance.config["projects"]["p"])
        entered = []

        async def execute(row):
            entered.append(row["id"])
            await asyncio.Event().wait()

        instance.execute = execute
        enqueue(instance, "one", "p", "claude")
        enqueue(instance, "two", "q", "gemini")
        worker = asyncio.create_task(instance.worker())
        for _ in range(10):
            await asyncio.sleep(0)
        assert entered == ["one"]
        assert any(
            "writable_root" in event["data"]
            for event in instance.message_repository.all_events("two")
        )
        instance.cancel(identity, "one")
        for _ in range(20):
            await asyncio.sleep(0)
        assert entered == ["one", "two"]
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        assert not instance.write_ownership.leases
        instance.db.close()

    asyncio.run(scenario())


def test_fairness_rechecked_between_provider_lane_dispatches(tmp_path):
    async def scenario():
        instance, _ = service(tmp_path)
        # Pins one run per provider; the default for cloud providers is 2 (D14).
        for backend in ("claude", "gemini"):
            instance.config["services"].setdefault(backend, {})["max_concurrent"] = 1
        instance.config["projects"] = {
            key: {"root": str(tmp_path / key)} for key in ("p", "q", "r")
        }
        entered = []

        async def execute(row):
            entered.append(row["id"])
            await asyncio.Event().wait()

        instance.execute = execute
        enqueue(instance, "a-first", "p", "claude")
        enqueue(instance, "a-second", "q", "gemini")
        enqueue(instance, "b-first", "r", "gemini")
        instance.db.execute("UPDATE jobs SET owner='b' WHERE id='b-first'")
        worker = asyncio.create_task(instance.worker())
        for _ in range(10):
            await asyncio.sleep(0)
        assert entered == ["a-first", "b-first"]
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        instance.db.close()

    asyncio.run(scenario())


def test_work_item_and_conversation_serialize_distinct_provider_lanes(tmp_path):
    async def scenario(reason):
        instance, _ = service(tmp_path / reason)
        instance.config["projects"]["p"].pop("root", None)
        entered = []

        async def execute(row):
            entered.append(row["id"])
            await asyncio.Event().wait()

        instance.execute = execute
        if reason == "conversation":
            enqueue(instance, "parent", "p", "claude")
            instance.finish("parent", "completed", {})
        for job, backend in [("one", "claude"), ("two", "gemini")]:
            enqueue(
                instance,
                job,
                "p",
                backend,
                parent="parent" if reason == "conversation" else None,
                work_item="ITEM1" if reason == "work_item" else None,
            )
        worker = asyncio.create_task(instance.worker())
        for _ in range(10):
            await asyncio.sleep(0)
        assert entered == ["one"]
        assert any(reason in row["data"] for row in instance.message_repository.all_events("two"))
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        assert not instance.write_ownership.leases
        instance.db.close()

    asyncio.run(scenario("conversation"))
    asyncio.run(scenario("work_item"))
