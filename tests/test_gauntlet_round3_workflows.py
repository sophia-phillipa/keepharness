"""Round-three planning isolation, workflow recovery and invocation contracts."""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from test_effect_executor import configure_effects, request
from test_invocation_normalization import invocation_service
from test_maestro_plan_approval import proposed_plan
from test_workflow_resume_rerun import setup_run
from test_workspaces import config

from agent_service import maestro
from agent_service.app import Service
from agent_service.effect_mcp import prepare_request
from agent_service.spans import events_to_spans
from agent_service.tools import ToolError


def test_planner_must_not_prepare_a_publication_surviving_plan_discard(tmp_path):
    async def scenario():
        cfg = configure_effects(config(tmp_path))
        cfg["codex"] = {"binary": "unused-synthetic"}
        service = Service(cfg)
        identity = ("a", cfg["clients"]["a"])
        service.effects.credentials.set(
            "synthetic", {"email": "fixture@example.invalid", "token": "synthetic-only"}
        )
        jid = service.submit(
            identity,
            {
                "project_id": "p",
                "backend": "maestro",
                "model": "auto",
                "effort": "auto",
                "prompt": "Plan a synthetic report",
            },
        )["job_id"]
        service.conversation_repository.set_running(jid)
        row = service.job(identity, jid)
        prepared = []

        async def provider(
            backend_config, prompt, progress, project, model, effort, session, backend, approve
        ):
            assert project == {"permissions": {}}, project
            capability = backend_config.get("_effect_capability")
            print("PLANNER_PERMISSIONS", project, "HAS_EFFECT_CAPABILITY", bool(capability))
            if capability:
                prepared.append(await prepare_request(capability, request()))
            return {"answer": json.dumps(proposed_plan())}

        with (
            patch("adapters.run_native", side_effect=provider),
            patch.object(service, "quota", AsyncMock(return_value=None)),
            patch.object(
                service.effects.driver,
                "create",
                AsyncMock(return_value=("done", {"issue_key": "TEST-SYNTHETIC"})),
            ) as publish,
        ):
            task = asyncio.create_task(service.execute(row))
            try:
                async with asyncio.timeout(3):
                    while True:
                        review = [
                            g
                            for g in service.gates.repository.for_job(jid)
                            if json.loads(g["spec"]).get("kind") == "maestro_plan"
                        ]
                        if review or task.done():
                            break
                        await asyncio.sleep(0.001)
                assert review, task.exception() if task.done() else "no review"
                service.gates.resolve(review[0]["gate_id"], identity, {"choice": "deny"})
                result = await task
                service.finish(jid, "completed", result)
                print("PLAN_RESULT", result["answer"])
                print(
                    "PREPARED",
                    len(prepared),
                    "EFFECT_STATES",
                    [e["status"] for e in service.effects.for_job(jid)],
                )
                if prepared:
                    effect = service.effects.for_job(jid)[0]
                    service.gates.resolve(effect["gate_id"], identity, {"choice": "approve"})
                    await service.effects.tasks[effect["effect_id"]]
                    print(
                        "AFTER_DISCARD_DRIVER_CALLS",
                        publish.await_count,
                        "STATUS",
                        service.effects.get(effect["effect_id"])["status"],
                    )
                assert not prepared, (
                    "Planning-only provider received a usable publication capability; effect survives Discard plan"
                )
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                await service.effects.close()
                service.db.close()

    asyncio.run(scenario())


def test_gate_required_event_failure_cleans_pending_gate(tmp_path):
    async def scenario():
        cfg = config(tmp_path)
        cfg["approval_timeout_seconds"] = 0.01
        service = Service(cfg)
        identity = ("a", cfg["clients"]["a"])
        jid = service.submit(
            identity,
            {"project_id": "p", "backend": "codex", "model": "gpt-6-astra", "prompt": "Synthetic"},
        )["job_id"]

        def progress(kind, data):
            if kind == "gate_required":
                raise OSError("synthetic event-write failure")

        try:
            with pytest.raises(OSError):
                await service.gates.ask(
                    jid,
                    {"question": "Continue?", "options": [{"id": "yes", "label": "Yes"}]},
                    progress,
                )
            await asyncio.sleep(0.03)
            rows = service.gates.repository.for_job(jid)
            print(
                "PENDING_AFTER_ASK_EXIT",
                len(service.approvals),
                "DURABLE_STATES",
                [g["state"] for g in rows],
            )
            assert not service.approvals, (
                "Failed gate publication leaks pending future with no waiter or expiry timer"
            )
            assert not service.gates.progress
            assert all(g["state"] == "invalidated" for g in rows)
        finally:
            service.db.close()

    asyncio.run(scenario())


def test_publication_dispatch_counts_as_active_after_human_approval(tmp_path):
    from test_workflow_effect import effect_run, pending

    from agent_service.services.budgets import RuntimeBudget

    async def scenario():
        service, identity, row, data, plan = effect_run(tmp_path)
        budget = RuntimeBudget()
        service.runtime_budgets[row["id"]] = budget
        observed = []

        async def driver(*args):
            observed.append({"wait_depth_at_dispatch": budget.wait_depth})
            await asyncio.sleep(0.12)
            return "done", {"issue_key": "TEST-SYNTHETIC"}

        async def run():
            async with budget.limit(0.05):
                return await maestro.execute_plan(service, row, data, plan)

        with (
            patch.object(service, "infer", AsyncMock(return_value={"answer": "prepared"})),
            patch.object(service.effects.driver, "create", side_effect=driver),
        ):
            task = asyncio.create_task(run())
            try:
                gate_id = await pending(service, task)
                await asyncio.sleep(0.08)
                assert not task.done()
                service.gates.resolve(gate_id, identity, {"choice": "approve"})
                # Human decision delay may exceed active time; dispatch may not.
                with pytest.raises(TimeoutError):
                    await task
                assert service.effects.for_job(row["id"])[0]["status"] == "unknown"
                assert observed[0]["wait_depth_at_dispatch"] == 0, (
                    "Approved external dispatch is still charged as human wait, suspending the active deadline"
                )
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                await service.effects.close()
                service.db.close()

    asyncio.run(scenario())


def test_direct_invocation_execution_attribution_reaches_root_span(tmp_path, monkeypatch):
    service, identity = invocation_service(tmp_path, monkeypatch)
    item = next(
        item
        for item in service.resource_catalog(identity, "p", "codex", "gpt-6-astra")["items"]
        if item["name"] == "reviewer"
    )
    job_id = service.submit(
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
    )["job_id"]
    row = service.job(identity, job_id)
    service.event(job_id, "running", {})
    with patch.object(service, "_run_inference", AsyncMock(return_value={"answer": "done"})):
        result = asyncio.run(service.infer(row, json.loads(row["payload"])))
    service.finish(job_id, "completed", result)

    events = service.message_repository.all_events(job_id)
    assert any(event["type"] == "invocation_started" for event in events)
    root = events_to_spans(service.job(identity, job_id), events)[0]
    assert root["attrs"].get("gen_ai.agent.name") == "reviewer", {
        "root_name": root["name"],
        "effort": root["attrs"].get("effort"),
        "agent": root["attrs"].get("gen_ai.agent.name"),
    }
    assert root["name"] == "reviewer"
    assert root["attrs"]["effort"] == "low"
    service.db.close()


def test_public_recovery_chain_retains_done_effect_guard(tmp_path):
    service, identity, source, data, plan = setup_run(tmp_path)
    with patch.object(service, "infer", AsyncMock(return_value={"answer": "first"})):
        original = asyncio.run(maestro.execute_plan(service, source, data, plan))
    first_execution = original["orchestration"]["steps"][0]["execution_id"]
    blank = json.dumps({})
    with service.db:
        service.db.execute(
            "INSERT INTO effects(effect_id,job_id,gate_id,status,request,artifact,binding,contract,execution_id,enforcement) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                "published-in-source",
                source["id"],
                "published-gate",
                "done",
                blank,
                blank,
                blank,
                blank,
                first_execution,
                "unenforced",
            ),
        )
        service.conversation_repository.set_result(source["id"], "completed", json.dumps(original))

    child_id = service.recover_workflow(identity, source["id"], {})["job_id"]
    child = service.job(identity, child_id)
    assert json.loads(child["payload"])["_workflow_parent_job_id"] == source["id"]
    with patch.object(service, "infer", AsyncMock()) as infer:
        resumed = asyncio.run(service.execute(child))
    infer.assert_not_awaited()
    with service.db:
        service.conversation_repository.set_result(child_id, "completed", json.dumps(resumed))

    grandchild_id = service.recover_workflow(
        identity,
        child_id,
        {"from_step": 2, "workflow_inputs": {"revision": 2}},
        rerun=True,
    )["job_id"]
    grandchild = service.job(identity, grandchild_id)
    payload = json.loads(grandchild["payload"])
    assert payload["_workflow_parent_job_id"] == child_id
    assert payload["_workflow_from_step"] == 2
    with patch.object(
        service, "infer", AsyncMock(return_value={"answer": "unexpected replay"})
    ) as infer:
        failure = None
        try:
            asyncio.run(service.execute(grandchild))
        except ToolError as error:
            failure = str(error)
        assert failure == "workflow_published_step_requires_explicit_rerun", {
            "failure": failure,
            "inference_calls": infer.await_count,
            "source_effect": "done",
            "requested_from_step": 2,
        }
    infer.assert_not_awaited()
    service.db.close()


@pytest.mark.parametrize("fence", ["```", "~~~~"])
def test_command_expansion_keeps_fenced_examples(fence):
    from agent_service.resources import prepare_prompt

    command = {"kind": "command", "name": "review", "_body": "EXPANDED[$ARGUMENTS]"}
    prompt = f"Example:\n{fence}text\n/review literal\n{fence}\n/review actual"
    assert (
        prepare_prompt(prompt, [command])
        == f"Example:\n{fence}text\n/review literal\n{fence}\nEXPANDED[actual]"
    )


@pytest.mark.parametrize("size", [149999, 150000, 150001, 160000])
def test_command_discovery_matches_executable_size(tmp_path, monkeypatch, size):
    from agent_service import resources
    from agent_service.errors import APIError

    root = tmp_path / "project"
    commands = root / ".codex" / "prompts"
    commands.mkdir(parents=True)
    (commands / "huge.md").write_text("H" * size)
    monkeypatch.setattr(resources, "roots", lambda *_: (tmp_path / "empty-home", []))
    cfg = config(tmp_path / "state")
    cfg["projects"]["p"]["root"] = str(root)
    service = Service(cfg)
    identity = ("a", cfg["clients"]["a"])
    try:
        item = next(
            x
            for x in service.resource_catalog(identity, "p", "codex", "gpt-6-astra")["items"]
            if x["name"] == "huge"
        )
        assert item["selectable"] is (size <= 150000)
        payload = {
            "project_id": "p",
            "backend": "codex",
            "model": "gpt-6-astra",
            "prompt": "/huge",
            "resource_selections": [
                {"id": item["id"], "revision": item["revision"], "token": "/huge"}
            ],
        }
        if size <= 150000:
            assert service.submit(identity, payload)["job_id"]
        else:
            assert "limit" in item["unavailable_reason"].lower()
            with pytest.raises(APIError):
                service.submit(identity, payload)
            assert service.db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0
    finally:
        service.db.close()


@pytest.mark.parametrize("completed", [0, 1])
def test_recovery_api_reports_valid_completed_prefix(tmp_path, completed):
    from types import SimpleNamespace

    from agent_service.routes.conversations import conversation, job

    service, identity, row, data, plan = setup_run(tmp_path)
    try:
        with patch.object(
            service,
            "infer",
            AsyncMock(side_effect=[{"answer": "done"}] * completed + [ToolError("failed")]),
        ):
            with pytest.raises(ToolError):
                asyncio.run(maestro.execute_plan(service, row, data, plan))
        service.finish(row["id"], "failed", {"error": "synthetic"})
        request = SimpleNamespace(
            method="GET", path_params={"conversation": row["id"], "job": row["id"]}
        )
        for route in [conversation, job]:
            result = json.loads(asyncio.run(route(request, service, identity)).body)
            result = result["turns"][0] if "turns" in result else result
            assert result["workflow_checkpoint"] is True
            assert result["workflow_completed_steps"] == completed
        assert service.recover_workflow(identity, row["id"], {})["job_id"]
    finally:
        service.db.close()


def test_planning_does_not_enter_catalog_runtime_or_hooks(tmp_path):
    from types import SimpleNamespace

    from test_catalog_hooks import executable

    from agent_service.secret_vault import injected_environment

    service = Service(config(tmp_path / "state"))
    service.config["services"]["codex"]["permissions"]["hooks"] = True
    hook = executable(tmp_path / "hook", "touch planning-hook-ran\n")
    runtime = {
        "environment": {"SYNTHETIC_TOKEN": "planning-private"},
        "allowed_hooks": [hook],
        "cwd": str(tmp_path),
        "catalogs": [],
    }
    plan = SimpleNamespace(
        row={"id": "planning", "project": "p"},
        data={"_planning_only": True, "model": "gpt-6-astra"},
        backend="codex",
        execution_mode="native",
        selected_resources=[],
    )

    async def transport(*_):
        assert not injected_environment()
        assert not (tmp_path / "planning-hook-ran").exists()
        return {"answer": "synthetic plan"}

    try:
        with (
            patch("agent_service.catalog_manifest.runtime_for_project", return_value=runtime),
            patch.object(service, "_run_transport_inference", side_effect=transport),
        ):
            asyncio.run(service._run_inference(plan))
    finally:
        service.db.close()


def test_explicit_rerun_acknowledges_older_published_prefix(tmp_path):
    service, identity, source, data, plan = setup_run(tmp_path)
    try:
        with patch.object(service, "infer", AsyncMock(return_value={"answer": "first"})):
            original = asyncio.run(maestro.execute_plan(service, source, data, plan))
        execution = original["orchestration"]["steps"][0]["execution_id"]
        blank = json.dumps({})
        service.db.execute(
            "INSERT INTO effects(effect_id,job_id,gate_id,status,request,artifact,binding,contract,execution_id,enforcement) VALUES(?,?,?,'done',?,?,?,?,?,?)",
            (
                "old-publication",
                source["id"],
                "old-gate",
                blank,
                blank,
                json.dumps({"arguments_digest": "fixture", "artifact_digest": "fixture"}),
                json.dumps({"endpoint": "https://fixture.invalid"}),
                execution,
                "unenforced",
            ),
        )
        service.finish(source["id"], "completed", original)
        child_id = service.recover_workflow(identity, source["id"], {"from_step": 1}, rerun=True)[
            "job_id"
        ]
        with patch.object(
            service, "infer", AsyncMock(return_value={"answer": "acknowledged rerun"})
        ):
            child_result = asyncio.run(service.execute(service.job(identity, child_id)))
        service.finish(child_id, "completed", child_result)
        next_id = service.recover_workflow(
            identity, child_id, {"from_step": 2, "workflow_inputs": {"revision": 2}}, rerun=True
        )["job_id"]
        with patch.object(
            service, "infer", AsyncMock(return_value={"answer": "new baseline"})
        ) as infer:
            asyncio.run(service.execute(service.job(identity, next_id)))
            assert infer.await_count == 2
    finally:
        service.db.close()


def test_active_deadline_cancels_all_dispatched_step_effects(tmp_path):
    from test_workflow_effect import effect_run

    from agent_service.services.budgets import RuntimeBudget

    async def scenario():
        service, identity, row, data, plan = effect_run(tmp_path)
        budget = RuntimeBudget()
        service.runtime_budgets[row["id"]] = budget

        async def infer(_row, payload):
            extra = request()
            extra["artifact"]["fields"]["summary"] = "Second synthetic publication"
            await service.effects.prepare(row["id"], extra, execution_id=payload["_execution_id"])
            return {"answer": "prepared"}

        async def driver(*_):
            await asyncio.sleep(1)
            return "done", {"issue_key": "TEST-1"}

        async def run():
            async with budget.limit(0.05):
                return await maestro.execute_plan(service, row, data, plan)

        try:
            with (
                patch.object(service, "infer", side_effect=infer),
                patch.object(service.effects.driver, "create", side_effect=driver),
            ):
                task = asyncio.create_task(run())
                async with asyncio.timeout(2):
                    while len(service.effects.for_job(row["id"])) < 2:
                        await asyncio.sleep(0.001)
                for effect in service.effects.for_job(row["id"]):
                    service.gates.resolve(effect["gate_id"], identity, {"choice": "approve"})
                with pytest.raises(TimeoutError):
                    await task
                assert [e["status"] for e in service.effects.for_job(row["id"])] == [
                    "unknown",
                    "unknown",
                ]
        finally:
            await service.effects.close()
            service.db.close()

    asyncio.run(scenario())
