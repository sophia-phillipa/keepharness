import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from test_workspaces import config

from agent_service import maestro
from agent_service.app import Service
from agent_service.tools import ToolError


def setup_run(tmp_path):
    service = Service(config(tmp_path))
    identity = ("a", service.config["clients"]["a"])
    submitted = service.submit(
        identity,
        dict(project_id="p", backend="codex", model="gpt-6-astra", effort="low", prompt="Review"),
    )
    row = service.job(identity, submitted["job_id"])
    data = json.loads(row["payload"])
    plan = {
        "steps": [
            dict(
                role=role,
                backend="codex",
                model="gpt-6-astra",
                effort="low",
                task=role,
                reason="Declared",
            )
            for role in ("research", "review")
        ]
    }
    return service, identity, row, data, plan


def test_resume_uses_completed_prefix_after_failure(tmp_path):
    service, identity, row, data, plan = setup_run(tmp_path)
    with patch.object(
        service, "infer", AsyncMock(side_effect=[{"answer": "evidence"}, ToolError("failed")])
    ):
        with pytest.raises(ToolError):
            asyncio.run(maestro.execute_plan(service, row, data, plan))
    with patch.object(service, "infer", AsyncMock(return_value={"answer": "reviewed"})) as infer:
        result = asyncio.run(
            maestro.execute_plan(service, row, {**data, "_workflow_resume": True}, plan)
        )
    assert infer.await_count == 1
    assert "evidence" in infer.call_args.args[1]["prompt"]
    assert result["answer"] == "reviewed"
    service.db.close()


def test_rerun_child_reuses_prefix_and_new_execution_identity(tmp_path):
    service, identity, row, data, plan = setup_run(tmp_path)
    with patch.object(service, "infer", AsyncMock(return_value={"answer": "first"})):
        original = asyncio.run(maestro.execute_plan(service, row, data, plan))
    child_id = service.submit(identity, {**data, "prompt": "Review"})["job_id"]
    child = service.job(identity, child_id)
    with patch.object(service, "infer", AsyncMock(return_value={"answer": "second"})) as infer:
        result = asyncio.run(
            maestro.execute_plan(
                service,
                child,
                {**data, "_workflow_parent_job_id": row["id"], "_workflow_from_step": 2},
                plan,
            )
        )
    assert infer.await_count == 1
    assert (
        result["orchestration"]["steps"][0]["execution_id"]
        == original["orchestration"]["steps"][0]["execution_id"]
    )
    assert (
        result["orchestration"]["steps"][1]["execution_id"]
        != original["orchestration"]["steps"][1]["execution_id"]
    )
    service.db.close()


def test_changed_inputs_revoke_downstream_resolved_gate(tmp_path):
    service, identity, row, data, plan = setup_run(tmp_path)
    with patch.object(service, "infer", AsyncMock(return_value={"answer": "first"})):
        asyncio.run(maestro.execute_plan(service, row, data, plan))
    with service.db:
        service.gates.repository.create("stale", row["id"], {"step": 2, "publish": False})
        service.gates.repository.resolve("stale", "approve", "a", 1)
    with patch.object(service, "infer", AsyncMock(return_value={"answer": "new"})) as infer:
        asyncio.run(
            maestro.execute_plan(
                service,
                row,
                {**data, "workflow_inputs": {"changed": True}, "_workflow_resume": True},
                plan,
            )
        )
    assert infer.await_count == 2
    assert service.gates.repository.get("stale")["state"] == "invalidated"
    service.db.close()


def test_resume_after_service_restart_reuses_durable_checkpoint(tmp_path):
    service, identity, row, data, plan = setup_run(tmp_path)
    with patch.object(
        service, "infer", AsyncMock(side_effect=[{"answer": "retained"}, ToolError("failed")])
    ):
        with pytest.raises(ToolError):
            asyncio.run(maestro.execute_plan(service, row, data, plan))
    job_id = row["id"]
    service.db.close()

    service = Service(config(tmp_path))
    row = service.job(identity, job_id)
    with patch.object(service, "infer", AsyncMock(return_value={"answer": "resumed"})) as infer:
        asyncio.run(
            maestro.execute_workflow(
                service,
                row,
                {**data, "_workflow_resume": True},
                maestro.saved_plan(service, job_id),
            )
        )
    assert infer.await_count == 1
    assert "retained" in infer.call_args.args[1]["prompt"]
    service.db.close()


def test_retained_large_resource_bodies_do_not_count_as_declaration_size(tmp_path):
    service, identity, row, data, plan = setup_run(tmp_path)
    plan["steps"][0]["resource_snapshots"] = [{"_text": "x" * 150000, "_body": "x" * 150000}]
    with patch.object(service, "infer", AsyncMock(return_value={"answer": "done"})) as infer:
        asyncio.run(maestro.execute_workflow(service, row, data, plan))
    assert infer.await_count == 2
    assert "resource_snapshots" not in maestro.declaration(plan)["steps"][0]
    service.db.close()


def test_actual_workflow_whitespace_revision_invalidates_resume(tmp_path):
    from agent_service import resources, workflows

    service, identity, row, data, plan = setup_run(tmp_path)
    root = tmp_path / "project"
    folder = root / "workflows"
    folder.mkdir(parents=True)
    path = folder / "review.json"
    path.write_text(json.dumps(plan))
    service.config["projects"]["p"]["root"] = str(root)
    item = next(
        item
        for item in resources.discover(service.config, "p", "codex")["items"]
        if item["kind"] == "workflow"
    )
    plan = workflows.resolve_workflow(service.config, "p", item["resource_id"])
    with patch.object(service, "infer", AsyncMock(return_value={"answer": "first"})):
        asyncio.run(maestro.execute_workflow(service, row, data, plan))
    path.write_text(path.read_text() + "\n")
    changed = workflows.resolve_workflow(service.config, "p", item["resource_id"])
    with patch.object(
        service, "infer", AsyncMock(return_value={"answer": "new revision"})
    ) as infer:
        asyncio.run(
            maestro.execute_workflow(service, row, {**data, "_workflow_resume": True}, changed)
        )
    assert infer.await_count == 2
    service.db.close()


def test_recovery_restores_original_context_for_inputs_and_inference(tmp_path):
    service, identity, row, data, plan = setup_run(tmp_path)
    seen = []

    def context(current, payload):
        seen.append(payload.get("parent_job_id"))
        return []

    data["_workflow_context_parent_id"] = "original-context-parent"
    with (
        patch.object(service, "context_turns", context),
        patch.object(service, "infer", AsyncMock(return_value={"answer": "done"})) as infer,
    ):
        asyncio.run(maestro.execute_plan(service, row, data, plan))
    assert seen and set(seen) == {"original-context-parent"}
    assert all(
        call.args[1]["parent_job_id"] == "original-context-parent" for call in infer.call_args_list
    )
    service.db.close()
