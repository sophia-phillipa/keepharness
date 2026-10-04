import asyncio
import json
import sqlite3
from unittest.mock import AsyncMock

import pytest
from test_execution_modes import service


@pytest.mark.parametrize("boundary", ["set_running", "queue_wait"])
def test_scheduler_transient_dispatch_failure(tmp_path, boundary):
    async def scenario():
        s, ident = service(tmp_path)
        # Pins one run per provider; the default for cloud providers is 2 (D14).
        s.config["services"]["codex"]["max_concurrent"] = 1
        jobs = [
            s.submit(
                ident, dict(project_id="p", backend="codex", model="gpt-6-astra", prompt=prompt)
            )["job_id"]
            for prompt in ("one", "two")
        ]
        s.execute = AsyncMock(return_value={"answer": "synthetic"})
        s.quota = AsyncMock(return_value={})
        original = s.conversation_repository.set_running
        failures = []

        def once(job):
            if not failures:
                failures.append(job)
                raise sqlite3.OperationalError("synthetic transient write fault")
            return original(job)

        if boundary == "set_running":
            s.conversation_repository.set_running = once
        else:
            original_event = s.event

            def event(job, kind, data):
                if kind == "queue_wait" and not failures:
                    failures.append(job)
                    raise sqlite3.OperationalError("synthetic queue wait persistence fault")
                return original_event(job, kind, data)

            s.event = event
        worker = asyncio.create_task(s.worker())
        try:
            for _ in range(50):
                await asyncio.sleep(0.001)
            s.wake.set()
            for _ in range(50):
                await asyncio.sleep(0.001)
            observed = {
                "worker_done": worker.done(),
                "worker_error": str(worker.exception()) if worker.done() else None,
                "states": [s.conversation_repository.state(j)[0] for j in jobs],
                "leases": list(s.write_ownership.leases),
                "execute_calls": s.execute.await_count,
            }
            print(json.dumps(observed))
            assert len(failures) == 1
            assert not worker.done(), (
                "One transient scheduler write terminates the entire worker permanently"
            )
            assert all(s.conversation_repository.state(job)[0] == "completed" for job in jobs)
            assert s.execute.await_count == len(jobs)
            assert not s.write_ownership.leases
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            s.db.close()

    asyncio.run(scenario())


def test_actual_sqlite_lock_recovers_worker(tmp_path):
    async def scenario():
        s, ident = service(tmp_path)
        jobs = [
            s.submit(ident, dict(project_id="p", backend="codex", model="gpt-6-astra", prompt=p))[
                "job_id"
            ]
            for p in ("one", "two")
        ]
        s.execute = AsyncMock(return_value={"answer": "synthetic"})
        s.quota = AsyncMock(return_value={})
        dbpath = s.db.execute("PRAGMA database_list").fetchone()[2]
        s.db.execute("PRAGMA busy_timeout=10")
        blocker = sqlite3.connect(dbpath)
        blocker.execute("BEGIN IMMEDIATE")
        worker = asyncio.create_task(s.worker())
        try:
            for _ in range(20):
                await asyncio.sleep(0.001)
            blocker.rollback()
            blocker.close()
            third = s.submit(
                ident,
                dict(
                    project_id="p",
                    backend="codex",
                    model="gpt-6-astra",
                    prompt="after lock released",
                ),
            )["job_id"]
            for _ in range(20):
                await asyncio.sleep(0.001)
            observed = {
                "worker_done": worker.done(),
                "error": str(worker.exception()) if worker.done() else None,
                "states": [s.conversation_repository.state(j)[0] for j in jobs + [third]],
                "leases": len(s.write_ownership.leases),
                "calls": s.execute.await_count,
            }
            print("SQLITE_LOCK", json.dumps(observed))
            assert not worker.done(), (
                "Real database lock leaves queue permanently unserviced even after lock released"
            )
            for _ in range(200):
                if all(
                    s.conversation_repository.state(job)[0] == "completed" for job in jobs + [third]
                ):
                    break
                await asyncio.sleep(0.01)
            assert all(
                s.conversation_repository.state(job)[0] == "completed" for job in jobs + [third]
            )
            assert s.execute.await_count == 3
            assert not s.write_ownership.leases
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            s.db.close()

    asyncio.run(scenario())
