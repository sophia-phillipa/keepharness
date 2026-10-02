import asyncio
import json
import sqlite3
from unittest.mock import AsyncMock

import pytest
from test_execution_modes import service


@pytest.mark.parametrize("locked", [False, True])
def test_unavailable_root_terminal_recovers_after_real_sqlite_lock(tmp_path, locked):
    async def scenario():
        s, ident = service(tmp_path / "state")
        root = tmp_path / "root"
        root.mkdir()
        s.config["projects"]["p"]["root"] = str(root)
        job = s.submit(
            ident, dict(project_id="p", backend="codex", model="gpt-6-astra", prompt="synthetic")
        )["job_id"]
        root.rmdir()
        root.symlink_to(root.name)
        s.execute = AsyncMock(return_value={"answer": "synthetic"})
        s.quota = AsyncMock(return_value={})
        s.db.execute("PRAGMA busy_timeout=10")
        dbpath = s.db.execute("PRAGMA database_list").fetchone()[2]
        blocker = sqlite3.connect(dbpath)
        if locked:
            blocker.execute("BEGIN IMMEDIATE")
        worker = asyncio.create_task(s.worker())
        try:
            await asyncio.sleep(0.08)
            blocker.rollback()
            blocker.close()
            await asyncio.sleep(0.2)
            observed = dict(
                worker_done=worker.done(),
                state=s.conversation_repository.state(job)[0],
                executions=s.execute.await_count,
                leases=list(s.write_ownership.leases),
                wake=s.wake.is_set(),
            )
            print("AFTER_WRITER_UNLOCK", json.dumps({"locked": locked, **observed}))
            assert observed["state"] == "failed", observed
            assert not observed["worker_done"] and not observed["leases"]
            assert observed["executions"] == 0
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            s.db.close()

    asyncio.run(scenario())
