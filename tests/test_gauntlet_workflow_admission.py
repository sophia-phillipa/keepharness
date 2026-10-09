"""Reject malformed declarations and preserve each selected occurrence."""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from test_workflow_resume_rerun import setup_run
from test_workflow_schema import workflow

from agent_service import maestro, workflows
from agent_service.errors import APIError
from agent_service.invocations import InvocationError, normalize_chips
from agent_service.resources import unfenced
from agent_service.routes import body


def test_selections_consume_distinct_token_occurrences():
    item = {"id": "project/p/agents/reviewer.md", "kind": "agent"}
    selection = {"id": item["id"], "token": "/reviewer"}
    with pytest.raises(InvocationError, match="resource_selection_missing"):
        normalize_chips("/reviewer check", [selection, selection], [item])
    result = normalize_chips("/reviewer first /reviewer second", [selection, selection], [item])
    assert [value.args for value in result] == ["first ", "second"]


@pytest.mark.parametrize("middle", ["```still-code", "  ```still-code", "``", "    ```"])
def test_invalid_fence_closers_cannot_hide_following_prose(middle):
    text = "```sh\n" + middle + "\n```\nUnsupported !`command`"
    assert "Unsupported !`command`" in unfenced(text)


def test_short_fence_cannot_close_longer_opener():
    assert unfenced("````sh\n```\n````\nUnsupported !`command`") == "Unsupported !`command`"


@pytest.mark.parametrize(
    "change",
    [
        {"publsih": True},
        {"gate": {"question": "Mode?", "options": ["quick", "thorough"]}},
        {"gate": {"question": "Mode?", "options": ["continue", "skip"], "multi_select": True}},
    ],
)
def test_unsupported_workflow_fields_or_gate_semantics_fail_admission(change):
    value = workflow()
    value["steps"][0].update(change)
    with pytest.raises(workflows.WorkflowError):
        workflows.validate_workflow(value)


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity", "1e999"])
def test_nonfinite_json_rejected_before_admission(constant):
    class Request:
        async def stream(self):
            yield ('{"workflow_inputs":{"score":' + constant + "}}").encode()

    with pytest.raises(APIError, match="invalid_json"):
        asyncio.run(body(Request()))


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_workflow_inputs_rejected_by_service(tmp_path, value):
    service, identity, row, data, _ = setup_run(tmp_path)
    try:
        before = service.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        with pytest.raises(APIError, match="invalid_workflow_inputs"):
            service.submit(identity, {**data, "workflow_inputs": {"nested": [value]}})
        assert service.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == before
    finally:
        service.db.close()


def test_rerun_start_must_exist_in_current_workflow(tmp_path):
    service, identity, row, data, _ = setup_run(tmp_path / "state")
    folder = tmp_path / "project" / "workflows"
    folder.mkdir(parents=True)
    service.config["projects"]["p"]["root"] = str(folder.parent)

    def definition(names):
        return {
            "id": "review",
            "version": 1,
            "steps": [
                {
                    "id": name,
                    "kind": "builtin",
                    "resource_id": "builtin/roles/" + name,
                    "args": "Run " + name,
                    "backend": "codex",
                    "model": "gpt-6-astra",
                    "effort": "low",
                }
                for name in names
            ],
        }

    path = folder / "review.json"
    path.write_text(json.dumps(definition(["one", "two", "three"])))
    plan = workflows.resolve_workflow(service.config, "p", "project/p/workflows/review.json")
    try:
        with patch.object(service, "infer", AsyncMock(return_value={"answer": "done"})):
            result = asyncio.run(maestro.execute_workflow(service, row, data, plan))
        with service.db:
            service.conversation_repository.set_result(row["id"], "completed", json.dumps(result))
        path.write_text(json.dumps(definition(["one"])))
        before = service.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        with pytest.raises(APIError, match="invalid_workflow_step"):
            service.recover_workflow(identity, row["id"], {"from_step": 3}, rerun=True)
        assert service.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == before
    finally:
        service.db.close()


@pytest.mark.parametrize("top_level", [False, True])
def test_source_workflows_reject_unknown_and_internal_metadata(top_level):
    value = workflow()
    if top_level:
        value["versoin"] = 1
    else:
        value["steps"][0]["resource_revision"] = "forged"
    with pytest.raises(workflows.WorkflowError):
        workflows.validate_workflow(value)


def test_recovery_visibility_uses_durable_checkpoint_in_both_read_routes(tmp_path):
    from types import SimpleNamespace

    from agent_service.routes.conversations import conversation, job

    service, identity, row, data, plan = setup_run(tmp_path)
    try:
        with service.db:
            service.conversation_repository.set_result(row["id"], "cancelled", "{}")
        for available in (False, True):
            if available:
                folder = service.root / "maestro" / row["id"]
                folder.mkdir(parents=True)
                (folder / "plan.json").write_text(json.dumps({"plan": plan}))
            request = SimpleNamespace(
                method="GET", path_params={"conversation": row["id"], "job": row["id"]}
            )
            response = asyncio.run(conversation(request, service, identity))
            assert json.loads(response.body)["turns"][0]["workflow_checkpoint"] is available
            response = asyncio.run(job(request, service, identity))
            assert json.loads(response.body)["workflow_checkpoint"] is available
    finally:
        service.db.close()
