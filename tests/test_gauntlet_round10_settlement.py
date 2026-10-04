import asyncio
import json
import sqlite3
from unittest.mock import AsyncMock

import pytest
from test_execution_modes import service as make_service

from agent_service.errors import ToolError


@pytest.mark.parametrize("outcome", ["failed", "cancelled", "interrupted"])
def test_error_settlement_lock_does_not_strand_conversation(tmp_path, outcome):
    async def scenario():
        service, identity = make_service(tmp_path)

        def submit(prompt, parent=None):
            return service.submit(
                identity,
                dict(
                    project_id="p",
                    backend="codex",
                    model="gpt-6-astra",
                    prompt=prompt,
                    **({"parent_job_id": parent} if parent else {}),
                ),
            )["job_id"]

        job = submit("synthetic first")
        child = submit("synthetic continuation", job)
        service.db.execute("PRAGMA busy_timeout=5")
        blocker = sqlite3.connect(service.db.execute("PRAGMA database_list").fetchone()[2])
        acquired = asyncio.Event()
        calls = []

        async def execute(row):
            calls.append(row["id"])
            if row["id"] == job:
                blocker.execute("BEGIN IMMEDIATE")
                acquired.set()
                if outcome == "failed":
                    raise ToolError("synthetic_provider_failure")
                if outcome == "interrupted":
                    raise ToolError("provider_rate_limit")
                await asyncio.Future()
            return {"answer": "child complete"}

        service.execute = execute
        service.quota = AsyncMock(return_value={})
        worker = asyncio.create_task(service.worker())
        try:
            await asyncio.wait_for(acquired.wait(), 1)
            if outcome == "cancelled":
                service.cancel(identity, job)
            await asyncio.sleep(0.12)
            blocker.rollback()
            service.wake.set()
            await asyncio.sleep(0.2)
            if outcome == "cancelled":
                # D16: Stop holds the queued follow-up until the user runs it.
                assert service.conversation_repository.state(child)[0] == "queued"
                service.run_queued(identity, child)
                await asyncio.sleep(0.2)
            observed = {
                "outcome": outcome,
                "parent": service.conversation_repository.state(job)[0],
                "child": service.conversation_repository.state(child)[0],
                "calls": calls,
                "job_tasks": list(service.job_tasks),
                "leases": list(service.write_ownership.leases),
                "worker_done": worker.done(),
            }
            print("OBSERVED", json.dumps(observed))
            assert observed["parent"] == outcome, observed
            assert observed["child"] == "completed", observed
            assert calls == [job, child]
            assert not service.job_tasks and not service.write_ownership.leases
            assert not worker.done()
        finally:
            blocker.rollback()
            blocker.close()
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            service.db.close()

    asyncio.run(scenario())
