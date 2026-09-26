"""Browser renewal chooses CLI credentials only after successful login."""

import asyncio
import os
import sys
from unittest.mock import AsyncMock, patch

import pytest
from starlette.testclient import TestClient

from Adapters.claude.auth import cli_login_environment
from control.operations import Operations
from control.server import Manager, create_app


def test_login_environment_does_not_modify_parent(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "stale-fixture")
    monkeypatch.setenv("KEEP_TEST_VARIABLE", "kept")
    env = cli_login_environment()
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in env
    assert env["KEEP_TEST_VARIABLE"] == "kept"
    assert os.environ["CLAUDE_CODE_OAUTH_TOKEN"] == "stale-fixture"


@pytest.mark.parametrize("exit_code", [0, 1])
def test_login_applies_credential_preference_only_on_success(tmp_path, monkeypatch, exit_code):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "stale-fixture")
    manager = Manager(tmp_path)
    manager._write_runtime({"claude": {"binary": "/fixture"}, "other": "preserved"})

    async def exercise():
        operations = Operations()
        job = operations.launch(
            [
                sys.executable,
                "-c",
                f'import os,sys; assert "CLAUDE_CODE_OAUTH_TOKEN" not in os.environ; sys.exit({exit_code})',
            ],
            env=cli_login_environment(),
            on_success=manager.claude_login_completed,
        )
        await asyncio.gather(*operations.tasks)
        assert job["state"] == ("completed" if exit_code == 0 else "failed")
        assert (tmp_path / "claude-cli-login").exists() == (exit_code == 0)
        runtime = manager._previous_runtime()
        assert runtime["claude"].get("use_cli_login", False) == (exit_code == 0)
        assert runtime["other"] == "preserved"
        assert manager.auth.get("claude") is (True if exit_code == 0 else None)

    asyncio.run(exercise())


def test_cancelled_login_keeps_existing_credentials(tmp_path):
    async def exercise():
        manager = Manager(tmp_path)
        operations = Operations()
        job = operations.launch(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            on_success=manager.claude_login_completed,
        )
        await asyncio.sleep(0.05)
        operations.cancel(job["id"])
        await asyncio.gather(*operations.tasks, return_exceptions=True)
        assert job["state"] == "cancelled"
        assert not (tmp_path / "claude-cli-login").exists()

    asyncio.run(exercise())


def test_login_endpoint_deduplicates_and_requires_admin(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "stale-fixture")
    inventory = {"services": [], "binaries": {"claude": "/fixture/claude"}, "network": {}}

    def launch(operations, args, **kwargs):
        assert args == ["/fixture/claude", "auth", "login"]
        assert "CLAUDE_CODE_OAUTH_TOKEN" not in kwargs["env"]
        assert callable(kwargs["on_success"])
        job = {"id": "login", "state": "running", "output": ""}
        operations.jobs["login"] = job
        return job

    with (
        patch("control.server.scan", AsyncMock(return_value=inventory)),
        patch.object(Operations, "launch", launch),
    ):
        with TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8094") as client:
            assert (
                client.post("/api/provider-login", json={"provider": "claude"}).status_code == 401
            )
            client.get("/")
            assert (
                client.post("/api/provider-login", json={"provider": "claude"}).status_code == 400
            )
            headers = {"X-Harness-Admin": "1"}
            one = client.post("/api/provider-login", json={"provider": "claude"}, headers=headers)
            two = client.post("/api/provider-login", json={"provider": "claude"}, headers=headers)
            assert one.status_code == two.status_code == 200
            assert one.json()["id"] == two.json()["id"]


def test_renewed_account_check_ignores_inherited_token(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "stale-fixture")
    manager = Manager(tmp_path)
    manager.inventory = {"services": [{"id": "claude", "found": True, "binary": "/fixture"}]}
    asyncio.run(manager.claude_login_completed())
    with (
        patch(
            "control.server.command", AsyncMock(return_value=(0, '{"loggedIn":true}'))
        ) as command,
        patch(
            "Adapters.claude.account.metadata",
            AsyncMock(return_value={"models": [{"value": "sonnet"}]}),
        ),
    ):
        assert asyncio.run(manager.check("claude"))["authenticated"]
        assert "CLAUDE_CODE_OAUTH_TOKEN" not in command.call_args.kwargs["env"]


@pytest.mark.parametrize("use_cli_login", [False, True])
def test_native_run_uses_the_selected_auth_source(tmp_path, monkeypatch, use_cli_login):
    from Adapters.claude import native

    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "stale-fixture")
    executable = tmp_path / "claude-fixture"
    executable.write_text(
        "#!" + sys.executable + "\nimport sys,json,os\n"
        "json.loads(sys.stdin.readline())\n"
        'print(json.dumps({"type":"result","subtype":"success","result":"env" if "CLAUDE_CODE_OAUTH_TOKEN" in os.environ else "cli"}),flush=True)\n'
    )
    executable.chmod(0o700)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(native, "configurations", lambda: {"claude": {}})
    monkeypatch.setattr(native, "inventory", lambda: {"claude": []})
    result = asyncio.run(
        native.run(
            {"binary": str(executable), "use_cli_login": use_cli_login},
            "fixture",
            lambda *_: None,
            tmp_path,
            "sonnet",
            home,
            {},
            [],
            AsyncMock(),
        )
    )
    assert result["answer"] == ("cli" if use_cli_login else "env")


def test_pending_claude_login_does_not_block_native_harness_startup(tmp_path):
    manager = Manager(tmp_path)
    manager.settings["services"]["claude"].update(enabled=True, models=["sonnet"])
    manager.inventory = {
        "network": {},
        "binaries": {},
        "services": [
            {
                "id": "claude",
                "binary": sys.executable,
                "auth_file": str(tmp_path / "missing-credentials"),
            }
        ],
    }
    with patch.object(
        manager,
        "check",
        AsyncMock(return_value={"authenticated": False, "models": {"sonnet": ["configured"]}}),
    ):
        runtime = asyncio.run(manager.build_runtime_config(manager.settings))
    assert runtime["services"]["claude"]["enabled"]
    assert runtime["services"]["claude"]["mode"] == "native"
    assert runtime["claude_models"] == {"sonnet": ["configured"]}
