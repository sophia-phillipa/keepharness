import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from test_workflow_resume_rerun import setup_run
from test_workspaces import config

from agent_service import maestro
from agent_service.app import Service


def test_a4_w6_missing_workflow_dependency_is_not_advertised_selectable(tmp_path):
    root = tmp_path / "project"
    (root / "workflows").mkdir(parents=True)
    (root / "workflows" / "missing.json").write_text(
        json.dumps(
            {
                "id": "missing-resource",
                "steps": [
                    {
                        "id": "review",
                        "kind": "skill",
                        "resource_id": "project/p/.agents/skills/missing/SKILL.md",
                        "args": "check",
                        "backend": "codex",
                        "model": "gpt-6-astra",
                        "effort": "low",
                    }
                ],
            }
        )
    )
    settings = config(tmp_path / "state")
    settings["projects"]["p"]["root"] = str(root)
    service = Service(settings)
    identity = ("a", settings["clients"]["a"])
    try:
        item = next(
            value
            for value in service.resource_catalog(identity, "p", "codex", "gpt-6-astra")["items"]
            if value["kind"] == "workflow"
        )
        assert item["selectable"] is False
        assert "resource is missing or unavailable" in item["unavailable_reason"]
    finally:
        service.db.close()


def test_a4_w7_condition_skip_does_not_emit_executed_invocation(tmp_path):
    service, _, row, data, plan = setup_run(tmp_path)
    plan["steps"][0]["id"] = "probe"
    plan["steps"][1]["condition"] = {"from": "probe.ok", "is": True}
    try:
        with patch.object(
            service,
            "infer",
            AsyncMock(return_value={"answer": '```harness-result\n{"ok":false}\n```'}),
        ) as infer:
            result = asyncio.run(maestro.execute_plan(service, row, data, plan))
        assert infer.await_count == 1
        assert result["orchestration"]["steps"][1]["outcome"] == "skipped"
        events = service.message_repository.all_events(row["id"])
        second_invocations = [
            event
            for event in events
            if event["type"] in ("invocation_started", "invocation_completed")
            and json.loads(event["data"]).get("index") == 2
        ]
        assert second_invocations == []
        terminal = [
            json.loads(event["data"])
            for event in events
            if event["type"] == "maestro_step_completed" and json.loads(event["data"])["index"] == 2
        ]
        assert terminal[0]["outcome"] == "skipped"
        from agent_service.spans import events_to_spans

        spans = events_to_spans(row, events)
        assert (
            next(span for span in spans if span["span_id"] == terminal[0]["execution_id"])["attrs"][
                "outcome"
            ]
            == "skipped"
        )
        with patch.object(service, "infer", AsyncMock()) as resumed:
            repeated = asyncio.run(
                maestro.execute_plan(service, row, {**data, "_workflow_resume": True}, plan)
            )
        assert resumed.await_count == 0
        assert repeated["orchestration"]["steps"][1]["outcome"] == "skipped"
    finally:
        service.db.close()


@pytest.mark.parametrize("remove", [False, True])
def test_save_checks_current_dependencies(tmp_path, monkeypatch, remove):
    from test_invocation_normalization import invocation_service

    from agent_service.workflows import WorkflowError

    service, identity = invocation_service(tmp_path, monkeypatch)
    try:
        items = service.resource_catalog(identity, "p", "codex", "gpt-6-astra")["items"]
        refs = [
            {"id": item["id"], "revision": item["revision"], "token": "/" + item["name"]}
            for item in items
            if item["name"] in ("reviewer", "writer")
        ]
        job = service.submit(
            identity,
            dict(
                project_id="p",
                backend="codex",
                model="gpt-6-astra",
                effort="low",
                prompt="/reviewer inspect\n/writer report",
                resource_selections=refs,
            ),
        )["job_id"]
        with patch.object(service, "infer", AsyncMock(return_value={"answer": "done"})):
            result = asyncio.run(service.execute(service.job(identity, job)))
        service.finish(job, "completed", result)
        if remove:
            (tmp_path / "project/.codex/agents/writer.toml").unlink()
            with pytest.raises(WorkflowError, match="workflow_resource_unavailable"):
                service.save_workflow(identity, job, "saved")
            assert not (tmp_path / "project/workflows/saved.json").exists()
        else:
            assert service.save_workflow(identity, job, "saved")["id"] == "saved"
    finally:
        service.db.close()
