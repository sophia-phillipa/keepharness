"""Secrets stay out of exports and logs, and state files are private (P5-18, F-09).

The umask is forced to 022 so the checks do not depend on the ``os.umask(0o077)`` that
only the command-line entry points apply.
"""

import asyncio
import os
import stat
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from starlette.testclient import TestClient
from test_workspaces import config

from agent_service.app import create_app as create_harness_app
from control import runtime_config
from control.server import Manager, create_app

DEEPSEEK_TOKEN = "sk-hygiene-" + "d" * 32


@pytest.fixture
def open_umask():
    previous = os.umask(0o022)
    try:
        yield
    finally:
        os.umask(previous)


def private(root):
    """Every file and directory under ``root`` (and ``root`` itself) that others can reach."""
    return sorted(
        f"{path.relative_to(root.parent)} {stat.filemode(path.stat().st_mode)}"
        for path in [root, *root.rglob("*")]
        if path.stat().st_mode & 0o077
    )


def start_without_process(state):
    """Run ``Manager.start`` with the harness process and readiness probe mocked."""
    manager = Manager(state)
    manager.refresh = AsyncMock()
    cfg = {"clients": {}, "projects": {"sem-projeto": {}}}
    runtime_config.build_clients(cfg, manager.settings | {"logins": []}, state, {})
    manager.build_runtime_config = AsyncMock(return_value=cfg)
    process = SimpleNamespace(returncode=None)
    spawn = AsyncMock(return_value=process)

    async def get(url):
        return SimpleNamespace(status_code=200)

    async def scenario():
        with (
            patch("socket.socket"),
            patch("asyncio.create_subprocess_exec", spawn),
            patch("httpx.AsyncClient") as client,
        ):
            client.return_value.__aenter__.return_value.get = AsyncMock(side_effect=get)
            await manager.start()

    asyncio.run(scenario())
    return spawn


def test_control_state_keeps_secrets_out_and_files_private(tmp_path, open_umask):
    state = tmp_path / "state"
    headers = {"X-Harness-Admin": "1"}
    with (
        patch("control.manager.Manager.refresh", AsyncMock()),
        TestClient(create_app(state), base_url="http://127.0.0.1:8094") as client,
    ):
        client.get("/")
        stored = client.post(
            "/api/provider-token",
            json={"provider": "deepseek", "token": DEEPSEEK_TOKEN},
            headers=headers,
        )
        assert stored.status_code == 200, stored.text
        manager = client.app.state.manager
        saved = client.post("/api/settings", json=manager.settings, headers=headers)
        assert saved.status_code == 200, saved.text
        start_without_process(state)
        vpn_token = client.post("/api/vpn-key", json={}, headers=headers).json()["token"]
        exported = client.post("/api/settings-export", json={}, headers=headers)
        assert exported.status_code == 200
    secrets = (DEEPSEEK_TOKEN, vpn_token)
    for text in (exported.text, (state / "audit.jsonl").read_text()):
        assert not any(secret in text for secret in secrets)
    assert not any(secret.encode() in (state / "harness.log").read_bytes() for secret in secrets)
    for name in ("audit.jsonl", "harness.log", "settings.json", "runtime.json", "vpn.key"):
        assert (state / name).exists(), name
    assert private(state) == []


def test_harness_starts_with_the_prefixed_config_variable(tmp_path, open_umask):
    spawn = start_without_process(tmp_path / "state")
    env = spawn.call_args.kwargs["env"]
    assert env["TAIL_HARNESS_AGENT_CONFIG"] == str(tmp_path / "state" / "runtime.json")
    assert env.get("LOCAL_AGENT_CONFIG") == os.environ.get("LOCAL_AGENT_CONFIG")


def test_harness_request_logs_and_database_are_private(tmp_path, open_umask):
    cfg = config(tmp_path / "runs")
    cfg["projects"]["p"]["service_units"] = ["demo.service"]
    cfg["services"]["codex"].update(mode="native", permissions={"shell": True})
    app = create_harness_app(cfg)
    with (
        TestClient(app, headers={"Authorization": "Bearer a"}) as client,
        patch("agent_service.service_control.shutil.which", return_value="/usr/bin/systemctl"),
        patch(
            "agent_service.service_control.process",
            AsyncMock(return_value=(0, "ActiveState=active\nLoadState=loaded")),
        ),
    ):
        response = client.post(
            "/v1/services",
            json={
                "project_id": "p",
                "action": "start",
                "unit": "demo.service",
                "user_requested": True,
            },
        )
        assert response.status_code == 200, response.text
    app.state.service.db.close()
    root = Path(cfg["state_dir"])
    assert (root / "service-actions.jsonl").exists()
    assert (root / "jobs.sqlite3").exists()
    assert private(root) == []
