"""Unattended runs have no internet unless their task opts in (D03)."""

# Fixtures imported from schedule_fixtures are re-declared as test arguments.
# ruff: noqa: F811

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from schedule_fixtures import (  # noqa: F401
    ALICE,
    VALID,
    clock,
    config,
    folder_of,
    idle_worker,
)
from starlette.testclient import TestClient
from test_approval_policy import run_codex_route

from adapters.claude.native import build_command as claude_command
from agent_service.app import create_app


@pytest.fixture
def api(config, clock):
    for name in ("codex", "claude"):
        config["services"][name]["permissions"] = {"read": True, "internet": True}
        config[name] = {"binary": "fixture"}
    config["claude_models"] = {"sonnet": ["configured"]}
    config["services"]["gemini"] = {
        **config["services"]["codex"],
        "models": ["auto-gemini-3"],
        "permissions": {"read": True, "internet": True},
    }
    config["gemini_models"] = {"auto-gemini-3": ["configured"]}
    config["gemini"] = {"binary": "fixture"}
    with TestClient(create_app(config), headers=ALICE) as client:
        yield client


def make(api, **overrides):
    response = api.post("/v1/schedules", json={**VALID, **overrides})
    assert response.status_code == 201, response.text
    return response.json()


def test_internet_is_off_by_default_and_can_be_turned_on(api):
    created = make(api)
    assert created["allow_internet"] is False
    changed = api.put(
        "/v1/schedules/" + created["id"],
        json={"revision": created["revision"], "allow_internet": True},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["allow_internet"] is True
    assert make(api, allow_internet=True)["allow_internet"] is True


@pytest.mark.parametrize("value", ["yes", 1, None])
def test_allow_internet_must_be_a_boolean(api, value):
    response = api.post("/v1/schedules", json={**VALID, "allow_internet": value})
    assert response.status_code == 422, response.text
    assert response.json()["field"] == "allow_internet"


def test_a_task_saved_before_the_option_loads_without_internet(api, config):
    created = make(api)
    path = folder_of(config) / (created["id"] + ".json")
    stored = json.loads(path.read_text(encoding="utf-8"))
    del stored["allow_internet"]
    path.write_text(json.dumps(stored), encoding="utf-8")
    assert api.get("/v1/schedules").json()["schedules"][0]["allow_internet"] is False


def test_a_client_cannot_grant_internet_to_its_own_job(api):
    job = {key: VALID[key] for key in ("project_id", "backend", "model", "effort", "prompt")}
    response = api.post("/v1/jobs", json={**job, "schedule_internet": True})
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_internal_field"


def scheduled_run(api, backend, allow_internet):
    """The project and backend config a scheduled run would hand to the provider."""
    route = {
        "claude": {"backend": "claude", "model": "sonnet", "effort": "configured"},
        "gemini": {"backend": "gemini", "model": "auto-gemini-3", "effort": "configured"},
    }.get(backend, {})
    created = make(api, allow_internet=allow_internet, **route)
    job_id = api.post("/v1/schedules/" + created["id"] + "/run").json()["job_id"]
    service = api.app.state.service
    row = service.conversation_repository.get(job_id)
    seen = {}

    async def native(config, prompt, event, project, *rest):
        seen.update(config=config, project=project)
        return {"answer": "fixture"}

    with (
        patch("adapters.run_native", side_effect=native),
        patch.object(service, "quota", AsyncMock(return_value={})),
        patch.object(service, "claude_quota", AsyncMock(return_value={})),
    ):
        asyncio.run(service.infer(row, json.loads(row["payload"])))
    return seen["project"], seen["config"]


@pytest.mark.parametrize("allow_internet", [False, True])
def test_codex_scheduled_runs_keep_owner_network_grant(api, tmp_path, allow_internet):
    project, _ = scheduled_run(api, "codex", allow_internet)
    assert project["permissions"]["internet"] is True
    command, _, turn = run_codex_route(tmp_path, "codex", {**project, "root": None})
    assert turn["sandboxPolicy"]["networkAccess"] is True
    assert not any(arg.startswith("web_search=") for arg in command)


@pytest.mark.parametrize("allow_internet", [False, True])
def test_claude_scheduled_runs_keep_owner_web_tools(api, tmp_path, allow_internet):
    project, _ = scheduled_run(api, "claude", allow_internet)
    assert project["permissions"]["internet"] is True
    with (
        patch("control.integrations.configurations", return_value={"claude": {}}),
        patch("control.integrations.inventory", return_value={"claude": []}),
    ):
        command = claude_command(
            {"binary": "claude"},
            "sonnet",
            tmp_path,
            project["permissions"],
            project["access_mode"],
            [],
        )
    assert "--tools" not in command


@pytest.mark.parametrize("allow_internet", [False, True])
def test_gemini_scheduled_runs_carry_no_connectors_either_way(api, tmp_path, allow_internet):
    """Gemini keeps its own native config: the run allows no MCP server, with or without internet."""
    from adapters.gemini.policy import prepare

    project, config = scheduled_run(api, "gemini", allow_internet)
    assert "integrations" not in config
    with (
        patch("adapters.gemini.policy.SYSTEM_POLICIES", tmp_path / "absent"),
        patch("adapters.gemini.policy.SYSTEM_SETTINGS", tmp_path / "absent.json"),
    ):
        command, _ = prepare(config, tmp_path, project["permissions"], project["access_mode"])
    allowed = command[command.index("--allowed-mcp-server-names") + 1]
    assert allowed.startswith("keepharness-none-")


def test_an_attended_follow_up_keeps_the_provider_grant(api):
    created = make(api)
    job_id = api.post("/v1/schedules/" + created["id"] + "/run").json()["job_id"]
    service = api.app.state.service
    service.finish(job_id, "completed", {"answer": "done"})
    job = {key: VALID[key] for key in ("project_id", "backend", "model", "effort", "prompt")}
    follow_up = api.post("/v1/jobs", json={**job, "parent_job_id": job_id}).json()["job_id"]
    row = service.conversation_repository.get(follow_up)
    seen = {}

    async def native(config, prompt, event, project, *rest):
        seen.update(project=project)
        return {"answer": "fixture"}

    with (
        patch("adapters.run_native", side_effect=native),
        patch.object(service, "quota", AsyncMock(return_value={})),
    ):
        asyncio.run(service.infer(row, json.loads(row["payload"])))
    assert seen["project"]["permissions"]["internet"] is True
