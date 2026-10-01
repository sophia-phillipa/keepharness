"""Generated plans wait for a human and execute only the validated approved plan."""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from test_workspaces import config

from agent_service import maestro
from agent_service.app import Service
from agent_service.errors import ToolError


def proposed_plan(task="Inspect the change"):
    return {"steps": [{"role": "reviewer", "backend": "codex", "model": "gpt-6-astra",
                       "effort": "low", "task": task, "reason": "Review before delivery"}]}


@pytest.mark.parametrize("spoofed_revision", [None, "client-supplied-revision"])
def test_plan_edit_keeps_server_planner_revision(tmp_path, spoofed_revision):
    from test_workflow_resume_rerun import setup_run

    async def scenario():
        service, identity, row, data, declared = setup_run(tmp_path)
        with patch.object(
            service,
            "infer",
            AsyncMock(
                side_effect=[{"answer": json.dumps(proposed_plan())}, {"answer": "Reviewed"}]
            ),
        ):
            task = asyncio.create_task(maestro.run(service, row, data))
            for _ in range(20):
                if service.approvals or task.done():
                    break
                await asyncio.sleep(0)
            gate_id = next(iter(service.approvals))
            original = json.loads(service.gates.repository.get(gate_id)["spec"])["plan"]
            edited = {"steps": proposed_plan("Edited task")["steps"]}
            if spoofed_revision is not None:
                edited["planner_revision"] = spoofed_revision
            service.gates.resolve(gate_id, identity, {"choice": "approve", "plan": edited})
            result = await task
            assert (
                result["orchestration"]["plan"]["planner_revision"] == original["planner_revision"]
            )
            emitted = next(
                json.loads(event["data"])
                for event in service.message_repository.all_events(row["id"])
                if event["type"] == "maestro_plan"
            )
            assert emitted["planner_revision"] == original["planner_revision"]
        service.db.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("decision", ["approve", "deny", "expire", "cancel"])
def test_generated_plan_waits_and_only_approval_runs_steps(tmp_path, decision):
    async def scenario():
        service = Service(config(tmp_path))
        service.config["approval_timeout_seconds"] = 0.03 if decision == "expire" else 30
        identity = ("a", service.config["clients"]["a"])
        submitted = service.submit(identity, {"project_id": "p", "backend": "codex",
                                             "model": "gpt-6-astra", "effort": "low", "prompt": "Review"})
        row = service.job(identity, submitted["job_id"])
        data = json.loads(row["payload"])
        with patch.object(service, "infer", AsyncMock(side_effect=[
            {"answer": json.dumps(proposed_plan())}, {"answer": "Reviewed"}
        ])) as infer:
            task = asyncio.create_task(maestro.run(service, row, data))
            try:
                for _ in range(20):
                    if service.approvals or task.done():
                        break
                    await asyncio.sleep(0)
                assert not task.done(), "execution must wait for plan approval"
                assert infer.await_count == 1, "only the planning inference ran"
                gate_id = next(iter(service.approvals))
                spec = json.loads(service.gates.repository.get(gate_id)["spec"])
                assert spec["kind"] == "maestro_plan"
                assert spec["plan"]["steps"][0]["task"] == "Inspect the change"
                if decision == "cancel":
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task
                else:
                    if decision != "expire":
                        service.gates.resolve(gate_id, identity, {"choice": decision})
                    result = await asyncio.wait_for(task, 1)
                    assert infer.await_count == (2 if decision == "approve" else 1)
                    assert result["answer"] == ("Reviewed" if decision == "approve" else
                                                "The Maestro plan was not approved. No steps were run.")
                assert not service.approvals
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                service.db.close()

    asyncio.run(scenario())


def test_plan_edits_are_validated_before_consuming_the_approval(tmp_path):
    async def scenario():
        service = Service(config(tmp_path))
        identity = ("a", service.config["clients"]["a"])
        submitted = service.submit(identity, {"project_id": "p", "backend": "codex",
                                             "model": "gpt-6-astra", "effort": "low", "prompt": "Review"})
        row = service.job(identity, submitted["job_id"])
        data = json.loads(row["payload"])
        initial_plan = proposed_plan()
        initial_plan["steps"].append({**initial_plan["steps"][0], "task": "Review another area"})
        with patch.object(service, "infer", AsyncMock(side_effect=[
            {"answer": json.dumps(initial_plan)}, {"answer": "Revised review"}
        ])) as infer:
            task = asyncio.create_task(maestro.run(service, row, data))
            try:
                for _ in range(20):
                    if service.approvals or task.done():
                        break
                    await asyncio.sleep(0)
                assert not task.done()
                gate_id = next(iter(service.approvals))
                forbidden = proposed_plan()
                forbidden["steps"][0]["model"] = "not-allowed"
                with pytest.raises(ToolError, match="maestro_model_or_effort_denied"):
                    service.gates.resolve(gate_id, identity, {"choice": "approve", "plan": forbidden})
                assert service.gates.repository.get(gate_id)["state"] == "pending"
                assert not task.done()
                changed = json.loads(service.gates.repository.get(gate_id)["spec"])["plan"]
                changed["steps"] = changed["steps"][1:]
                changed["steps"][0]["task"] = "Review only the tests"
                service.gates.resolve(gate_id, identity, {"choice": "approve", "plan": changed})
                await asyncio.wait_for(task, 1)
                assert "Review only the tests" in infer.call_args_list[1].args[1]["prompt"]
                stored = json.loads(service.gates.repository.get(gate_id)["spec"])
                assert stored["plan"]["steps"][0]["task"] == "Review only the tests"
                assert stored["plan"]["steps"][0]["invocation"]["args"] == "Review only the tests"
                assert stored["plan"]["steps"][0]["invocation"]["order"] == 0
                events = service.message_repository.events_after(row["id"], 0)
                approved = next(json.loads(e["data"]) for e in events if e["type"] == "gate_resolved")
                assert approved["plan"] == stored["plan"]
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                service.db.close()

    asyncio.run(scenario())
