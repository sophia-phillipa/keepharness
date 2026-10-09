from types import SimpleNamespace

import pytest

from adapters.codex.native import RuntimeOptions, thread_parameters

# Old settings (0.15) still carry these; nothing may turn them into a plugin or MCP override (#46).
RETIRED_KEYS = [
    {},
    {"plugin_inventory": ["plugin:installed@marketplace"], "integrations": ["plugin:x@market"]},
    {"plugin_inventory": [], "integrations": []},
    {"integrations": ["plugin:legacy@marketplace", "mcp:fixture"], "personal_setup": True},
]


@pytest.mark.parametrize("config", RETIRED_KEYS)
def test_native_runs_leave_owner_plugins_and_servers_alone(config, tmp_path):
    workspace = SimpleNamespace(
        cwd="/tmp/project", permissions={"read": True, "write": True}, roots=[], home=tmp_path
    )
    params = thread_parameters(
        config, {}, "fixture", workspace, RuntimeOptions(command=["codex"]), False
    )

    assert "plugins" not in params["config"]
    assert not any(name != "harness_reader" for name in params["config"]["mcp_servers"])


def test_isolated_runtime_exposes_no_plugins():
    workspace = SimpleNamespace(cwd="/tmp/project", permissions={})
    runtime = RuntimeOptions(command=["codex"], isolated=True)

    params = thread_parameters({}, {}, "fixture", workspace, runtime, False)

    assert params["config"] == {"mcp_servers": {}, "plugins": {}}
