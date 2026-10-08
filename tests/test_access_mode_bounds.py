"""Automatic is project-bounded and Full access is owner-only and off until enabled (D11)."""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from test_approval_authority import ceiling_config, client_for
from test_approval_policy import ALL_GRANTS, HOST_SERVERS, SELECTED, run_codex_route

from adapters.claude.native import build_command as claude_command
from agent_service.app import create_app
from agent_service.approval_policy import effective_permissions
from agent_service.errors import APIError
from control.manager import Manager
from control.runtime_config import base_config


def codex_project(tmp_path, mode, grants=ALL_GRANTS):
    root = tmp_path / "project"
    root.mkdir(exist_ok=True)
    return {
        "root": str(root),
        "permissions": effective_permissions(grants, mode),
        "access_mode": mode,
    }, root


@pytest.mark.parametrize("provider", ["codex", "deepseek"])
@pytest.mark.parametrize("internet", [True, False])
def test_codex_automatic_is_a_workspace_sandbox_on_the_project(tmp_path, provider, internet):
    grants = {**ALL_GRANTS, "internet": internet}
    project, root = codex_project(tmp_path, "auto", grants)
    command, thread, turn = run_codex_route(tmp_path, provider, project)
    assert thread["sandbox"] == "workspace-write"
    # Anything outside the sandbox escalates to an approval card instead of running.
    assert thread["approvalPolicy"] == turn["approvalPolicy"] == "on-request"
    assert turn["sandboxPolicy"] == {
        "type": "workspaceWrite",
        "networkAccess": internet,
        "writableRoots": [str(root.resolve())],
        "excludeSlashTmp": True,
        "excludeTmpdirEnvVar": True,
    }
    # Connector calls leave the project too: each one asks.
    if provider == "deepseek":
        host = {name: thread["config"]["mcp_servers"][name] for name in HOST_SERVERS}
        assert {spec["default_tools_approval_mode"] for spec in host.values()} == {"prompt"}
    else:
        assert not set(HOST_SERVERS) & thread["config"]["mcp_servers"].keys()


@pytest.mark.parametrize("provider", ["codex", "deepseek"])
def test_codex_full_access_stays_unrestricted(tmp_path, provider):
    project, _ = codex_project(tmp_path, "full")
    _, thread, turn = run_codex_route(tmp_path, provider, project)
    assert thread["sandbox"] == "danger-full-access"
    assert thread["approvalPolicy"] == turn["approvalPolicy"] == "never"
    assert turn["sandboxPolicy"] == {"type": "dangerFullAccess"}


def claude_build(tmp_path, mode, roots=()):
    with (
        patch("control.integrations.configurations", return_value={"claude": HOST_SERVERS}),
        patch("control.integrations.inventory", return_value={"claude": []}),
    ):
        return claude_command(
            {"binary": "claude", "unrestricted": True, "personal_setup": True},
            "fixture",
            tmp_path,
            effective_permissions(ALL_GRANTS, mode),
            SELECTED,
            mode,
            list(roots),
        )


def test_claude_automatic_accepts_edits_in_the_project_and_asks_beyond(tmp_path):
    command = claude_build(tmp_path, "auto", ["/project", "/project-extra"])
    assert "bypassPermissions" not in command
    assert command[command.index("--permission-mode") + 1] == "acceptEdits"
    assert command[command.index("--add-dir") + 1 :][:2] == ["/project", "/project-extra"]
    settings = json.loads(command[command.index("--settings") + 1])
    # Native acceptEdits decides approval; KeepHarness does not rewrite owner rules.
    assert "permissions" not in settings


def test_claude_full_access_bypasses_permissions(tmp_path):
    command = claude_build(tmp_path, "full")
    assert command[command.index("--permission-mode") + 1] == "bypassPermissions"


# -- Full access is off until the owner turns it on --------------------------------------------


def full_app(tmp_path, enabled):
    config = ceiling_config(tmp_path)
    if enabled is None:
        del config["full_access"]
    else:
        config["full_access"] = enabled
    return create_app(config)


JOB = {"project_id": "p", "backend": "codex", "model": "gpt-6-astra", "prompt": "fixture"}


async def owner_post(app, path, body=None):
    async with client_for(app, headers={"Authorization": "Bearer local-token"}) as client:
        if body is None:
            return await client.get(path)
        return await client.post(path, json=body)


@pytest.mark.parametrize("enabled", [None, False])
def test_full_access_is_refused_until_enabled(tmp_path, enabled):
    app = full_app(tmp_path, enabled)
    try:
        refused = asyncio.run(owner_post(app, "/v1/jobs", {**JOB, "access_mode": "full"}))
        assert refused.status_code == 403, refused.text
        assert refused.json()["code"] == "full_access_disabled"
        automatic = asyncio.run(owner_post(app, "/v1/jobs", {**JOB, "access_mode": "auto"}))
        assert automatic.status_code == 202, automatic.text
    finally:
        app.state.service.db.close()


def test_full_access_is_accepted_once_enabled(tmp_path):
    app = full_app(tmp_path, True)
    try:
        accepted = asyncio.run(owner_post(app, "/v1/jobs", {**JOB, "access_mode": "full"}))
        assert accepted.status_code == 202, accepted.text
    finally:
        app.state.service.db.close()


def test_a_queued_full_run_fails_closed_once_full_access_is_turned_off(tmp_path):
    app = full_app(tmp_path, True)
    service = app.state.service
    try:
        job_id = asyncio.run(owner_post(app, "/v1/jobs", {**JOB, "access_mode": "full"})).json()[
            "job_id"
        ]
        service.config["full_access"] = False
        row = service.conversation_repository.get(job_id)
        with (
            patch("adapters.run_native", AsyncMock(return_value={"answer": "fixture"})) as native,
            patch.object(service, "quota", AsyncMock(return_value={})),
            pytest.raises(APIError) as refused,
        ):
            asyncio.run(service.infer(row, {**JOB, "effort": "low", "access_mode": "full"}))
        assert refused.value.code == "full_access_disabled"
        native.assert_not_called()
    finally:
        service.db.close()


@pytest.mark.parametrize("enabled,offered", [(True, True), (False, False)])
def test_the_model_catalog_tells_the_access_menu_whether_to_offer_full(tmp_path, enabled, offered):
    app = full_app(tmp_path, enabled)

    async def scenario():
        async with client_for(app, headers={"Authorization": "Bearer local-token"}) as client:
            return await client.get("/v1/models", params={"project_id": "p"})

    try:
        with patch.object(app.state.service, "models_with_context", AsyncMock(return_value=[])):
            response = asyncio.run(scenario())
        assert response.status_code == 200, response.text
        assert response.json()["full_access"] is offered
        # The owner is the owner whether or not Full mode is enabled.
        assert response.json()["local_owner"] is True
    finally:
        app.state.service.db.close()


# -- the admin setting --------------------------------------------------------------------------


def test_the_admin_setting_defaults_off_and_reaches_the_runtime_config(tmp_path):
    manager = Manager(tmp_path / "state")
    assert manager.validate(manager.settings)["full_access"] is False
    enabled = manager.validate({**manager.settings, "full_access": True})
    assert enabled["full_access"] is True
    for value in ("yes", 1, None):
        with pytest.raises(ValueError):
            manager.validate({**manager.settings, "full_access": value})
    runtime = base_config(enabled, tmp_path / "state", 8094, "http://127.0.0.1:8095/", {})
    assert runtime["full_access"] is True
    assert base_config(manager.settings, tmp_path / "state", 8094, "", {})["full_access"] is False


@pytest.mark.parametrize("mode", ["ask", "auto", "full", "read_only"])
@pytest.mark.parametrize("shell,write", [(True, True), (False, False)])
def test_access_menu_metadata_matches_actual_native_commands(tmp_path, mode, shell, write):
    """The HTTP metadata and native execution must share their permission translation."""
    from types import SimpleNamespace

    from agent_service.routes.models import models as models_route

    grants = {**ALL_GRANTS, "shell": shell, "write": write}
    entries = [{"id": "fixture", "backend": p, "permissions": grants} for p in ("codex", "claude")]
    identity = ("local", {"projects": ["p"]})
    config = {"codex": {"unrestricted": True}, "claude": {"unrestricted": True}}
    service = SimpleNamespace(
        config=config, models_with_context=AsyncMock(return_value=entries),
        project=lambda *a: None, identity=lambda *a, **k: identity, uploads_enabled=lambda *a: False,
    )
    response = asyncio.run(models_route(SimpleNamespace(query_params={}), service, identity))
    codex, claude = json.loads(response.body)["models"]
    project, _ = codex_project(tmp_path, mode, grants)
    _, thread, turn = run_codex_route(tmp_path, "codex", project)
    assert codex["access_modes"][mode] == {key: thread[key] for key in ("sandbox", "approvalPolicy")}
    assert codex["access_modes"][mode]["approvalPolicy"] == turn["approvalPolicy"]
    command = claude_command(
        {"binary": "claude", "unrestricted": True}, "fixture", tmp_path,
        effective_permissions(grants, mode), [], mode, [],
    )
    assert claude["access_modes"][mode] == {"permissionMode": command[command.index("--permission-mode") + 1]}
