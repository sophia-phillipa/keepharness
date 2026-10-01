import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from test_workflow_resume_rerun import setup_run

from agent_service import maestro, workflows
from agent_service.errors import ToolError


def test_workflow_inputs_reach_adapter(tmp_path):
    service, identity, row, data, plan = setup_run(tmp_path)
    service.config["codex"] = {}
    data["workflow_inputs"] = {"ticket": "A4-SYNTHETIC-TICKET-812"}
    plan["steps"] = [plan["steps"][0]]
    plan["steps"][0]["inputs"] = {
        "type": "object",
        "required": ["ticket"],
        "properties": {"ticket": {"type": "string"}},
    }
    try:
        with (
            patch("adapters.run_native", AsyncMock(return_value={"answer": "done"})) as native,
            patch.object(service, "quota", AsyncMock(return_value=None)),
        ):
            asyncio.run(maestro.execute_plan(service, row, data, plan))
        assert native.await_count == 1
        prompt = native.call_args.args[1]
        print("ADAPTER PROMPT:", prompt)
        assert "A4-SYNTHETIC-TICKET-812" in prompt, (
            "validated workflow_inputs never reaches adapter prompt"
        )
    finally:
        service.db.close()


@pytest.mark.parametrize(
    "requires",
    [
        {"permissions": ["write"]},
        {"integrations": ["missing"]},
        {"operations": ["missing.operation"]},
        {"mode": "sdk"},
    ],
)
def test_generated_plan_requires_enforced(tmp_path, requires):
    service, identity, row, data, plan = setup_run(tmp_path)
    plan["steps"] = [plan["steps"][0]]
    plan["steps"][0]["requires"] = requires
    # The same candidate must reject this declaration as a workflow.
    with pytest.raises(ToolError, match="workflow_requirement_denied"):
        workflows.validate_workflow(plan, maestro.candidates(service.config, "p"), [])
    data["maestro_plan_policy"] = "auto"
    try:
        with patch.object(
            service,
            "infer",
            AsyncMock(side_effect=[{"answer": json.dumps(plan)}, {"answer": "executed"}]),
        ) as infer:
            with pytest.raises(ToolError, match="workflow_requirement_denied"):
                asyncio.run(maestro.run(service, row, data))
        print("INFER CALLS:", infer.await_count)
        assert infer.await_count == 1, "step executed despite unsatisfied declared requires.write"
    finally:
        service.db.close()


def test_null_schema_rejected_before_inference(tmp_path):
    service, identity, row, data, plan = setup_run(tmp_path)
    plan["steps"] = [plan["steps"][0]]
    plan["steps"][0]["outputs"] = {"type": "null"}
    try:
        with (
            patch.object(
                service, "infer", AsyncMock(return_value={"answer": "not structured JSON"})
            ),
            patch.object(
                service.gates,
                "ask",
                AsyncMock(return_value={"approved": True, "choice": "approve"}),
            ) as gate,
        ):
            from agent_service.workflows import WorkflowError

            with pytest.raises(WorkflowError, match="workflow_invalid_schema"):
                asyncio.run(maestro.execute_plan(service, row, data, plan))
            service.infer.assert_not_awaited()
        gate.assert_not_awaited()
    finally:
        service.db.close()


def test_example_fenced_result_does_not_execute_conditional_step(tmp_path):
    service, identity, row, data, plan = setup_run(tmp_path)
    example = 'This is a code example, not a result:\n````markdown\n```harness-result\n{"ok": true}\n```\n````'
    plan["steps"][0]["id"] = "research"
    plan["steps"][1]["condition"] = {"from": "research.ok", "is": True}
    try:
        with (
            patch.object(
                service,
                "infer",
                AsyncMock(side_effect=[{"answer": example}, {"answer": "conditional step ran"}]),
            ) as infer,
            patch.object(
                service.gates,
                "ask",
                AsyncMock(return_value={"approved": True, "choice": "approve"}),
            ) as gate,
        ):
            asyncio.run(maestro.execute_plan(service, row, data, plan))
        print(
            "PARSED:",
            workflows.parse_result(example),
            "GATE CALLS:",
            gate.await_count,
            "INFER CALLS:",
            infer.await_count,
        )
        assert gate.await_count == 1, (
            "nested documentation code sample consumed as executable condition result"
        )
    finally:
        service.db.close()


def test_completed_steps_reflect_current_workflow_revision(tmp_path):
    service, identity, row, data, plan = setup_run(tmp_path)
    folder = tmp_path / "project" / "workflows"
    folder.mkdir(parents=True)
    path = folder / "review.json"
    path.write_text(json.dumps(plan))
    service.config["projects"]["p"]["root"] = str(folder.parent)
    resource = "project/p/workflows/review.json"
    try:
        workflow = workflows.resolve_workflow(service.config, "p", resource)
        with patch.object(service, "infer", AsyncMock(return_value={"answer": "done"})):
            result = asyncio.run(maestro.execute_workflow(service, row, data, workflow))
        service.finish(row["id"], "completed", result)
        row = service.job(identity, row["id"])
        before = service.workflow_completed_steps(row)
        path.write_text(path.read_text() + "\n")
        after = service.workflow_completed_steps(row)
        from starlette.applications import Starlette
        from starlette.testclient import TestClient

        from agent_service.routes.conversations import ROUTES

        app = Starlette(routes=ROUTES)
        app.state.service = service
        with TestClient(app, headers={"Authorization": "Bearer a"}) as client:
            response = client.get("/v1/jobs/" + row["id"])
            assert response.status_code == 200, response.text
            print(
                "HTTP RECOVERY PROJECTION:",
                {
                    key: response.json().get(key)
                    for key in ("workflow_checkpoint", "workflow_completed_steps")
                },
            )
            assert response.json()["workflow_completed_steps"] == after
        child_id = service.recover_workflow(identity, row["id"], {})["job_id"]
        with patch.object(service, "infer", AsyncMock(return_value={"answer": "reran"})) as infer:
            asyncio.run(service.execute(service.job(identity, child_id)))
        print(
            "ADVERTISED BEFORE:",
            before,
            "AFTER EDIT:",
            after,
            "ACTUAL REEXECUTED STEPS:",
            infer.await_count,
        )
        assert before == 2
        assert infer.await_count == 2
        assert after == 0, (
            "recovery projection claims reusable prefix after workflow revision changed"
        )
    finally:
        service.db.close()


@pytest.mark.parametrize("outer", ["````markdown", "~~~markdown"])
def test_only_top_level_structured_results_are_consumed(outer):
    valid = '```harness-result\n{"ok": true}\n```'
    assert workflows.parse_result(valid) == {"ok": True}
    assert workflows.parse_result(outer + "\n" + valid + "\n" + outer.split("markdown")[0]) is None
    assert workflows.parse_result(valid + "\n" + valid) is None
    assert workflows.parse_result('````harness-result\n{"ok":true}\n```') is None


@pytest.mark.parametrize("after_admission", [False, True])
def test_missing_workspace_source_is_actionable_before_work(tmp_path, after_admission):
    service, identity, row, data, plan = setup_run(tmp_path)
    workspace = service.root / "workspaces" / "workspace-a" / "work"
    workspace.mkdir(parents=True)
    source = workspace / "input.txt"
    source.write_text("synthetic input")
    with service.db:
        service.project_repository.add_workspace(
            "workspace-a", "p", "a", "synthetic", 1, json.dumps([{"path": "input.txt"}])
        )
        data["workspace_id"] = "workspace-a"
        service.conversation_repository.set_payload(row["id"], json.dumps(data))
    row = service.job(identity, row["id"])
    try:
        with patch.object(service, "infer", AsyncMock(return_value={"answer": "done"})):
            result = asyncio.run(maestro.execute_plan(service, row, data, plan))
        service.finish(row["id"], "completed", result)
        if after_admission:
            child = service.recover_workflow(identity, row["id"], {})["job_id"]
        source.unlink()
        with patch.object(service, "infer", AsyncMock()) as infer:
            with pytest.raises(ToolError, match="workflow_source_unavailable"):
                if after_admission:
                    asyncio.run(service.execute(service.job(identity, child)))
                else:
                    service.recover_workflow(identity, row["id"], {})
            assert infer.await_count == 0
    finally:
        service.db.close()


@pytest.mark.parametrize(
    "requires",
    [
        {"permissions": ["write"]},
        {"integrations": ["missing"]},
        {"operations": ["missing.operation"]},
        {"mode": "sdk"},
    ],
)
def test_manual_plan_requirements_preserve_pending_gate(tmp_path, requires):
    async def scenario():
        service, identity, row, data, plan = setup_run(tmp_path)
        task = None
        try:
            with patch.object(
                service, "infer", AsyncMock(return_value={"answer": json.dumps(plan)})
            ) as infer:
                task = asyncio.create_task(maestro.run(service, row, data))
                for _ in range(30):
                    if service.approvals or task.done():
                        break
                    await asyncio.sleep(0)
                gate_id = next(iter(service.approvals))
                plan["steps"][0]["requires"] = requires
                with pytest.raises(ToolError, match="workflow_requirement_denied"):
                    service.gates.resolve(gate_id, identity, {"choice": "approve", "plan": plan})
                assert service.gates.repository.get(gate_id)["state"] == "pending"
                assert infer.await_count == 1
                service.gates.resolve(gate_id, identity, {"choice": "deny"})
                await task
        finally:
            if task and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            service.db.close()

    asyncio.run(scenario())


def test_resource_step_inputs_reach_adapter_and_changed_resume(tmp_path, monkeypatch):
    from test_invocation_normalization import invocation_service

    service, identity = invocation_service(tmp_path, monkeypatch)
    service.config["codex"] = {}
    item = next(
        i
        for i in service.resource_catalog(identity, "p", "codex", "gpt-6-astra")["items"]
        if i["name"] == "writer"
    )
    try:
        job_id = service.submit(
            identity,
            dict(
                project_id="p",
                backend="codex",
                model="gpt-6-astra",
                effort="low",
                prompt="/writer report",
                resource_selections=[
                    dict(id=item["id"], revision=item["revision"], token="/writer")
                ],
                workflow_inputs={"ticket": "synthetic-first"},
            ),
        )["job_id"]
        row = service.job(identity, job_id)
        with (
            patch("adapters.run_native", AsyncMock(return_value={"answer": "done"})) as native,
            patch.object(service, "quota", AsyncMock(return_value=None)),
        ):
            payload = json.loads(row["payload"])
            declared = maestro.declared_plan(
                service.config, payload, service.selected_resources(payload)
            )
            result = asyncio.run(maestro.execute_plan(service, row, payload, declared))
            assert "synthetic-first" in native.call_args.args[1]
            service.finish(job_id, "completed", result)
            child_id = service.recover_workflow(
                identity, job_id, {"workflow_inputs": {"ticket": "synthetic-second"}}
            )["job_id"]
            asyncio.run(service.execute(service.job(identity, child_id)))
            assert "synthetic-second" in native.call_args.args[1]
            assert native.await_count == 2
    finally:
        service.db.close()
