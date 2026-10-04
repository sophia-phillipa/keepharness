import asyncio
import json
import sqlite3
from unittest.mock import AsyncMock

import httpx
import pytest
from test_execution_modes import service

from agent_service.app import create_app


@pytest.mark.parametrize("locked", [False, True])
def test_cancel_queued_while_other_job_runs(tmp_path, locked):
    async def scenario():
        s, ident = service(tmp_path)
        # Pins one run per provider; the default for cloud providers is 2 (D14).
        s.config["services"]["codex"]["max_concurrent"] = 1
        payload = dict(
            project_id="p", backend="codex", model="gpt-6-astra", prompt="synthetic task"
        )
        first = s.submit(ident, payload)["job_id"]
        second = s.submit(ident, payload)["job_id"]
        began = asyncio.Event()
        release = asyncio.Event()
        entered = []

        async def execute(row):
            entered.append(row["id"])
            if row["id"] == first:
                began.set()
                await release.wait()
            return {"answer": "synthetic success"}

        s.execute = execute
        s.quota = AsyncMock(return_value={})
        app = create_app(s.config)
        app.state.service.db.close()
        app.state.service = s
        worker = asyncio.create_task(s.worker())
        await asyncio.wait_for(began.wait(), 1)
        s.db.execute("PRAGMA busy_timeout=5")
        lock = sqlite3.connect(tmp_path / "jobs.sqlite3")
        try:
            if locked:
                lock.execute("BEGIN IMMEDIATE")
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
                headers={"Authorization": "Bearer a"},
            ) as client:
                response = await client.post("/v1/jobs/" + second + "/cancel", json={})
                if locked:
                    assert response.status_code == 503
                    assert response.json()["retryable"] is True
                    assert response.headers["Retry-After"] == "1"
                    assert s.conversation_repository.state(second)[0] == "queued"
                    lock.rollback()
                    response = await client.post("/v1/jobs/" + second + "/cancel", json={})
            after = s.conversation_repository.state(second)[0]
            lock.rollback()
            release.set()
            async with asyncio.timeout(1):
                while s.conversation_repository.state(second)[0] not in (
                    "completed",
                    "cancelled",
                    "failed",
                ):
                    await asyncio.sleep(0.001)
            final = s.conversation_repository.state(second)[0]
            print(
                "LIVE_QUEUE_CANCEL",
                json.dumps(
                    {
                        "locked": locked,
                        "http": response.status_code,
                        "response": response.json(),
                        "after_cancel": after,
                        "final": final,
                        "cancelled_job_executed": second in entered,
                    }
                ),
            )
            assert response.status_code == 200
            assert second not in entered, (
                "Cancelled queued job must never execute after storage unlock"
            )
            assert final == "cancelled"
        finally:
            lock.rollback()
            lock.close()
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            s.db.close()

    asyncio.run(scenario())
