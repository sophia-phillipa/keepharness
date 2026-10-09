import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from adapters import run_native
from adapters.claude.native import build_command as claude_command
from agent_service.approval_policy import (
    MODES,
    effective_permissions,
    full_approval_allowed,
    rule_key,
)
from tests.deepseek_fixtures import SAFE_CONFIG, write_deepseek_key


def test_full_is_valid_but_does_not_expand_grants():
    original = {"read": True, "write": False, "shell": False, "internet": False}
    assert "full" in MODES
    assert effective_permissions(original, "full") == original


def test_full_only_auto_approves_actions_inside_existing_grants():
    permissions = {"read": True, "write": False, "shell": True, "internet": False}
    assert full_approval_allowed("item/commandExecution/requestApproval", {}, permissions)
    assert not full_approval_allowed("applyPatchApproval", {}, permissions)
    assert not full_approval_allowed(
        "permissions/requestApproval", {"permissions": {"network": True}}, permissions
    )
    assert full_approval_allowed("claude/can_use_tool", {"tool_name": "Read"}, permissions)
    assert not full_approval_allowed("claude/can_use_tool", {"tool_name": "WebFetch"}, permissions)


def test_read_only_cannot_expand_admin_grants():
    original = {"read": True, "write": True, "shell": True, "internet": False, "hooks": True}
    actual = effective_permissions(original, "read_only")
    assert actual == {
        "read": True,
        "write": False,
        "shell": False,
        "internet": False,
        "hooks": False,
        "tests": False,
    }
    assert original["write"] is True


def test_remembered_command_is_exact_and_permission_bound():
    kind = "item/commandExecution/requestApproval"
    request = {"command": "curl https://example.org", "cwd": "/project"}
    key = rule_key(kind, request, {"internet": True})
    assert key == rule_key(kind, {**request, "threadId": "different"}, {"internet": True})
    assert key != rule_key(
        kind, {**request, "command": "curl https://other.org"}, {"internet": True}
    )
    assert key != rule_key(kind, {**request, "cwd": "/other"}, {"internet": True})
    assert key != rule_key(kind, request, {"internet": False})
    assert rule_key("item/tool/requestUserInput", request, {}) is None


def test_codex_file_change_approval_follows_the_write_grant():
    kind = "item/fileChange/requestApproval"
    assert full_approval_allowed(kind, {"itemId": "patch-1"}, {"write": True})
    assert not full_approval_allowed(kind, {"itemId": "patch-1"}, {"write": False})
    assert rule_key(kind, {"itemId": "patch-1"}, {"write": True}) is None


# -- connector gating per access mode (decisions D04 and D12) ------------------------------------

ALL_GRANTS = {"read": True, "write": True, "shell": True, "internet": True}
HOST_SERVERS = {"node_repl": {"command": "node"}, "github": {"url": "https://example.invalid/mcp"}}
SELECTED = ["mcp:node_repl", "mcp:github", "plugin:notes@market"]


def run_codex_route(tmp_path, provider, project, backend_config=None):
    """One fake app-server turn; returns (command, thread/start params, turn/start params)."""
    recorded = {}

    class RPC:
        process = SimpleNamespace(stdin=SimpleNamespace(write=lambda value: None))

        async def call(self, method, params):
            if method == "config/read":
                return SAFE_CONFIG
            recorded[method] = params
            return {"thread": {"id": "fixture"}}

        async def send(self, method, params):
            recorded[method] = params

        async def receive(self):
            return {"method": "turn/completed", "params": {"turn": {"status": "completed"}}}

    @asynccontextmanager
    async def connection(command, **kwargs):
        recorded["command"] = command
        yield RPC()

    key = tmp_path / "deepseek.key"
    write_deepseek_key(key)
    config = {
        "binary": "fixture",
        "unrestricted": True,
        "integrations": SELECTED,
        "plugin_inventory": ["plugin:notes@market"],
        "personal_setup": True,  # host connectors and plugins are the owner's opt-in (D01)
        **(
            {"api_provider": {"url": "https://example.invalid", "key_file": str(key)}}
            if provider == "deepseek"
            else {}
        ),
        **(backend_config or {}),
    }
    with (
        patch("adapters.codex.native.connection", connection),
        patch("adapters.codex.native.configurations", return_value={"codex": HOST_SERVERS}),
        patch("adapters.codex.native.inventory", return_value={"codex": []}),
    ):
        asyncio.run(
            run_native(
                config,
                "fixture",
                lambda *args: None,
                project,
                "fixture",
                "high",
                tmp_path / "session",
                provider,
                lambda *args: None,
            )
        )
    return recorded["command"], recorded["thread/start"], recorded["turn/start"]


@pytest.mark.parametrize("provider", ["codex", "deepseek"])
@pytest.mark.parametrize("mode", ["ask", "auto", "full", "read_only"])
def test_mcp_gating_matrix(tmp_path, provider, mode):
    root = tmp_path / "project"
    root.mkdir()
    project = {
        "root": str(root),
        "permissions": effective_permissions(ALL_GRANTS, mode),
        "access_mode": mode,
    }
    command, thread, turn = run_codex_route(tmp_path, provider, project)
    servers = thread["config"]["mcp_servers"]
    host = {name: servers[name] for name in HOST_SERVERS}
    if mode == "read_only":
        # Read only enables no host connector and no plugin; the harness reader replaces them.
        assert not any(spec["enabled"] for spec in host.values())
        assert thread["config"]["plugins"] == {"notes@market": {"enabled": False}}
        assert "features.shell_tool=false" in command
        reader = servers["harness_reader"]
        assert reader["enabled"] is True
        assert reader["default_tools_approval_mode"] == "approve"
        assert reader["args"][-1:] == [str(root.resolve())]
    else:
        assert all(spec["enabled"] for spec in host.values())
        assert "harness_reader" not in servers
    if mode == "ask":
        # Ask never runs a connector tool without a card.
        assert {spec["default_tools_approval_mode"] for spec in host.values()} == {"prompt"}
        assert thread["approvalPolicy"] == turn["approvalPolicy"] == "on-request"


@pytest.mark.parametrize("mode", ["ask", "auto", "full", "read_only"])
def test_claude_read_only_loads_no_connector_or_plugin(tmp_path, mode):
    with (
        patch("adapters.claude.native.configurations", return_value={"claude": HOST_SERVERS}),
        patch(
            "adapters.claude.native.inventory",
            return_value={"claude": [{"id": "plugin:notes@market", "kind": "plugin"}]},
        ),
    ):
        command = claude_command(
            {"binary": "claude", "unrestricted": True, "personal_setup": True},
            "fixture",
            tmp_path,
            effective_permissions(ALL_GRANTS, mode),
            SELECTED,
            mode,
            [],
        )
    servers = json.loads((tmp_path / "mcp.json").read_text())["mcpServers"]
    plugins = json.loads(command[command.index("--settings") + 1])["enabledPlugins"]
    if mode == "read_only":
        assert servers == {} and plugins == {"notes@market": False}
    else:
        assert set(servers) == set(HOST_SERVERS) and plugins == {"notes@market": True}
