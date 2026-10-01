import pytest
from test_workspaces import config

from agent_service import maestro
from agent_service.tools import ToolError


def test_default_stays_codex_and_explicit_local_is_supported(tmp_path):
    cfg = config(tmp_path)
    assert maestro.coordinator(cfg, "p")["backend"] == "codex"
    cfg["maestro_coordinator"] = {"backend": "local", "model": "installed-model"}
    assert maestro.coordinator(cfg, "p")["backend"] == "local"


def test_coordinator_never_falls_back_from_denied_explicit_choice(tmp_path):
    cfg = config(tmp_path)
    cfg["maestro_coordinator"] = {"backend": "local", "model": "missing"}
    with pytest.raises(ToolError, match="maestro_coordinator_unavailable"):
        maestro.coordinator(cfg, "p")


def test_default_unavailable_preserves_legacy_error(tmp_path):
    cfg = config(tmp_path)
    cfg["services"]["codex"]["enabled"] = False
    with pytest.raises(ToolError, match="maestro_requires_enabled_codex_for_project"):
        maestro.coordinator(cfg, "p")


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


@pytest.mark.parametrize("scope", ["project", "run"])
def test_explicit_auto_policy_skips_only_generated_plan_review(tmp_path, scope):
    import asyncio
    import json
    from unittest.mock import AsyncMock, patch

    from test_workflow_resume_rerun import setup_run

    service, identity, row, data, plan = setup_run(tmp_path)
    if scope == "project":
        service.config["projects"]["p"]["maestro_plan_policy"] = "auto"
    else:
        data["maestro_plan_policy"] = "auto"
    with (
        patch.object(
            service,
            "infer",
            AsyncMock(
                side_effect=[{"answer": json.dumps(plan)}, {"answer": "one"}, {"answer": "two"}]
            ),
        ) as infer,
        patch.object(service.gates, "ask", AsyncMock()) as gate,
    ):
        result = asyncio.run(maestro.run(service, row, data))
    gate.assert_not_called()
    assert infer.await_count == 3
    assert len(result["orchestration"]["plan"]["planner_revision"]) == 64
    service.db.close()
