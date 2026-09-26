"""Defaults never override explicit choices or grant project access."""

from unittest.mock import AsyncMock, patch

import pytest
from starlette.testclient import TestClient

from agent_service.execution_defaults import resolve
from control.server import Manager, create_app
from tail_ui import ASSETS, PUBLIC, asset_response


def configuration():
    return {
        "services": {
            "codex": {
                "enabled": True,
                "models": ["sol", "luna"],
                "projects": ["p"],
                "permissions": {},
            },
            "local": {"enabled": True, "models": ["qwen"], "projects": ["p"], "permissions": {}},
        },
        "codex_models": {"sol": ["low", "high"], "luna": ["low", "medium"]},
        "mcp_defaults": {"backend": "codex", "model": "sol", "effort": "high"},
    }


def test_omitted_fields_use_defaults():
    result = resolve(configuration(), {"project_id": "p"})
    assert (result["backend"], result["model"], result["effort"]) == ("codex", "sol", "high")


def test_explicit_model_or_effort_win():
    assert resolve(configuration(), {"project_id": "p", "effort": "low"})["effort"] == "low"
    r = resolve(configuration(), {"project_id": "p", "model": "luna"})
    assert (r["model"], r["effort"]) == ("luna", "low")
    r = resolve(configuration(), {"project_id": "p", "backend": "local"})
    assert (r["model"], r["effort"]) == ("qwen", "configured")
    assert (
        resolve(
            configuration(),
            {"project_id": "p", "backend": "codex", "model": "luna", "effort": "medium"},
        )
        is None
    )


def test_unavailable_defaults_fail_without_changing_executor():
    cfg = configuration()
    with pytest.raises(ValueError, match="not_available"):
        resolve(cfg, {"project_id": "forbidden"})
    cfg["mcp_defaults"]["effort"] = "ultra"
    with pytest.raises(ValueError, match="effort_not_available"):
        resolve(cfg, {"project_id": "p"})
    cfg["mcp_defaults"] = {}
    assert resolve(cfg, {"project_id": "p"}) is None


def test_defaults_validation_and_private_persistence(tmp_path):
    manager = Manager(tmp_path)
    settings = manager.settings
    settings["services"]["local"].update(enabled=True, models=["qwen"])
    settings["mcp_defaults"] = {"backend": "local", "model": "qwen", "effort": "configured"}
    manager.save(settings)
    assert Manager(tmp_path).settings["mcp_defaults"] == settings["mcp_defaults"]
    settings["mcp_defaults"]["model"] = "unavailable"
    with pytest.raises(ValueError):
        manager.save(settings)


def test_shared_assets_whitelist():
    assert all((ASSETS / name).is_file() for name in PUBLIC)
    assert asset_response("/assets/../__init__.py").status_code == 404
    assert asset_response("/assets/not-found.js").status_code == 404


def test_admin_assets_keep_host_boundary(tmp_path):
    with patch("control.server.scan", AsyncMock(return_value={"services": [], "network": {}})):
        with TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8094") as client:
            assert client.get("/assets/themes.css").status_code == 200
            assert (
                client.get("/assets/theme.js", headers={"Host": "evil.example"}).status_code == 403
            )
            assert client.get("/assets/nope").status_code == 404
