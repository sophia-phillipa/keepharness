import asyncio
import json
import sqlite3
import threading
import time
from unittest.mock import AsyncMock, patch

import pytest
from test_execution_modes import service as execution_service
from test_partial_answer import _run_to_terminal, _submit
from test_shared_projects import config
from test_workflow_resume_rerun import setup_run
from test_workspaces import config as native_config

from agent_service import maestro, mcp_bridge, workflows
from agent_service.app import Service
from agent_service.conversation_context import portable_history
from agent_service.errors import APIError
from agent_service.tools import ToolError


@pytest.mark.parametrize("mode", ["expire", "cancel", "expire_cancel"])
@pytest.mark.parametrize("plan_gate", [False, True])
@pytest.mark.parametrize("busy_timeout", [1, 5000])
def test_gate_terminal_write_survives_contention(tmp_path, mode, plan_gate, busy_timeout):
    async def run():
        instance, identity = execution_service(tmp_path)
        instance.config["approval_timeout_seconds"] = 0.05 if mode != "cancel" else 30
        instance.db.execute(f"PRAGMA busy_timeout={busy_timeout}")
        instance.conversation_repository.insert(
            "job",
            "p",
            "a",
            "queued",
            time.time(),
            json.dumps({"backend": "local", "model": "installed-model"}),
            None,
            None,
            "job",
        )
        instance.db.commit()

        async def execute(row):
            reply = await instance.gates.ask(
                "job",
                {"question": "Continue?", "options": [{"id": "yes", "label": "Yes"}]},
                lambda kind, data: instance.event("job", kind, data),
                plan={"steps": []} if plan_gate else None,
            )
            return {"answer": str(reply)}

        instance.execute = execute
        worker = asyncio.create_task(instance.worker())
        lock = sqlite3.connect(tmp_path / "jobs.sqlite3", check_same_thread=False)
        timer = None
        try:
            async with asyncio.timeout(2):
                while not instance.approvals:
                    await asyncio.sleep(0.001)
            gate_id = next(iter(instance.approvals))
            lock.execute("BEGIN IMMEDIATE")
            timer = threading.Timer(5.5 if busy_timeout == 5000 else 0.15, lock.rollback)
            timer.start()
            if mode in ("cancel", "expire_cancel"):
                if mode == "expire_cancel":
                    await asyncio.sleep(0.08)
                instance.cancel(identity, "job")
            async with asyncio.timeout(9):
                while instance.conversation_repository.get("job")["state"] in ("queued", "running"):
                    await asyncio.sleep(0.01)
            row = instance.conversation_repository.get("job")
            assert row["state"] == ("cancelled" if mode != "expire" else "completed")
            assert instance.gates.repository.get(gate_id)["state"] == (
                "invalidated" if mode != "expire" else "expired"
            )
            terminal = [
                r
                for r in instance.message_repository.all_events("job")
                if r["type"] in ("gate_expired", "gate_invalidated")
            ]
            assert len(terminal) == 1
            assert not instance.approvals and not instance.gates.progress
            assert not worker.done()
        finally:
            if timer:
                timer.join()
            lock.close()
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            instance.db.close()

    asyncio.run(run())


@pytest.mark.parametrize("root_text", ["", "Root answer. "])
def test_partial_answer_and_portable_context_exclude_delegated_text(tmp_path, root_text):
    async def run():
        instance = Service(config(tmp_path))
        identity = ("a", instance.config["clients"]["a"])
        job = _submit(instance, identity)

        async def execute(row):
            instance.event(job, "answer_delta", {"text": root_text})
            instance.event(
                job, "answer_delta", {"text": "Child answer.", "parent_tool_use_id": "child"}
            )
            raise ToolError("provider_authentication_required")

        instance.execute = execute
        instance.quota = AsyncMock(return_value={})
        try:
            row = await _run_to_terminal(instance, identity, job)
            result = json.loads(row["result"])
            assert result.get("partial_answer", "") == root_text
            payload = {**json.loads(row["payload"]), "_job_id": job, "_state": row["state"]}
            history = portable_history(instance.db, [(payload, {})])
            assert history[0]["assistant"] == root_text
            assert any(
                "Child answer." in r["data"] for r in instance.message_repository.all_events(job)
            )
        finally:
            instance.db.close()

    asyncio.run(run())


@pytest.mark.parametrize("rerun", [False, True])
def test_mcp_recovery_forwards_idempotency_through_http(tmp_path, monkeypatch, rerun):
    import httpx

    from agent_service.app import create_app

    async def run():
        instance, identity, row, data, plan = setup_run(tmp_path)
        with patch.object(instance, "infer", AsyncMock(return_value={"answer": "done"})):
            result = await maestro.execute_plan(instance, row, data, plan)
        instance.finish(row["id"], "completed", result)
        app = create_app(instance.config)
        app.state.service.db.close()
        app.state.service = instance

        async def call(method, path, payload=None, headers=None):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
                headers={"Authorization": "Bearer a", **(headers or {})},
            ) as client:
                response = await client.request(method, path, json=payload)
                if response.status_code >= 400:
                    raise APIError(response.text, response.status_code)
                return response.json()

        monkeypatch.setattr(mcp_bridge, "call", call)
        fn = mcp_bridge.rerun_workflow if rerun else mcp_bridge.resume_workflow
        args = {"job_id": row["id"], "idempotency_key": "retry-key"}
        if rerun:
            args["from_step"] = 1
        try:
            first = await fn(**args)
            second = await fn(**args)
            assert first["job_id"] == second["job_id"] and second["reused"]
            assert (
                instance.db.execute("SELECT count(*) FROM jobs WHERE idem='retry-key'").fetchone()[
                    0
                ]
                == 1
            )
            with pytest.raises(APIError, match="idempotency_conflict"):
                await fn(**args, workflow_inputs={"changed": True})
        finally:
            await instance.effects.close()
            instance.db.close()

    asyncio.run(run())


@pytest.mark.parametrize("suffix", [".json", ".yaml", ".yml"])
@pytest.mark.parametrize("field", ["id", "name"])
def test_saved_workflow_refuses_existing_logical_name(tmp_path, suffix, field):
    import importlib.util

    yaml_unavailable = suffix != ".json" and importlib.util.find_spec("yaml") is None
    instance, identity, row, data, plan = setup_run(tmp_path / "state")
    root = tmp_path / "project"
    folder = root / "workflows"
    folder.mkdir(parents=True)
    document = {"version": 1, "id": "legacy", "steps": plan["steps"]}
    document[field] = "saved"
    existing = folder / ("legacy-name" + suffix)
    text = json.dumps(document)
    if suffix != ".json":
        # The parser accepts JSON as a YAML subset.
        text = (
            "version: 1\nid: "
            + document["id"]
            + "\n"
            + ("name: saved\n" if field == "name" else "")
            + "steps: "
            + json.dumps(document["steps"])
            + "\n"
        )
    existing.write_text(text)
    try:
        with pytest.raises(
            workflows.WorkflowError,
            match="workflow_yaml_unavailable_use_json"
            if yaml_unavailable
            else "workflow_already_exists",
        ):
            workflows.save_chain_as_workflow({"root": str(root)}, plan, "saved", successful=True)
        assert existing.read_text() == text
        assert not (folder / "saved.json").exists()
    finally:
        instance.db.close()


@pytest.mark.parametrize("change", ["edit", "delete", "valid"])
def test_selected_resource_rechecked_after_provider_capacity_wait(tmp_path, monkeypatch, change):
    async def run():
        cfg = native_config(tmp_path / "state")
        cfg["projects"]["p"]["root"] = str(tmp_path / "project")
        cfg["services"]["codex"].update(mode="native", max_concurrent=1)
        cfg["codex"] = {"binary": "synthetic"}
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        monkeypatch.delenv("CODEX_HOME", raising=False)
        path = tmp_path / "home/.codex/agents/review.toml"
        path.parent.mkdir(parents=True)
        path.write_text('name="review"\ndeveloper_instructions="Original"\n')
        instance = Service(cfg)
        identity = ("a", cfg["clients"]["a"])
        prepared = asyncio.Event()
        original = instance._prepare_inference

        async def prepare(*args):
            result = await original(*args)
            prepared.set()
            return result

        instance._prepare_inference = prepare
        item = next(
            item
            for item in instance.resource_catalog(identity, "p", "codex", "gpt-6-astra")["items"]
            if item["name"] == "review"
        )
        job = instance.submit(
            identity,
            {
                "project_id": "p",
                "backend": "codex",
                "model": "gpt-6-astra",
                "effort": "low",
                "prompt": "/review check",
                "resource_selections": [
                    {"id": item["id"], "revision": item["revision"], "token": "/review"}
                ],
            },
        )["job_id"]
        row = instance.job(identity, job)
        instance.provider_inflight["codex"] = 1
        adapter = AsyncMock(return_value={"answer": "done"})
        try:
            with patch("adapters.run_native", adapter):
                task = asyncio.create_task(instance.infer(row, json.loads(row["payload"])))
                await asyncio.wait_for(prepared.wait(), 2)
                await asyncio.sleep(0)
                assert not task.done()
                if change == "edit":
                    path.write_text('name="review"\ndeveloper_instructions="Changed"\n')
                elif change == "delete":
                    path.unlink()
                async with instance.provider_slots["codex"]:
                    instance.provider_inflight["codex"] = 0
                    instance.provider_slots["codex"].notify_all()
                if change == "valid":
                    assert (await task)["answer"] == "done"
                    assert adapter.await_count == 1
                else:
                    with pytest.raises(APIError):
                        await task
                    assert adapter.await_count == 0
                assert instance.provider_inflight["codex"] == 0
                assert not instance.active_executors and not instance.job_tasks
        finally:
            await instance.effects.close()
            instance.db.close()

    asyncio.run(run())


@pytest.mark.parametrize("name", ["review:one", "review\\one", "review\tone", "review-one"])
@pytest.mark.parametrize("kind", ["agent", "workflow"])
def test_discovery_never_selects_nonportable_identity(tmp_path, monkeypatch, name, kind):
    from agent_service.invocations import Invocation, InvocationError

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    cfg = native_config(tmp_path / "state")
    root = tmp_path / "project"
    cfg["projects"]["p"]["root"] = str(root)
    cfg["services"]["codex"]["mode"] = "native"
    folder = root / (".codex/agents" if kind == "agent" else "workflows")
    folder.mkdir(parents=True)
    path = folder / (name + (".toml" if kind == "agent" else ".json"))
    if kind == "agent":
        path.write_text('name="review"\ndeveloper_instructions="Review"\n')
    else:
        path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "id": "review",
                    "steps": [
                        {
                            "id": "review",
                            "kind": "builtin",
                            "resource_id": "builtin/roles/review",
                            "args": "Review",
                            "backend": "codex",
                            "model": "gpt-6-astra",
                            "effort": "low",
                        }
                    ],
                }
            )
        )
    instance = Service(cfg)
    try:
        items = instance.resource_catalog(("a", cfg["clients"]["a"]), "p", "codex", "gpt-6-astra")[
            "items"
        ]
        item = next(item for item in items if item.get("source") == str(path))
        if name == "review-one":
            assert item["selectable"]
        try:
            Invocation(kind, item["resource_id"])
        except InvocationError:
            assert not item["selectable"]
            assert item.get("unavailable_reason") and item.get("preflight_hint")
    finally:
        instance.db.close()


@pytest.mark.parametrize("mode", ["expire", "cancel"])
def test_gate_cleanup_shutdown_is_recovered_on_restart(tmp_path, mode):
    async def run():
        instance, identity = execution_service(tmp_path)
        cfg = instance.config
        instance.db.execute("PRAGMA busy_timeout=1")
        cfg["approval_timeout_seconds"] = 0.03 if mode == "expire" else 30
        instance.conversation_repository.insert(
            "job",
            "p",
            "a",
            "running",
            time.time(),
            json.dumps({"backend": "local", "model": "installed-model"}),
            None,
            None,
            "job",
        )
        instance.db.commit()
        task = asyncio.create_task(
            instance.gates.ask(
                "job",
                {"question": "Continue?", "options": [{"id": "yes", "label": "Yes"}]},
                lambda kind, data: instance.event("job", kind, data),
            )
        )
        await asyncio.sleep(0.01)
        gate_id = next(iter(instance.approvals))
        lock = sqlite3.connect(tmp_path / "jobs.sqlite3")
        lock.execute("BEGIN IMMEDIATE")
        if mode == "cancel":
            task.cancel()
        await asyncio.sleep(0.1)
        assert not task.done()
        instance.stopping = True
        task.cancel()
        await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 1)
        assert not instance.approvals and not instance.gates.progress
        lock.rollback()
        lock.close()
        instance.db.close()
        restarted = Service(cfg)
        try:
            assert restarted.gates.repository.get(gate_id)["state"] == "invalidated"
            assert (
                sum(
                    r["type"] == "gate_invalidated"
                    for r in restarted.message_repository.all_events("job")
                )
                == 1
            )
        finally:
            restarted.db.close()

    asyncio.run(run())


def test_workflow_save_scan_limit_is_a_public_validation_error(tmp_path):
    instance, identity, row, data, plan = setup_run(tmp_path / "state")
    folder = tmp_path / "project/workflows"
    folder.mkdir(parents=True)
    for index in range(500):
        (folder / (str(index) + ".txt")).touch()
    try:
        with pytest.raises(workflows.WorkflowError, match="resource_scan_limit"):
            workflows.save_chain_as_workflow(
                {"root": str(folder.parent)}, plan, "saved", successful=True
            )
        assert not (folder / "saved.json").exists()
    finally:
        instance.db.close()


@pytest.mark.parametrize("mode", ["waiting", "expire", "cancel"])
def test_worker_shutdown_does_not_wait_for_gate_writer(tmp_path, mode):
    async def run():
        instance, identity = execution_service(tmp_path)
        cfg = instance.config
        cfg["approval_timeout_seconds"] = 0.03 if mode == "expire" else 30
        instance.db.execute("PRAGMA busy_timeout=1")
        instance.conversation_repository.insert(
            "job",
            "p",
            "a",
            "queued",
            time.time(),
            json.dumps({"backend": "local", "model": "installed-model"}),
            None,
            None,
            "job",
        )
        instance.db.commit()

        async def execute(row):
            return await instance.gates.ask(
                "job",
                {"question": "Continue?", "options": [{"id": "yes", "label": "Yes"}]},
                lambda kind, data: instance.event("job", kind, data),
            )

        instance.execute = execute
        worker = asyncio.create_task(instance.worker())
        async with asyncio.timeout(2):
            while not instance.approvals:
                await asyncio.sleep(0.001)
        gate_id = next(iter(instance.approvals))
        lock = sqlite3.connect(tmp_path / "jobs.sqlite3")
        lock.execute("BEGIN IMMEDIATE")
        if mode == "cancel":
            instance.cancel(identity, "job")
        if mode != "waiting":
            await asyncio.sleep(0.1)
        worker.cancel()
        await asyncio.wait_for(asyncio.gather(worker, return_exceptions=True), 1)
        assert not instance.approvals
        lock.rollback()
        lock.close()
        instance.db.close()
        restarted = Service(cfg)
        try:
            assert restarted.gates.repository.get(gate_id)["state"] == "invalidated"
        finally:
            restarted.db.close()

    asyncio.run(run())
