import asyncio
import copy
import sys
from unittest.mock import AsyncMock, patch

from control.server import Manager


def test_cli_installed_plugins_drive_inventory_validation_and_runtime(tmp_path):
    manager = Manager(tmp_path)
    installed = [
        {
            "id": "plugin:drive@market",
            "name": "Drive",
            "kind": "plugin",
            "status": "installed",
            "enabled": True,
        }
    ]
    legacy = {
        "codex": [
            {"id": "mcp:kept", "kind": "mcp"},
            {"id": "plugin:stale@market", "kind": "plugin"},
        ]
    }
    discovered = {
        "binaries": {"codex": "/bin/codex"},
        "network": {},
        "services": [
            {"id": "codex", "binary": sys.executable, "auth_file": str(tmp_path / "auth.json")}
        ],
    }
    with (
        patch("control.integrations.inventory", return_value=legacy),
        patch("control.discovery.scan", AsyncMock(return_value=discovered)),
        patch("control.integration_catalog.installed_plugins", AsyncMock(return_value=installed)),
    ):
        asyncio.run(manager.refresh())
        items = manager.integrations()
        assert [x["id"] for x in items["codex"]] == ["mcp:kept", "plugin:drive@market"]
        assert items["local"] == items["deepseek"] == items["codex"]
        settings = copy.deepcopy(manager.settings)
        settings["services"]["codex"]["integrations"] = ["plugin:drive@market"]
        assert manager.validate(settings)["services"]["codex"]["integrations"] == [
            "plugin:drive@market"
        ]
        settings["services"]["codex"].update(enabled=True, models=["fixture"])
        with patch.object(
            manager,
            "check",
            AsyncMock(return_value={"authenticated": True, "models": {"fixture": ["low"]}}),
        ):
            config = asyncio.run(manager.build_runtime_config(settings))
        assert config["codex"]["plugin_inventory"] == ["plugin:drive@market"]
        with patch("control.integration_catalog.installed_plugins", AsyncMock(return_value=None)):
            asyncio.run(manager.refresh())
        assert manager.integrations()["codex"] == items["codex"]
        assert manager.inventory["integration_warnings"]


def test_installed_only_query_never_admits_remote_catalog_entries():
    from control.integration_catalog import installed_plugins

    payload = '{"installed":[{"pluginId":"drive@market","name":"Drive","enabled":true}],"available":[{"pluginId":"remote@market","name":"Remote"}]}'
    with patch("control.integration_catalog._run", AsyncMock(return_value=(0, payload))) as command:
        items = asyncio.run(installed_plugins("/bin/codex"))
    command.assert_awaited_once_with("/bin/codex", "plugin", "list", "--json")
    assert [x["id"] for x in items] == ["plugin:drive@market"]
