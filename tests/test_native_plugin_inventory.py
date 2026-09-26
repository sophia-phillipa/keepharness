from types import SimpleNamespace
from unittest.mock import patch

from Adapters.codex.native import RuntimeOptions, thread_parameters


def test_plugin_inventory_is_authoritative_and_only_selects_integrations():
    workspace = SimpleNamespace(cwd="/tmp/project", permissions={"read": True, "write": True})
    runtime = RuntimeOptions(command=["codex"])
    config = {
        "plugin_inventory": ["plugin:installed@marketplace", "plugin:other@marketplace"],
        "integrations": ["plugin:other@marketplace"],
    }

    with (
        patch("Adapters.codex.native.configurations", return_value={"codex": {}}),
        patch(
            "Adapters.codex.native.inventory",
            return_value={"codex": [{"id": "plugin:stale@marketplace", "kind": "plugin"}]},
        ),
    ):
        params = thread_parameters(config, {}, "fixture", workspace, runtime, False)

    assert params["config"]["plugins"] == {
        "installed@marketplace": {"enabled": False},
        "other@marketplace": {"enabled": True},
    }


def test_empty_plugin_inventory_disables_legacy_inventory_fallback():
    workspace = SimpleNamespace(cwd="/tmp/project", permissions={})
    runtime = RuntimeOptions(command=["codex"])

    with (
        patch("Adapters.codex.native.configurations", return_value={"codex": {}}),
        patch(
            "Adapters.codex.native.inventory",
            return_value={"codex": [{"id": "plugin:stale@marketplace", "kind": "plugin"}]},
        ),
    ):
        params = thread_parameters(
            {"plugin_inventory": [], "integrations": []},
            {},
            "fixture",
            workspace,
            runtime,
            False,
        )

    assert params["config"]["plugins"] == {}


def test_plugin_inventory_falls_back_to_legacy_catalog_when_absent():
    workspace = SimpleNamespace(cwd="/tmp/project", permissions={})
    runtime = RuntimeOptions(command=["codex"])

    with (
        patch("Adapters.codex.native.configurations", return_value={"codex": {}}),
        patch(
            "Adapters.codex.native.inventory",
            return_value={"codex": [{"id": "plugin:legacy@marketplace", "kind": "plugin"}]},
        ),
    ):
        params = thread_parameters(
            {"integrations": ["plugin:legacy@marketplace"]},
            {},
            "fixture",
            workspace,
            runtime,
            False,
        )

    assert params["config"]["plugins"] == {"legacy@marketplace": {"enabled": True}}


def test_isolated_runtime_does_not_read_or_expose_plugin_inventory():
    workspace = SimpleNamespace(cwd="/tmp/project", permissions={})
    runtime = RuntimeOptions(command=["codex"], isolated=True)

    with patch("Adapters.codex.native.inventory") as inventory:
        params = thread_parameters(
            {},
            {},
            "fixture",
            workspace,
            runtime,
            False,
        )

    inventory.assert_not_called()
    assert params["config"] == {"mcp_servers": {}, "plugins": {}}
