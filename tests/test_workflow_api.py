"""Workflow admission and recovery preserve owner and server metadata boundaries."""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from test_workspaces import config

from agent_service.app import Service
from agent_service.errors import APIError


def submitted(service, identity, **fields):
    return service.submit(
        identity,
        {
            "project_id": "p",
            "backend": "codex",
            "model": "gpt-6-astra",
            "effort": "low",
            "prompt": "Review",
            **fields,
        },
    )


@pytest.mark.parametrize(
    "field",
    ["_declared_workflow", "_workflow_parent_job_id", "_workflow_from_step", "_workflow_resume"],
)
def test_clients_cannot_forge_workflow_recovery(tmp_path, field):
    service = Service(config(tmp_path))
    try:
        identity = ("a", service.config["clients"]["a"])
        with pytest.raises(APIError, match="invalid_internal_field"):
            submitted(service, identity, **{field: "forged"})
    finally:
        service.db.close()


@pytest.mark.parametrize("policy", [True, "automatic", None, {}, 1])
def test_plan_policy_requires_explicit_review_or_auto(tmp_path, policy):
    service = Service(config(tmp_path))
    try:
        identity = ("a", service.config["clients"]["a"])
        with pytest.raises(APIError, match="invalid_maestro_plan_policy"):
            submitted(service, identity, maestro_plan_policy=policy)
    finally:
        service.db.close()


def test_recovery_checks_owner_and_requires_terminal_source(tmp_path):
    service = Service(config(tmp_path))
    try:
        identity = ("a", service.config["clients"]["a"])
        job = submitted(service, identity)["job_id"]
        with pytest.raises(APIError):
            service.recover_workflow(("b", service.config["clients"]["b"]), job, {})
        with pytest.raises(APIError, match="workflow_source_busy"):
            service.recover_workflow(identity, job, {})
    finally:
        service.db.close()


def test_capabilities_support_non_codex_coordinator(tmp_path):
    settings = config(tmp_path)
    settings["services"]["codex"]["enabled"] = False
    settings["maestro_coordinator"] = {"backend": "local"}
    service = Service(settings)
    try:
        capabilities = service.capabilities()
        assert capabilities["maestro"]["enabled"] is True
        assert capabilities["maestro"]["max_steps"] == 12
        assert capabilities["maestro"]["plan_policy"] == "review"
    finally:
        service.db.close()


def test_workflow_palette_dispatch_recovery_and_save_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    settings = config(tmp_path / "state")
    root = tmp_path / "project"
    (root / "workflows").mkdir(parents=True)
    settings["projects"]["p"]["root"] = str(root)
    definition = {
        "id": "review",
        "version": 1,
        "steps": [
            {
                "id": name,
                "kind": "builtin",
                "resource_id": "builtin/roles/" + name,
                "args": "Inspect " + name,
                "backend": "codex",
                "model": "gpt-6-astra",
                "effort": "low",
            }
            for name in ("draft", "review")
        ],
    }
    (root / "workflows" / "review.json").write_text(json.dumps(definition))
    service = Service(settings)
    try:
        identity = ("a", settings["clients"]["a"])
        resource = next(
            item
            for item in service.resource_catalog(identity, "p", "codex", "gpt-6-astra")["items"]
            if item["kind"] == "workflow"
        )
        job = submitted(
            service,
            identity,
            prompt="/review Inspect",
            resource_selections=[
                {"id": resource["id"], "revision": resource["revision"], "token": "/review"}
            ],
        )["job_id"]

        async def execute(job_id):
            with patch.object(
                service, "infer", AsyncMock(return_value={"answer": "Reviewed"})
            ) as infer:
                result = await service.execute(service.job(identity, job_id))
                with service.db:
                    service.conversation_repository.set_result(
                        job_id, "completed", json.dumps(result)
                    )
                return infer.await_count

        assert asyncio.run(execute(job)) == 2
        resumed = service.recover_workflow(identity, job, {})["job_id"]
        assert asyncio.run(execute(resumed)) == 0
        rerun = service.recover_workflow(identity, resumed, {"from_step": 2}, rerun=True)["job_id"]
        assert asyncio.run(execute(rerun)) == 1
        saved = service.save_workflow(identity, rerun, "saved-review")
        assert saved["path"] == "workflows/saved-review.json"
        assert (root / saved["path"]).is_file()
        changed = service.recover_workflow(
            identity, rerun, {"workflow_inputs": {"evidence": "changed"}}
        )["job_id"]
        assert asyncio.run(execute(changed)) == 2
    finally:
        service.db.close()


def test_mcp_auto_policy_is_explicit_and_recovery_is_available(monkeypatch):
    from agent_service import mcp_bridge

    call = AsyncMock(return_value={"job_id": "synthetic"})
    monkeypatch.setattr(mcp_bridge, "call", call)
    asyncio.run(mcp_bridge.submit_job("p"))
    assert "maestro_plan_policy" not in call.call_args.args[2]
    asyncio.run(mcp_bridge.submit_job("p", maestro_plan_policy="auto", workflow_inputs={"x": 1}))
    assert call.call_args.args[2]["maestro_plan_policy"] == "auto"
    asyncio.run(mcp_bridge.rerun_workflow("synthetic", 2))
    assert call.call_args.args == ("POST", "/v1/jobs/synthetic/rerun", {"from_step": 2})


def test_control_preserves_coordinator_and_project_plan_policy(tmp_path):
    from control.server import Manager

    manager = Manager(tmp_path / "control")
    settings = dict(manager.settings)
    settings["maestro_coordinator"] = {"backend": "local", "model": "fixture"}
    (tmp_path / "project").mkdir()
    settings["projects"] = [
        {"id": "p", "root": str(tmp_path / "project"), "maestro_plan_policy": "auto"}
    ]
    validated = manager.validate(settings)
    assert validated["maestro_coordinator"] == settings["maestro_coordinator"]
    assert validated["projects"][0]["maestro_plan_policy"] == "auto"
    settings["projects"][0]["maestro_plan_policy"] = "silent"
    with pytest.raises(ValueError, match="plan policy"):
        manager.validate(settings)


def test_denied_plan_cannot_be_saved_as_successful_workflow(tmp_path):
    service = Service(config(tmp_path))
    try:
        identity = ("a", service.config["clients"]["a"])
        job = submitted(service, identity)["job_id"]
        with service.db:
            service.conversation_repository.set_result(
                job,
                "completed",
                json.dumps(
                    {
                        "orchestration": {
                            "plan": {"steps": [{"task": "Denied"}]},
                            "steps": [],
                            "approved": False,
                        }
                    }
                ),
            )
        with pytest.raises(APIError, match="workflow_requires_successful_chain"):
            service.save_workflow(identity, job, "denied")
    finally:
        service.db.close()


def test_queued_recovery_resolves_current_workflow_revision(tmp_path):
    service = Service(config(tmp_path))
    try:
        identity = ("a", service.config["clients"]["a"])
        job = submitted(service, identity)["job_id"]
        row = service.job(identity, job)
        data = json.loads(row["payload"])
        data["_declared_workflow"] = {"resource_id": "project/p/workflows/review.json", "steps": []}
        with service.db:
            service.conversation_repository.set_payload(job, json.dumps(data))
        current = {"id": "review", "steps": [{"task": "Updated"}]}
        with (
            patch("agent_service.workflows.resolve_workflow", return_value=current) as resolve,
            patch(
                "agent_service.maestro.execute_workflow", AsyncMock(return_value={"answer": "done"})
            ) as execute,
        ):
            asyncio.run(service.execute(service.job(identity, job)))
        resolve.assert_called_once_with(service.config, "p", "project/p/workflows/review.json")
        assert execute.call_args.args[3] == current
    finally:
        service.db.close()
