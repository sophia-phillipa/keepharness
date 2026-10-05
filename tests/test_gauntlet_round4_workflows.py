"""Round-four synthetic regression boundaries; all state is temporary."""

import asyncio
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from test_execution_modes import service
from test_invocation_normalization import invocation_service
from test_workspaces import config

from agent_service import maestro
from agent_service.app import APIError, create_app


def test_running_event_failure_settles_job_and_releases_followup(tmp_path):
    async def scenario():
        s, ident = service(tmp_path)
        first = s.submit(
            ident, dict(project_id="p", backend="codex", model="gpt-6-astra", prompt="first")
        )["job_id"]
        child = s.submit(
            ident,
            dict(
                project_id="p",
                backend="codex",
                model="gpt-6-astra",
                prompt="followup",
                parent_job_id=first,
            ),
        )["job_id"]
        other = s.submit(
            ident,
            dict(project_id="p", backend="local", model="installed-model", prompt="independent"),
        )["job_id"]
        s.execute = AsyncMock(return_value={"answer": "synthetic"})
        s.quota = AsyncMock(return_value={})
        original = s.message_repository.add_event
        faulted = []

        def once(job, timestamp, kind, data):
            if job == first and kind == "running" and not faulted:
                faulted.append(kind)
                raise sqlite3.OperationalError("synthetic transient event write failure")
            return original(job, timestamp, kind, data)

        s.message_repository.add_event = once
        worker = asyncio.create_task(s.worker())
        try:
            for _ in range(1000):
                if all(
                    s.conversation_repository.state(j)[0]
                    in ("completed", "failed", "cancelled", "interrupted")
                    for j in (first, child, other)
                ):
                    break
                await asyncio.sleep(0.002)
            states = {
                name: s.conversation_repository.state(job)[0]
                for name, job in [("first", first), ("followup", child), ("independent", other)]
            }
            print(
                "RUNNING_EVENT_FAILURE",
                json.dumps(
                    {
                        "states": states,
                        "worker_alive": not worker.done(),
                        "job_task_present": first in s.job_tasks,
                        "lease_present": first in s.write_ownership.leases,
                    }
                ),
            )
            assert states["first"] in ("failed", "interrupted", "cancelled"), (
                "Transient event failure leaves a running job with no task"
            )
            assert states["followup"] == "completed"
            assert states["independent"] == "completed"
            assert s.execute.await_count == 2
            assert first not in s.job_tasks
            assert first not in s.write_ownership.leases
            assert first not in s.runtime_budgets
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            s.db.close()

    asyncio.run(scenario())


def test_native_approval_open_failure_removes_pending_waiter(tmp_path):
    async def scenario():
        s, ident = service(tmp_path)
        s.config["approval_timeout_seconds"] = 0.01
        job = s.submit(
            ident, dict(project_id="p", backend="codex", model="gpt-6-astra", prompt="approve")
        )["job_id"]
        plan = SimpleNamespace(
            row=s.job(ident, job), data={"model": "gpt-6-astra"}, backend="codex"
        )

        def progress(kind, data):
            if kind == "approval_required":
                raise sqlite3.OperationalError("synthetic transient event write failure")

        approve = s._approval_handler(plan, progress, {}, {})
        try:
            try:
                await approve("command", {"command": "synthetic"})
            except sqlite3.OperationalError:
                pass
            assert not s.approvals
            s.finish(job, "failed", {"error": "OperationalError"})
            await asyncio.sleep(0.03)
            print(
                "NATIVE_APPROVAL_OPEN_FAILURE",
                json.dumps(
                    {
                        "job_state": s.job(ident, job)["state"],
                        "pending_count": len(s.approvals),
                        "future_done": [f.done() for _, f in s.approvals.values()],
                    }
                ),
            )
            assert not s.approvals, (
                "Failed approval opening bypasses cleanup and its timeout never starts"
            )
            s.config["approval_timeout_seconds"] = 1
            events = []
            retry = s._approval_handler(
                plan, lambda kind, data: events.append((kind, data)), {}, {}
            )
            task = asyncio.create_task(retry("command", {"command": "synthetic"}))
            await asyncio.sleep(0)
            assert len(s.approvals) == 1
            _, future = next(iter(s.approvals.values()))
            future.set_result({"approved": True})
            assert (await task)["approved"] is True
            assert not s.approvals
            assert [kind for kind, _ in events] == ["approval_required", "approval_resolved"]
        finally:
            s.db.close()

    asyncio.run(scenario())


def test_missing_workspace_source_degrades_completed_prefix_to_zero(tmp_path):
    cfg = config(tmp_path)
    app = create_app(cfg)
    service = app.state.service
    identity = ("a", cfg["clients"]["a"])
    workspace_root = service.root / "workspaces" / "workspace-a" / "work"
    workspace_root.mkdir(parents=True)
    source = workspace_root / "input.txt"
    source.write_text("synthetic input")
    with service.db:
        service.project_repository.add_workspace(
            "workspace-a", "p", "a", "synthetic", 1, json.dumps([{"path": "input.txt"}])
        )
    submitted = service.submit(
        identity,
        {
            "project_id": "p",
            "backend": "codex",
            "model": "gpt-6-astra",
            "effort": "low",
            "prompt": "Review workspace",
            "workspace_id": "workspace-a",
        },
    )
    row = service.job(identity, submitted["job_id"])
    data = json.loads(row["payload"])
    plan = {
        "steps": [
            {
                "role": "review",
                "backend": "codex",
                "model": "gpt-6-astra",
                "effort": "low",
                "task": "Review",
                "reason": "Synthetic",
            }
        ]
    }
    try:
        with patch.object(service, "infer", AsyncMock(return_value={"answer": "done"})):
            result = asyncio.run(maestro.execute_plan(service, row, data, plan))
        service.finish(row["id"], "completed", result)
        assert service.workflow_completed_steps(service.job(identity, row["id"])) == 1
        source.unlink()

        async def fetch():
            transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
                headers={"Authorization": "Bearer a"},
            ) as client:
                job_response = await client.get("/v1/jobs/" + row["id"])
                conversation_response = await client.get("/v1/conversations/" + row["id"])
                return job_response, conversation_response

        job_response, conversation_response = asyncio.run(fetch())
        assert job_response.status_code == 200, job_response.text
        assert conversation_response.status_code == 200, conversation_response.text
        assert job_response.json()["result"]["answer"] == "done"
        assert conversation_response.json()["turns"][0]["result"]["answer"] == "done"
        assert job_response.json()["workflow_completed_steps"] == 0
        assert conversation_response.json()["turns"][0]["workflow_completed_steps"] == 0
    finally:
        service.db.close()


def test_single_invocation_rejects_conflicting_backend_before_enqueue(tmp_path, monkeypatch):
    service, identity = invocation_service(tmp_path, monkeypatch)
    source = tmp_path / "project/.codex/agents/reviewer.toml"
    source.write_text(
        source.read_text() + '\nbackend="local"\nmodel="installed-model"\neffort="configured"\n'
    )
    try:
        item = next(
            item
            for item in service.resource_catalog(identity, "p", "codex", "gpt-6-astra")["items"]
            if item["name"] == "reviewer"
        )
        with pytest.raises(APIError, match="invocation_backend_mismatch"):
            service.submit(
                identity,
                {
                    "project_id": "p",
                    "backend": "codex",
                    "model": "gpt-6-astra",
                    "prompt": "/reviewer inspect",
                    "resource_selections": [
                        {"id": item["id"], "revision": item["revision"], "token": "/reviewer"}
                    ],
                },
            )
        assert service.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    finally:
        service.db.close()


@pytest.mark.parametrize("field,value", [("model", "other-model"), ("effort", "high")])
def test_single_invocation_rejects_conflicting_model_or_effort(tmp_path, monkeypatch, field, value):
    instance, identity = invocation_service(tmp_path, monkeypatch)
    source = tmp_path / "project/.codex/agents/reviewer.toml"
    source.write_text(source.read_text() + f'\n{field}="{value}"\n')
    try:
        item = next(
            item
            for item in instance.resource_catalog(identity, "p", "codex", "gpt-6-astra")["items"]
            if item["name"] == "reviewer"
        )
        with pytest.raises(APIError, match="invocation_model_or_effort_mismatch"):
            instance.submit(
                identity,
                {
                    "project_id": "p",
                    "backend": "codex",
                    "model": "gpt-6-astra",
                    "effort": "low",
                    "prompt": "/reviewer inspect",
                    "resource_selections": [
                        {"id": item["id"], "revision": item["revision"], "token": "/reviewer"}
                    ],
                },
            )
        assert instance.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    finally:
        instance.db.close()
