import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient
from test_workflow_resume_rerun import setup_run

from agent_service import maestro, resources
from agent_service.routes.conversations import ROUTES


@pytest.mark.parametrize("kind", ["array", "string", "number", "integer", "boolean", "null"])
def test_nonobject_output_schema_rejected_at_admission(tmp_path, kind):
    from agent_service.workflows import WorkflowError, validate_workflow

    service, _, _, _, plan = setup_run(tmp_path)
    try:
        plan["steps"][0]["outputs"] = {"type": kind}
        with pytest.raises(WorkflowError, match="workflow_invalid_schema"):
            validate_workflow(maestro.declaration(plan))
    finally:
        service.db.close()


def test_resumed_workflow_projection_preserves_original_context(tmp_path):
    service, identity, first, data, plan = setup_run(tmp_path)
    try:
        service.finish(first["id"], "completed", {"answer": "Synthetic established context"})
        data.pop("execution_mode", None)
        original = service.submit(
            identity, {**data, "parent_job_id": first["id"], "prompt": "Review prior answer"}
        )["job_id"]
        row = service.job(identity, original)
        with patch.object(service, "infer", AsyncMock(return_value={"answer": "done"})):
            result = asyncio.run(
                maestro.execute_plan(service, row, json.loads(row["payload"]), plan)
            )
        service.finish(original, "completed", result)
        assert service.workflow_completed_steps(service.job(identity, original)) == 2
        child = service.recover_workflow(identity, original, {})["job_id"]
        with patch.object(
            service, "infer", AsyncMock(return_value={"answer": "unexpected reexecution"})
        ) as infer:
            result = asyncio.run(service.execute(service.job(identity, child)))
        service.finish(child, "completed", result)
        assert infer.await_count == 0
        app = Starlette(routes=ROUTES)
        app.state.service = service
        with TestClient(app, headers={"Authorization": "Bearer a"}) as client:
            response = client.get("/v1/jobs/" + child)
        assert response.status_code == 200
        print(
            "HTTP projection",
            json.dumps(
                {k: response.json()[k] for k in ("workflow_checkpoint", "workflow_completed_steps")}
            ),
            "actual reused steps",
            len(result["orchestration"]["steps"]),
        )
        assert response.json()["workflow_completed_steps"] == 2
    finally:
        service.db.close()


def test_s27_workflow_mode_requirement_uses_actual_execution_mode(tmp_path):
    from test_workflow_resume_rerun import setup_run

    from agent_service import maestro
    from agent_service.tools import ToolError

    service, _identity, row, data, plan = setup_run(tmp_path)
    plan["steps"] = [plan["steps"][0]]
    plan["steps"][0]["requires"] = {"mode": "native"}
    actual = {**data, "execution_mode": "scoped"}
    try:
        with patch.object(service, "infer", AsyncMock(return_value={"answer": "ran"})) as infer:
            with pytest.raises(ToolError, match="workflow_requirement_denied"):
                asyncio.run(maestro.execute_plan(service, row, actual, plan))
        infer.assert_not_awaited()
    finally:
        service.db.close()


def test_s27b_scoped_only_workflow_cannot_dispatch_native_adapter(tmp_path):
    from test_workflow_resume_rerun import setup_run

    from agent_service import maestro
    from agent_service.tools import ToolError

    service, _identity, row, data, plan = setup_run(tmp_path)
    service.config["services"]["codex"]["mode"] = "scoped"
    service.config["codex"] = {}
    plan["steps"] = [plan["steps"][0]]
    plan["steps"][0]["requires"] = {"mode": "scoped"}
    actual = {**data, "execution_mode": "native"}
    denied = False
    try:
        with (
            patch(
                "adapters.run_native", AsyncMock(return_value={"answer": "native-ran"})
            ) as native,
            patch.object(service, "quota", AsyncMock(return_value=None)),
        ):
            try:
                asyncio.run(maestro.execute_plan(service, row, actual, plan))
            except ToolError as error:
                denied = str(error) == "workflow_requirement_denied"
        assert denied and native.await_count == 0, (
            f"scoped-only step denial={denied}, native adapter calls={native.await_count}"
        )
    finally:
        service.db.close()


def put(root, relative, text):
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    return target


def config(project, catalog=None, *, backend="claude", trusted=True):
    value = {
        "projects": {
            "p": {
                "root": str(project),
                "permissions": {"delegate": True},
                "catalogs": ["demo"] if catalog else [],
            }
        },
        "services": {backend: {"mode": "native"}},
    }
    if catalog:
        value["catalogs"] = [
            {
                "id": "demo",
                "root": str(catalog),
                "kind": "folder",
                "trusted": trusted,
                "namespace": "demo",
            }
        ]
    return value


def test_s21b_namespaced_agent_keeps_source_argument_hint(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project, catalog = tmp_path / "project", tmp_path / "catalog"
    put(
        catalog,
        "agents/writer.md",
        "---\nname: writer\ndescription: Example: /writer <brief-file>\n---\nWrite",
    )
    item = resources.discover(config(project, catalog), "p", "claude")["items"][0]
    assert item["name"] == "demo--writer"
    assert item["argument_hint"] == "<brief-file>"


@pytest.mark.parametrize("reason", ["provider_capacity", "writable_root", "work_item"])
def test_queue_reason_projects_latest_safe_cause(tmp_path, reason):
    from agent_service.routes.activity import ROUTES as ACTIVITY_ROUTES
    from agent_service.spans import events_to_spans

    service, identity, row, data, plan = setup_run(tmp_path)
    try:
        service.event(row["id"], "queue_wait", {"reason": "conversation_parent"})
        service.event(row["id"], "queue_wait", {"reason": reason})
        app = Starlette(routes=ACTIVITY_ROUTES)
        app.state.service = service
        with TestClient(app, headers={"Authorization": "Bearer a"}) as client:
            response = client.get("/v1/activity")
        assert response.status_code == 200
        assert response.json()["jobs"][0]["wait_reason"] == reason
        spans = events_to_spans(dict(row), service.message_repository.all_events(row["id"]))
        queue = next(span for span in spans if span["kind"] == "queue_wait")
        assert queue["attrs"]["wait_reason"] == reason
        assert [event["name"] for event in queue["content"]] == ["queue_wait", "queue_wait"]
        service.event(row["id"], "queue_wait", {"reason": "conversation"})
        assert service.activity(identity)["jobs"][0]["wait_reason"] == "conversation_parent"
        spans = events_to_spans(dict(row), service.message_repository.all_events(row["id"]))
        assert (
            next(span for span in spans if span["kind"] == "queue_wait")["attrs"]["wait_reason"]
            == "conversation_parent"
        )
        service.event(row["id"], "queue_wait", {"reason": "unrecognized private detail"})
        assert service.activity(identity)["jobs"][0]["wait_reason"] == "queue"
        spans = events_to_spans(dict(row), service.message_repository.all_events(row["id"]))
        assert (
            next(span for span in spans if span["kind"] == "queue_wait")["attrs"]["wait_reason"]
            == "queue"
        )
        service.conversation_repository.set_running(row["id"])
        assert service.activity(identity)["jobs"][0]["wait_reason"] is None
    finally:
        service.db.close()


@pytest.mark.parametrize("mode", ["native", "scoped"])
def test_workflow_matching_effective_mode_reaches_dispatch(tmp_path, mode):
    service, _, row, data, plan = setup_run(tmp_path)
    service.config["services"]["codex"]["mode"] = "scoped" if mode == "native" else "native"
    plan["steps"] = plan["steps"][:1]
    plan["steps"][0]["requires"] = {"mode": mode}
    try:
        with patch.object(service, "infer", AsyncMock(return_value={"answer": "done"})) as infer:
            asyncio.run(maestro.execute_plan(service, row, {**data, "execution_mode": mode}, plan))
        assert infer.await_count == 1
        assert infer.call_args.args[1]["execution_mode"] == mode
    finally:
        service.db.close()


def test_object_output_with_nested_primitives_remains_satisfiable(tmp_path):
    service, _, row, data, plan = setup_run(tmp_path)
    plan["steps"] = plan["steps"][:1]
    plan["steps"][0]["outputs"] = {
        "type": "object",
        "properties": {
            "items": {"type": "array", "items": {"type": "integer"}},
            "ok": {"type": "boolean"},
        },
        "required": ["items", "ok"],
    }
    try:
        with (
            patch.object(
                service,
                "infer",
                AsyncMock(
                    return_value={"answer": '```harness-result\n{"items":[1,2],"ok":true}\n```'}
                ),
            ),
            patch.object(service.gates, "ask", AsyncMock()) as gate,
        ):
            asyncio.run(maestro.execute_plan(service, row, data, plan))
        gate.assert_not_awaited()
    finally:
        service.db.close()
