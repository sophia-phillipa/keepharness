import asyncio
import json
import sqlite3
from unittest.mock import AsyncMock

from test_execution_modes import service


def test_cancel_during_other_jobs_settlement_does_not_resurrect(tmp_path):
    async def scenario():
        s, identity = service(tmp_path / "state")
        root = tmp_path / "broken"
        root.mkdir()
        s.config["projects"]["p"]["root"] = str(root)
        good = tmp_path / "good"
        good.mkdir()
        s.config["projects"]["q"] = {"root": str(good)}
        identity[1]["projects"].append("q")
        s.config["services"]["codex"]["projects"].append("q")
        data = dict(backend="codex", model="gpt-6-astra", prompt="Synthetic")
        first = s.submit(identity, dict(data, project_id="p"))["job_id"]
        second = s.submit(identity, dict(data, project_id="q"))["job_id"]
        root.rmdir()
        root.symlink_to(root.name)
        s.db.execute("PRAGMA busy_timeout=1")
        lock = sqlite3.connect(s.db.execute("PRAGMA database_list").fetchone()[2])
        lock.execute("BEGIN IMMEDIATE")
        entered = []

        async def execute(row):
            entered.append(row["id"])
            return {"answer": "synthetic"}

        s.execute = execute
        s.quota = AsyncMock(return_value={})
        worker = asyncio.create_task(s.worker())
        try:
            await asyncio.sleep(0.02)
            lock.rollback()
            cancellation = s.cancel(identity, second)
            after_cancel = s.conversation_repository.state(second)[0]
            await asyncio.sleep(0.2)
            observed = dict(
                first=s.conversation_repository.state(first)[0],
                second=s.conversation_repository.state(second)[0],
                after_cancel=after_cancel,
                cancellation=cancellation,
                second_executed=second in entered,
                worker_done=worker.done(),
            )
            print("STALE_QUEUE_SNAPSHOT", json.dumps(observed))
            assert after_cancel == "cancelled"
            assert second not in entered, observed
            assert observed["second"] == "cancelled"
            assert observed["first"] == "failed"
            third = s.submit(identity, dict(data, project_id="q"))["job_id"]
            await asyncio.sleep(0.1)
            assert s.conversation_repository.state(third)[0] == "completed"
        finally:
            lock.close()
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            s.db.close()

    asyncio.run(scenario())


def test_tag_during_other_job_settlement_uses_current_work_item_lease(tmp_path):
    async def scenario():
        s, identity = service(tmp_path / "state")
        root = tmp_path / "broken"
        root.mkdir()
        s.config["projects"]["p"]["root"] = str(root)
        s.config["projects"]["q"] = {}
        identity[1]["projects"].append("q")
        s.config["services"]["codex"]["projects"].append("q")
        data = dict(backend="codex", model="gpt-6-astra", prompt="Synthetic")
        first = s.submit(identity, dict(data, project_id="p"))["job_id"]
        second = s.submit(identity, dict(data, project_id="q", work_item="OLD"))["job_id"]
        assert s.write_ownership.acquire("competing-writer", "q", "NEW", []) is None
        root.rmdir()
        root.symlink_to(root.name)
        s.db.execute("PRAGMA busy_timeout=1")
        lock = sqlite3.connect(s.db.execute("PRAGMA database_list").fetchone()[2])
        lock.execute("BEGIN IMMEDIATE")
        entered = []

        async def execute(row):
            entered.append(dict(row))
            await asyncio.Event().wait()

        s.execute = execute
        s.quota = AsyncMock(return_value={})
        worker = asyncio.create_task(s.worker())
        try:
            await asyncio.sleep(0.02)
            lock.rollback()
            tag = s.tag_work_item(identity, second, "NEW")
            await asyncio.sleep(0.15)
            observed = dict(
                tag=tag,
                state=s.conversation_repository.state(second)[0],
                persisted_work_item=s.job(identity, second)["work_item"],
                dispatch_work_items=[row["work_item"] for row in entered],
                leases={
                    key: [value[0], value[1]] for key, value in s.write_ownership.leases.items()
                },
            )
            print("STALE_WORK_ITEM", json.dumps(observed))
            assert not entered, observed
            assert observed["state"] == "queued"
            assert s.conversation_repository.state(first)[0] == "failed"
            s.write_ownership.release("competing-writer")
            s.wake.set()  # Lease release normally accompanies a finished worker.
            await asyncio.sleep(0.1)
            assert [row["work_item"] for row in entered] == ["NEW"]
            assert json.loads(entered[0]["payload"])["work_item"] == "NEW"
        finally:
            lock.close()
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            s.write_ownership.release("competing-writer")
            s.db.close()

    asyncio.run(scenario())


def test_failed_terminal_write_does_not_reintroduce_attempted_candidate(tmp_path, monkeypatch):
    async def scenario():
        s, identity = service(tmp_path / "state")
        root = tmp_path / "broken"
        root.symlink_to(root.name)
        s.config["projects"]["p"]["root"] = str(root)
        s.config["projects"]["q"] = {}
        identity[1]["projects"].append("q")
        s.config["services"]["codex"]["projects"].append("q")
        data = dict(backend="codex", model="gpt-6-astra", prompt="Synthetic")
        # Admission requires a valid root; make it invalid only after queuing.
        s.config["projects"]["p"]["root"] = str(tmp_path)
        first = s.submit(identity, dict(data, project_id="p"))["job_id"]
        second = s.submit(identity, dict(data, project_id="q"))["job_id"]
        s.config["projects"]["p"]["root"] = str(root)
        finish = s.finish
        attempts = []

        def fail_first(job, state, result):
            if job == first:
                attempts.append(job)
                raise sqlite3.OperationalError("synthetic disk full")
            return finish(job, state, result)

        monkeypatch.setattr(s, "finish", fail_first)
        ready = s.conversation_repository.ready
        reads = 0

        def bounded_ready():
            nonlocal reads
            reads += 1
            if reads > 5:
                raise RuntimeError(
                    "Repeated failed candidate: stop test before event-loop starvation"
                )
            return ready()

        monkeypatch.setattr(s.conversation_repository, "ready", bounded_ready)
        s.execute = AsyncMock(return_value={"answer": "synthetic"})
        s.quota = AsyncMock(return_value={})
        worker = asyncio.create_task(s.worker())
        try:
            await asyncio.sleep(0.1)
            assert s.conversation_repository.state(second)[0] == "completed"
            assert not worker.done()
            assert len(attempts) <= 4  # Initial pass and the completed job's wake.
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            s.db.close()

    asyncio.run(scenario())
