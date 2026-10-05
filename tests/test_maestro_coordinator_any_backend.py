import pytest
from test_workspaces import config

from agent_service import maestro
from agent_service.tools import ToolError


def test_operation_requirements_match_configured_supported_transport(tmp_path):
    from test_effect_executor import configure_effects

    from agent_service.workflows import validate_workflow

    cfg = configure_effects(config(tmp_path))
    steps = [
        {
            "role": "publish",
            "task": "Prepare",
            "reason": "Requested",
            "backend": "codex",
            "model": "gpt-6-astra",
            "effort": "low",
            "requires": {"operations": ["jira.create_issue"]},
        }
    ]
    validate_workflow({"steps": steps}, maestro.candidates(cfg, "p"))
    steps[0].update(backend="local", model="installed-model", effort="configured")
    with pytest.raises(ToolError, match="workflow_requirement_denied"):
        validate_workflow({"steps": steps}, maestro.candidates(cfg, "p"))


def usage(inputs, outputs, seconds):
    return {"input_tokens": inputs, "output_tokens": outputs, "inference_seconds": seconds}


def two_provider_plan(plan):
    plan["steps"][1].update(backend="local", model="installed-model", effort="configured")
    return plan


def test_a_maestro_run_reports_every_step_split_by_provider(tmp_path):
    import asyncio
    from unittest.mock import AsyncMock, patch

    from test_workflow_resume_rerun import setup_run

    service, identity, row, data, plan = setup_run(tmp_path)
    steps = [
        {
            "answer": "one",
            "backend": "codex",
            "metrics": usage(41000, 3900, 12.5),
            "context_usage": {"last": {"totalTokens": 41000}},
        },
        {
            "answer": "two",
            "backend": "local",
            "metrics": usage(1200, 80, 1.5),
            "context_usage": {"last": {"totalTokens": 1200}},
        },
    ]
    with patch.object(service, "infer", AsyncMock(side_effect=steps)):
        result = asyncio.run(
            maestro.execute_plan(
                service,
                row,
                data,
                two_provider_plan(plan),
            )
        )
    assert result["metrics"] == {
        "usage_scope": "turn",
        "input_tokens": 42200,
        "output_tokens": 3980,
        "inference_seconds": 14,
        "by_provider": {
            "codex": {"input_tokens": 41000, "output_tokens": 3900},
            "local": {"input_tokens": 1200, "output_tokens": 80},
        },
    }
    # Each step has its own session: the last step's context says nothing about the run.
    assert "context_usage" not in result
    assert result["answer"] == "two"
    service.db.close()


def test_a_maestro_run_without_any_reported_usage_stays_unreported(tmp_path):
    import asyncio
    from unittest.mock import AsyncMock, patch

    from test_workflow_resume_rerun import setup_run

    service, identity, row, data, plan = setup_run(tmp_path)
    with patch.object(service, "infer", AsyncMock(return_value={"answer": "x"})):
        result = asyncio.run(maestro.execute_plan(service, row, data, plan))
    assert "metrics" not in result
    service.db.close()


def test_steps_reused_from_a_checkpoint_were_not_spent_by_the_resumed_run(tmp_path):
    import asyncio
    from unittest.mock import AsyncMock, patch

    from test_workflow_resume_rerun import setup_run

    service, identity, row, data, plan = setup_run(tmp_path)
    first = {"answer": "evidence", "backend": "codex", "metrics": usage(500, 50, 1)}
    with patch.object(service, "infer", AsyncMock(side_effect=[first, ToolError("failed")])):
        with pytest.raises(ToolError):
            asyncio.run(maestro.execute_plan(service, row, data, plan))
    second = {"answer": "reviewed", "backend": "codex", "metrics": usage(700, 70, 2)}
    with patch.object(service, "infer", AsyncMock(return_value=second)):
        result = asyncio.run(
            maestro.execute_plan(service, row, {**data, "_workflow_resume": True}, plan)
        )
    assert result["metrics"]["input_tokens"] == 700
    assert result["orchestration"]["steps"][0]["metrics"]["input_tokens"] == 500
    service.db.close()
