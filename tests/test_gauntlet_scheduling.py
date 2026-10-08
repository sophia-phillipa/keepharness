"""Synthetic queue regressions: ownership, human waits and coordinator reloads."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from test_execution_modes import service

from agent_service import maestro


def test_option_gates_obey_consecutive_human_wait_limit(tmp_path):
    async def scenario():
        s, ident = service(tmp_path)
        s.config.update(approval_timeout_seconds=0.001, approval_max_consecutive_expirations=2)
        job = s.submit(
            ident,
            {
                "project_id": "p",
                "backend": "local",
                "model": "installed-model",
                "prompt": "wait",
                "execution_mode": "scoped",
            },
        )["job_id"]
        replies = []

        async def execute(row):
            plan = SimpleNamespace(row=row, data={"model": "installed-model"}, backend="local")
            approve = s._approval_handler(plan, lambda k, d: s.event(job, k, d), {}, {})
            for _ in range(4):
                replies.append(
                    await approve(
                        "gate",
                        {"question": "Continue?", "options": [{"id": "ok", "label": "Continue"}]},
                    )
                )
            return {"answer": "Finished despite four consecutive expired human waits"}

        s.execute = execute
        s.quota = AsyncMock(return_value={})
        worker = asyncio.create_task(s.worker())
        try:
            async with asyncio.timeout(1):
                while s.conversation_repository.state(job)[0] in ("queued", "running"):
                    await asyncio.sleep(0.001)
            row = s.conversation_repository.get(job)
            print("A4-S5 gate expiry:", row["state"], replies, row["result"])
            assert row["state"] == "cancelled", (
                "Two consecutive expired human waits must cancel regardless of gate transport"
            )
            assert len(replies) == 1
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            s.db.close()

    asyncio.run(scenario())


def test_declared_chain_local_step_acquires_effective_write_roots(tmp_path):
    async def scenario():
        s, ident = service(tmp_path)
        shared = tmp_path / "shared"
        shared.mkdir()
        s.config["projects"] = {"p": {"root": str(tmp_path / "p")}, "q": {"root": str(shared)}}
        ident[1]["projects"] = ["p", "q"]
        s.config["services"]["codex"]["projects"] = ["p", "q"]
        s.config["services"]["local"]["permissions"]["write"] = True
        s.config["local"]["model_roots"] = {"installed-model": [str(shared)]}
        m = s.submit(
            ident,
            {
                "project_id": "p",
                "backend": "local",
                "model": "installed-model",
                "effort": "configured",
                "prompt": "Review",
            },
        )["job_id"]
        s.submit(
            ident,
            {
                "project_id": "q",
                "backend": "codex",
                "model": "gpt-6-astra",
                "prompt": "Write shared fixture",
            },
        )["job_id"]
        entered = []
        block = asyncio.Event()
        effective = {}

        async def infer(row, data):
            p = SimpleNamespace(
                row=row,
                data=data,
                backend=data["backend"],
                execution_mode="scoped",
                selected_resources=[],
                image_sources=[],
                persisted_session=None,
                pending_files=set(),
                history_folder=None,
                catalog_runtime=None,
            )
            project, _, permissions = s._project_config(p)
            effective.update(project=project, permissions=permissions)
            entered.append("chain-local")
            await block.wait()
            return {"answer": "done"}

        s.infer = infer
        plan = {
            "steps": [
                {
                    "role": "writer",
                    "backend": "local",
                    "model": "installed-model",
                    "effort": "configured",
                    "task": "Write fixture",
                    "reason": "Requested",
                }
            ]
        }

        async def execute(row):
            if row["id"] == m:
                return await maestro.execute_plan(s, row, json.loads(row["payload"]), plan)
            entered.append("codex-shared")
            await block.wait()
            return {"answer": "done"}

        s.execute = execute
        s.quota = AsyncMock(return_value={})
        worker = asyncio.create_task(s.worker())
        try:
            for _ in range(50):
                await asyncio.sleep(0.001)
            print(
                "A4-S6 simultaneous writers:",
                entered,
                "effective_local_project:",
                effective,
                "leases:",
                s.write_ownership.leases,
            )
            assert str(shared) in effective["project"]["additional_roots"]
            assert effective["project"]["apply_changes"] is True
            assert len(entered) == 1, (
                "A declared-chain local step and other provider must not concurrently own overlapping writable roots"
            )
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            s.db.close()

    asyncio.run(scenario())
