import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from test_workflow_resume_rerun import setup_run

from agent_service import maestro, workflows


def test_unsupported_nested_gate_fields_fail_closed():
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

