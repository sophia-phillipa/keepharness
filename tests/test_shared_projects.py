"""Project registration and native provider policy, without model inference."""

import asyncio
import copy
import hashlib
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

from adapters import run_native as run
from agent_service.app import Service, create_app
from control.server import Manager


def config(tmp_path):
    return {
        "state_dir": str(tmp_path / "state"),
        "shared_projects": True,
        "projects": {"sem-projeto": {}},
        "clients": {"a": {"sha256": hashlib.sha256(b"a").hexdigest(), "projects": ["sem-projeto"]}},
        "services": {
            p: {
                "enabled": True,
                "models": [p + "-test"],
                "projects": [],
                "permissions": {"read": True},
            }
            for p in ("local", "codex", "claude", "deepseek")
        },
        "codex_models": {"codex-test": ["low"]},
        "deepseek_models": {"deepseek-test": ["configured"]},
    }


def test_registration_persists_and_shares_models(tmp_path):
    cfg = config(tmp_path)
    app = create_app(copy.deepcopy(cfg))
    root = tmp_path / "project"
    root.mkdir()
    client = TestClient(app, headers={"Authorization": "Bearer a"})
    response = client.post("/v1/projects", json={"root": str(root), "label": "Demo"})
    assert response.status_code == 201, response.text
    pid = response.json()["project_id"]
    assert client.post("/v1/projects", json={"root": str(root)}).json()["project_id"] == pid
    assert pid in client.get("/v1/projects").json()["projects"]
    assert {
        m["backend"] for m in client.get("/v1/models", params={"project_id": pid}).json()["models"]
    } >= {"local", "codex", "claude", "deepseek"}
    client.close()
    app.state.service.db.close()
    service = Service(copy.deepcopy(cfg))
    assert service.config["projects"][pid]["root"] == str(root)
    assert all(pid in spec["projects"] for spec in service.config["services"].values())
    assert pid in service.config["clients"]["a"]["projects"]
    service.db.close()


def test_registration_auth_and_paths(tmp_path):
    app = create_app(config(tmp_path))
    client = TestClient(app)
    assert client.post("/v1/projects", json={"root": str(tmp_path)}).status_code == 401
    client.headers["Authorization"] = "Bearer a"
    for root in ("/", str(tmp_path / "state")):
        assert client.post("/v1/projects", json={"root": root}).status_code == 403
    for root in ("relative", str(tmp_path / "missing")):
        assert client.post("/v1/projects", json={"root": root}).status_code == 422
    client.close()
    app.state.service.db.close()


def test_control_normalizes_cloud_access_and_keeps_local_permissions(tmp_path):
    manager = Manager(tmp_path / "control")
    settings = copy.deepcopy(manager.settings)
    for name in ("codex", "claude", "deepseek"):
        model = "claude-sonnet-4-6" if name == "claude" else "fixture"
        settings["services"][name].update(enabled=True, models=[model], projects=[], mode="scoped")
    normalized = manager.validate(settings)
    for name in ("codex", "claude", "deepseek"):
        assert normalized["services"][name]["mode"] == "native"
        assert all(normalized["services"][name]["permissions"].values())
        assert normalized["services"][name]["projects"] == ["sem-projeto"]
    assert not any(normalized["services"]["local"]["permissions"].values())
    assert normalized["uploads_enabled"]


@pytest.mark.parametrize("provider", ["codex", "deepseek"])
def test_cloud_native_turn_is_unrestricted(tmp_path, provider):
    recorded = {}

    class RPC:
        async def call(self, method, params):
            recorded["thread"] = params
            return {"thread": {"id": "fixture"}}

        async def send(self, method, params):
            recorded["turn"] = params

        async def receive(self):
            return {"method": "turn/completed", "params": {"turn": {"status": "completed"}}}

    @asynccontextmanager
    async def connection(*args, **kwargs):
        yield RPC()

    async def approve(*args):
        raise AssertionError("No tools executed")

    root = tmp_path / "project"
    root.mkdir()
    config = {"binary": "fixture", "unrestricted": True}
    if provider == "deepseek":
        key = tmp_path / "fixture.key"
        key.write_text("fixture-key")
        config["api_provider"] = {"url": "https://api.deepseek.com", "key_file": str(key)}
    with (
        patch("adapters.codex.native.connection", connection),
        patch("adapters.codex.native.configurations", return_value={"codex": {}}),
        patch("adapters.codex.native.inventory", return_value={"codex": []}),
    ):
        asyncio.run(
            run(
                config,
                "test",
                lambda *args: None,
                {
                    "root": str(root),
                    "permissions": {"read": True, "write": True, "shell": True, "internet": True},
                    "access_mode": "full",
                },
                "fixture",
                "configured",
                tmp_path / "session",
                provider,
                approve,
            )
        )
    assert recorded["thread"]["sandbox"] == "danger-full-access"
    assert recorded["turn"]["sandboxPolicy"] == {"type": "dangerFullAccess"}
    assert recorded["thread"]["cwd"] == str(root)


def test_start_rechecks_providers_and_builds_native_config(tmp_path):
    import json
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    manager = Manager(tmp_path / "control")
    manager.settings["services"]["deepseek"].update(enabled=True, models=["first"])
    manager.inventory = {
        "binaries": {},
        "network": {},
        "services": [{"id": "deepseek", "binary": "fixture"}],
    }
    import socket

    with socket.socket() as port:
        port.bind(("127.0.0.1", 0))
        manager.settings["port"] = port.getsockname()[1]
    process = SimpleNamespace(returncode=None)

    async def check(provider):
        return {
            "authenticated": True,
            "models": {m: ["configured"] for m in manager.settings["services"][provider]["models"]},
        }

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, *args):
            return SimpleNamespace(status_code=200)

    with (
        patch.object(manager, "refresh", AsyncMock()) as refresh,
        patch.object(manager, "check", AsyncMock(side_effect=check)) as checked,
        patch(
            "asyncio.create_subprocess_exec",
            AsyncMock(side_effect=[process, SimpleNamespace(returncode=None)]),
        ),
        patch("httpx.AsyncClient", return_value=Client()),
    ):
        asyncio.run(manager.start())
        process.returncode = 0
        manager.settings["services"]["deepseek"]["models"] = ["new-model"]
        asyncio.run(manager.start())
    cfg = json.loads((manager.state / "runtime.json").read_text())
    assert refresh.await_count == checked.await_count == 2
    assert cfg["deepseek_models"] == {"new-model": ["configured"]}
    assert cfg["deepseek"]["unrestricted"] is True
    assert cfg["shared_projects"] is True


def test_claude_native_disables_sandbox(tmp_path):
    import json
    import sys

    exe = tmp_path / "fake-claude"
    exe.write_text(
        "#!"
        + sys.executable
        + "\n"
        + """import sys,json
json.loads(sys.stdin.readline())
print(json.dumps({'type':'result','subtype':'success','result':json.dumps(sys.argv[1:]),'session_id':'fixture'}),flush=True)
"""
    )
    exe.chmod(0o700)

    async def approve(*args):
        raise AssertionError("No inference or tools executed")

    with (
        patch("adapters.claude.native.configurations", return_value={"claude": {}}),
        patch("adapters.claude.native.inventory", return_value={"claude": []}),
    ):
        result = asyncio.run(
            run(
                {"binary": str(exe), "unrestricted": True},
                "fixture",
                lambda *args: None,
                {
                    "permissions": {"read": True, "write": True, "shell": True, "internet": True},
                    "access_mode": "full",
                },
                "fixture",
                "configured",
                tmp_path / "session",
                "claude",
                approve,
            )
        )
    command = json.loads(result["answer"])
    assert command[command.index("--permission-mode") + 1] == "bypassPermissions"
    assert json.loads(command[command.index("--settings") + 1])["sandbox"] == {"enabled": False}


@pytest.mark.parametrize("project_id", [None, "sem-projeto"])
def test_model_catalog_excludes_maestro_even_when_enabled(tmp_path, project_id):
    cfg = config(tmp_path)
    cfg["maestro_enabled"] = True
    app = create_app(cfg)
    with TestClient(app, headers={"Authorization": "Bearer a"}) as client:
        response = client.get("/v1/models", params={"project_id": project_id} if project_id else {})
        assert response.status_code == 200
        models = response.json()["models"]
        assert models
        assert all(model["backend"] != "maestro" for model in models)
        assert any(model["backend"] == "codex" for model in models)
