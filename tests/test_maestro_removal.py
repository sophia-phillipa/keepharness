"""The Maestro planner is gone; Workflows and declared chains keep running on the step engine."""

import asyncio
import copy
import json
from unittest.mock import AsyncMock, patch

import pytest
from starlette.testclient import TestClient
from test_workflow_api import submitted
from test_workflow_resume_rerun import setup_run
from test_workspaces import config, single_owner_config

from agent_service import maestro
from agent_service.app import Service, create_app
from agent_service.config import runtime_job_affected, validate_runtime_config
from agent_service.errors import APIError


def make_service(tmp_path, **extra):
    service = Service({**config(tmp_path), **extra})
    return service, ("a", service.config["clients"]["a"])


def legacy_job(service, identity, **payload):
    """A job as an older build stored it: backend ``maestro``, terminal or queued."""
    job = submitted(service, identity)["job_id"]
    row = service.job(identity, job)
    with service.db:
        service.conversation_repository.set_payload(
            job, json.dumps({**json.loads(row["payload"]), "backend": "maestro", **payload})
        )
    return job


@pytest.mark.parametrize(
    "extra",
    [{}, {"invocations": [{"kind": "workflow", "resource_id": "x"}]}],
    ids=["plain", "with_invocations"],
)
def test_submit_backend_maestro_is_backend_unavailable(tmp_path, extra):
    service, identity = make_service(tmp_path)
    try:
        with pytest.raises(APIError, match="backend_unavailable") as caught:
            submitted(service, identity, backend="maestro", model="auto", effort="auto", **extra)
        assert caught.value.status == 422
    finally:
        service.db.close()


def test_auto_uses_default_backend_then_first_enabled(tmp_path):
    service, _ = make_service(tmp_path)
    try:
        assert service.resolve_execution({"project_id": "p", "backend": "auto"})["backend"] == (
            "codex"
        )
        service.config["default_backend"] = "local"
        chosen = service.resolve_execution({"project_id": "p", "backend": "auto"})
        assert chosen["backend"] == "local" and chosen["model"] == "installed-model"
    finally:
        service.db.close()


def test_legacy_maestro_settings_keys_are_stripped_without_error(tmp_path):
    from control.server import Manager

    manager = Manager(tmp_path / "control")
    (tmp_path / "project").mkdir()
    settings = {
        **manager.settings,
        "maestro_enabled": False,
        "maestro_instructions": 7,
        "maestro_coordinator": {"backend": "nope"},
        "projects": [
            {"id": "p", "root": str(tmp_path / "project"), "maestro_plan_policy": "silent"}
        ],
    }
    validated = manager.validate(settings)
    assert not [key for key in validated if key.startswith("maestro_")]
    assert "maestro_plan_policy" not in validated["projects"][0]
    candidate = single_owner_config(tmp_path)
    candidate["projects"]["p"]["maestro_plan_policy"] = "silent"
    candidate["maestro_plan_policy"] = "silent"
    assert validate_runtime_config(candidate)


def test_queued_maestro_job_fails_backend_unavailable(tmp_path):
    service, identity = make_service(tmp_path)
    try:
        job = legacy_job(service, identity)
        with patch.object(service, "infer", AsyncMock()) as infer:
            with pytest.raises(APIError, match="backend_unavailable"):
                asyncio.run(service.execute(service.job(identity, job)))
        infer.assert_not_called()
    finally:
        service.db.close()


def test_maestro_job_offers_no_workflow_recovery(tmp_path):
    service, identity = make_service(tmp_path)
    try:
        step = {"role": "r", "backend": "codex", "model": "gpt-6-astra", "effort": "low"}
        planner = legacy_job(service, identity)
        declared = legacy_job(service, identity, _declared_workflow={"steps": [step]})
        for job in (planner, declared):
            folder = service.root / "maestro" / job
            folder.mkdir(parents=True)
            (folder / "plan.json").write_text(json.dumps({"plan": {"steps": [step]}}))
            with service.db:
                service.conversation_repository.set_result(job, "completed", "{}")
        assert not service.has_workflow_checkpoint(service.job(identity, planner))
        assert service.has_workflow_checkpoint(service.job(identity, declared))
        with pytest.raises(APIError, match="backend_unavailable") as caught:
            service.recover_workflow(identity, planner, {}, rerun=True)
        assert caught.value.status == 409
    finally:
        service.db.close()


@pytest.mark.parametrize("policy", ["auto", "review", "silent", 7])
def test_maestro_plan_policy_request_key_ignored(tmp_path, policy):
    service, identity = make_service(tmp_path)
    try:
        job = submitted(service, identity, maestro_plan_policy=policy)["job_id"]
        assert "maestro_plan_policy" not in json.loads(service.job(identity, job)["payload"])
    finally:
        service.db.close()


def test_workflow_still_runs_steps(tmp_path):
    service, identity, row, data, plan = setup_run(tmp_path)
    try:
        with patch.object(
            service, "infer", AsyncMock(side_effect=[{"answer": "evidence"}, {"answer": "done"}])
        ):
            result = asyncio.run(maestro.execute_workflow(service, row, data, plan))
        assert result["answer"] == "done"
        kinds = [event["type"] for event in service.message_repository.all_events(row["id"])]
        assert kinds.count("maestro_step") == kinds.count("maestro_step_completed") == 2
        assert "maestro_planning" not in kinds
    finally:
        service.db.close()


def test_capabilities_drop_maestro_planner(tmp_path):
    service, _ = make_service(tmp_path)
    try:
        assert "maestro" not in service.capabilities()
    finally:
        service.db.close()


@pytest.mark.parametrize(
    "model", ["auto", "gpt-6-astra"], ids=["model_auto", "model_explicit"]
)
def test_assess_backend_maestro_is_backend_unavailable(tmp_path, model):
    service, identity = make_service(tmp_path)
    try:
        with pytest.raises(APIError, match="backend_unavailable") as caught:
            service.assess(
                identity,
                {"project_id": "p", "prompt": "x", "backend": "maestro", "model": model},
            )
        assert caught.value.status == 422
    finally:
        service.db.close()


def test_assess_route_backend_maestro_is_backend_unavailable(tmp_path):
    with TestClient(create_app(config(tmp_path)), headers={"Authorization": "Bearer a"}) as client:
        for model in ("auto", "gpt-6-astra"):
            response = client.post(
                "/v1/assess",
                json={"project_id": "p", "prompt": "x", "backend": "maestro", "model": model},
            )
            assert response.status_code == 422
            assert "backend_unavailable" in response.text


def test_models_route_has_no_maestro_field(tmp_path):
    with TestClient(create_app(config(tmp_path)), headers={"Authorization": "Bearer a"}) as client:
        body = client.get("/v1/models", params={"project_id": "p"}).json()
        assert "maestro" not in body
        assert "maestro" not in client.get("/.well-known/agent-capabilities.json").json()


def test_running_declared_workflow_is_judged_by_its_active_step_provider(tmp_path):
    current = config(tmp_path)
    row = {
        "id": "j",
        "owner": "a",
        "project": "p",
        "state": "running",
        "payload": json.dumps(
            {"backend": "codex", "model": "gpt-6-astra", "_declared_workflow": True}
        ),
    }
    executors = {"j": ("local", "installed-model")}
    step_removed = copy.deepcopy(current)
    step_removed["services"]["local"]["models"] = []
    assert runtime_job_affected(current, executors, row, step_removed)
    payload_provider_removed = copy.deepcopy(current)
    payload_provider_removed["services"]["codex"]["models"] = []
    assert not runtime_job_affected(current, executors, row, payload_provider_removed)
