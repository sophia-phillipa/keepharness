"""Gemini discovery and routing do not silently use another account/provider."""

import asyncio
import copy
import json
from unittest.mock import AsyncMock, patch

import pytest

from agent_service.app import Service
from control.server import Manager
from tests.owner_session import sign_in


def test_discovery_finds_gemini_without_exporting_credentials(tmp_path):
    from control.discovery import scan

    auth = tmp_path / ".gemini" / "oauth_creds.json"
    auth.parent.mkdir()
    auth.write_text('{"token":"must-not-export"}')
    with (
        patch("control.discovery.Path.home", return_value=tmp_path),
        patch(
            "control.discovery.shutil.which",
            side_effect=lambda n: "/bin/gemini" if n == "gemini" else None,
        ),
        patch("control.discovery.discover", AsyncMock(return_value=[])),
    ):
        result = asyncio.run(scan())
    provider = next(s for s in result["services"] if s["id"] == "gemini")
    assert provider["found"] and provider["credential_present"]
    assert provider["binary"] == "/bin/gemini"
    assert "must-not-export" not in json.dumps(result)


def test_existing_settings_gain_disabled_gemini(tmp_path):
    (tmp_path / "settings.json").write_text(
        json.dumps({"services": {"claude": {"enabled": False}}})
    )
    manager = Manager(tmp_path)
    assert manager.settings["services"]["gemini"]["enabled"] is False
    assert manager.settings["services"]["gemini"]["mode"] == "native"


def test_gemini_config_and_assessment(tmp_path):
    manager = Manager(tmp_path / "control")
    settings = copy.deepcopy(manager.settings)
    settings["services"]["gemini"].update(enabled=True, models=["auto-gemini-3"])
    settings["default_backend"] = "gemini"
    validated = manager.validate(settings)
    assert validated["services"]["gemini"]["mode"] == "native"
    cfg = {
        "state_dir": str(tmp_path / "runs"),
        "services": validated["services"],
        "projects": {"sem-projeto": {}},
        "clients": {"u": {"projects": ["sem-projeto"]}},
        "gemini": {"binary": "/bin/gemini"},
        "gemini_models": ["auto-gemini-3"],
    }
    service = Service(cfg)
    try:
        identity = ("u", cfg["clients"]["u"])
        payload = {
            "project_id": "sem-projeto",
            "backend": "gemini",
            "model": "auto-gemini-3",
            "effort": "configured",
            "prompt": "test",
        }
        assert service.assess(identity, payload)["decision"] == "accept"
        assert service.assess(identity, {**payload, "effort": "high"})["decision"] == "unsupported"
    finally:
        service.db.close()


def test_gemini_mcp_inventory_contains_no_secrets(tmp_path):
    from control.integrations import inventory

    folder = tmp_path / ".gemini"
    folder.mkdir()
    (folder / "settings.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "drive": {"httpUrl": "https://example.test/mcp", "env": {"KEY": "secret-value"}}
                }
            }
        )
    )
    with patch("control.integrations.Path.home", return_value=tmp_path):
        result = inventory()
    assert result["gemini"] == [
        {
            "id": "mcp:drive",
            "name": "drive",
            "kind": "mcp",
            "transport": "http",
            "status": "configured",
        }
    ]
    assert "secret-value" not in json.dumps(result)


def test_gemini_check_uses_own_account_and_builds_native_config(tmp_path):
    manager = Manager(tmp_path / "control")
    binary = tmp_path / "gemini"
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o700)
    manager.inventory = {
        "services": [
            {
                "id": "gemini",
                "found": True,
                "binary": str(binary),
                "auth_file": str(tmp_path / "oauth_creds.json"),
            }
        ],
        "binaries": {"gemini": str(binary)},
        "network": {},
    }
    expected = {
        "authenticated": True,
        "models": {"auto-gemini-3": ["configured"]},
        "model_source": "CLI",
    }
    settings = copy.deepcopy(manager.settings)
    settings["services"]["gemini"].update(enabled=True, models=["auto-gemini-3"])
    with patch("adapters.gemini.account.check", AsyncMock(return_value=expected)) as check:
        config = asyncio.run(manager.build_runtime_config(settings))
    check.assert_awaited_once_with(str(binary))
    assert config["gemini"]["binary"] == str(binary)
    assert config["gemini_models"] == ["auto-gemini-3"]
    assert config["services"]["gemini"]["mode"] == "native"
    assert "codex" not in config and "claude" not in config


def test_gemini_credential_directory_cannot_be_shared(tmp_path):
    home = tmp_path / "home"
    protected = home / ".gemini"
    protected.mkdir(parents=True)
    manager = Manager(tmp_path / "control")
    data = copy.deepcopy(manager.settings)
    data["projects"] = [{"id": "credentials", "root": str(protected)}]
    with patch("pathlib.Path.home", return_value=home):
        with pytest.raises(ValueError, match="credentials"):
            manager.validate(data)


def test_gemini_session_marker_avoids_replaying_prior_turns(tmp_path):
    cfg = {
        "state_dir": str(tmp_path),
        "projects": {"p": {}},
        "clients": {"a": {"projects": ["p"]}},
        "services": {
            "gemini": {
                "enabled": True,
                "mode": "native",
                "models": ["auto-gemini-3"],
                "projects": ["p"],
                "permissions": {},
            }
        },
        "gemini": {"binary": "fixture"},
        "gemini_models": ["auto-gemini-3"],
    }
    service = Service(cfg)
    try:
        old = {
            "backend": "gemini",
            "model": "auto-gemini-3",
            "effort": "configured",
            "project_id": "p",
            "prompt": "earlier-prompt",
        }
        current = {**old, "prompt": "new-prompt", "parent_job_id": "first"}
        for ident, payload, result in [
            ("first", old, {"answer": "earlier-answer", "thread_id": "gemini-thread"}),
            ("second", current, {}),
        ]:
            service.db.execute(
                "INSERT INTO jobs(id,project,owner,state,created,payload,result) VALUES(?,?,?,?,?,?,?)",
                (ident, "p", "a", "completed", 1, json.dumps(payload), json.dumps(result)),
            )
        service.db.commit()
        session = tmp_path / "sessions" / "first" / "gemini"
        session.mkdir(parents=True)
        (session / "gemini-session.json").write_text('{"id":"gemini-thread"}')
        from agent_service.conversation_context import save_cursor

        save_cursor(session, "first", {"thread_id": "gemini-thread"}, "native")
        row = dict(service.db.execute("SELECT * FROM jobs WHERE id='second'").fetchone())

        async def run(config, prompt, event, project, model, effort, folder, provider, approve):
            assert provider == "gemini" and folder == session
            assert "new-prompt" in prompt
            assert "earlier-answer" not in prompt
            return {"answer": "new-answer", "backend": "gemini"}

        with patch("adapters.run_native", side_effect=run):
            assert asyncio.run(service.infer(row, current))["answer"] == "new-answer"
    finally:
        service.db.close()


def test_panel_gemini_login_launches_oauth_helper(tmp_path):
    import sys

    from starlette.testclient import TestClient

    from control.server import create_app

    inventory = {"services": [], "binaries": {"gemini": "/fixture/gemini"}, "network": {}}
    with (
        patch("control.discovery.scan", AsyncMock(return_value=inventory)),
        patch(
            "control.operations.Operations.launch", return_value={"id": "login", "state": "running"}
        ) as launch,
    ):
        with TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8094") as client:
            assert sign_in(client).get("/").status_code == 200
            response = client.post(
                "/api/provider-login", json={"provider": "gemini"}, headers={"X-Harness-Admin": "1"}
            )
            assert response.status_code == 200
            launch.assert_called_once_with(
                [sys.executable, "-m", "adapters.gemini.account", "--binary", "/fixture/gemini"],
                timeout=900,
            )


def test_panel_gemini_check_without_login_returns_200(tmp_path, monkeypatch):
    from starlette.testclient import TestClient

    from control.server import create_app

    monkeypatch.setenv("HOME", str(tmp_path))
    inventory = {
        "services": [{"id": "gemini", "found": True, "binary": "/unused/gemini"}],
        "binaries": {"gemini": "/unused/gemini"},
        "network": {},
    }
    with patch("control.discovery.scan", AsyncMock(return_value=inventory)):
        with TestClient(
            create_app(tmp_path / "control"), base_url="http://127.0.0.1:8094"
        ) as client:
            sign_in(client).get("/")
            response = client.post(
                "/api/check", json={"provider": "gemini"}, headers={"X-Harness-Admin": "1"}
            )
            assert response.status_code == 200
            assert response.json()["authenticated"] is False
            assert response.json()["error"] == "gemini_login_required"
