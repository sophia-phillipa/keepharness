import asyncio
import copy
import json
from unittest.mock import AsyncMock, patch

import pytest
from test_workflow_resume_rerun import setup_run
from test_workspaces import config

from agent_service import maestro, workflows
from agent_service.errors import APIError
from agent_service.tools import ToolError


def test_A4_W1_workspace_coordinator_does_not_fallback_from_first_model(tmp_path):
    """Round-four's no-fallback contract preserves the chosen first model."""
    cfg = config(tmp_path)
    cfg["services"]["local"].update(
        models=["no-upload", "workspace-ready"],
        model_permissions={
            "no-upload": {"read": True, "upload": False},
            "workspace-ready": {"read": True, "upload": True},
        },
    )
    cfg["maestro_coordinator"] = {"backend": "local"}

    with pytest.raises(ToolError, match="maestro_coordinator_workspace_denied"):
        maestro.coordinator(cfg, "p", workspace=True)

    cfg["maestro_coordinator"]["model"] = "workspace-ready"
    assert maestro.coordinator(cfg, "p", workspace=True)["model"] == "workspace-ready"


@pytest.mark.parametrize("level", ["project", "global"])
def test_invalid_runtime_plan_policy_rejected_before_planner(tmp_path, level):
    """The runtime config accepts only the documented review/auto values."""

    async def scenario():
        service, identity, row, data, generated = setup_run(tmp_path)
        candidate = copy.deepcopy(service.config)
        target = candidate["projects"]["p"] if level == "project" else candidate
        target["maestro_plan_policy"] = "silent"
        try:
            with pytest.raises(APIError, match="runtime_config_invalid"):
                await service.apply_runtime_config(candidate)
        finally:
            service.db.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("level", ["project", "global"])
def test_invalid_plan_policy_blocks_before_planner_inference(tmp_path, level):
    """Even malformed effective config must not run the planner before rejection."""

    async def scenario():
        service, identity, row, data, generated = setup_run(tmp_path)
        target = service.config["projects"]["p"] if level == "project" else service.config
        target["maestro_plan_policy"] = "silent"
        try:
            with patch.object(
                service, "infer", AsyncMock(return_value={"answer": json.dumps(generated)})
            ) as infer:
                with pytest.raises(ToolError, match="invalid_maestro_plan_policy"):
                    await maestro.run(service, row, data)
                infer.assert_not_awaited()
        finally:
            service.db.close()

    asyncio.run(scenario())


def test_A4_W3_unsupported_nested_gate_fields_fail_closed():
    """Unknown gate behavior must not be accepted then silently replaced at runtime."""
    declaration = {
        "steps": [
            {
                "role": "review",
                "task": "Review",
                "reason": "Requested",
                "backend": "codex",
                "model": "fixture",
                "effort": "low",
                "gate": {
                    "question": "Continue?",
                    "options": ["continue", "deny"],
                    "on_timeout": "approve",
                },
            }
        ]
    }
    with pytest.raises(workflows.WorkflowError, match="workflow_invalid_gate"):
        workflows.validate_workflow(declaration)


@pytest.mark.parametrize("effect", [{}, {"integration": "synthetic"}, None])
def test_invalid_effect_rejected_before_execution(tmp_path, effect):
    service, identity, row, data, plan = setup_run(tmp_path)
    service.config["approval_timeout_seconds"] = 0.01
    plan["steps"] = [plan["steps"][0]]
    plan["steps"][0].update(publish=True, effect=effect)
    try:
        with patch.object(service, "infer", AsyncMock()) as infer:
            with pytest.raises(workflows.WorkflowError, match="workflow_invalid_effect"):
                asyncio.run(maestro.execute_plan(service, row, data, plan))
            infer.assert_not_awaited()
        assert not service.effects.for_job(row["id"])
        assert not service.gates.repository.for_job(row["id"])
    finally:
        service.db.close()


def test_global_auto_cannot_bypass_project_plan_review(tmp_path):
    async def scenario():
        service, identity, row, data, generated = setup_run(tmp_path)
        service.config["maestro_plan_policy"] = "auto"
        try:
            with (
                patch(
                    "agent_service.maestro.plan",
                    AsyncMock(
                        return_value={
                            "plan": {**generated, "planner_revision": "fixture"},
                            "planning_result": {},
                            "coordinator": {},
                        }
                    ),
                ),
                patch("agent_service.maestro.execute_plan", AsyncMock(return_value={})),
                patch.object(
                    service.gates, "ask", AsyncMock(return_value={"approved": False})
                ) as gate,
            ):
                await maestro.run(service, row, data)
                gate.assert_awaited_once()
        finally:
            service.db.close()

    asyncio.run(scenario())
