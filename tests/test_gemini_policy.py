import json
import tomllib
from unittest.mock import patch

import pytest

from agent_service.tools import ToolError


def test_policy_denies_unknown_tools_and_read_only_writes(tmp_path):
    from Adapters.gemini.policy import prepare

    with (
        patch("Adapters.gemini.policy.SYSTEM_POLICIES", tmp_path / "absent"),
        patch("Adapters.gemini.policy.SYSTEM_SETTINGS", tmp_path / "absent.json"),
    ):
        command, env = prepare(
            {"binary": "gemini"},
            tmp_path,
            {"read": True, "write": True, "shell": True, "internet": True},
            "read_only",
        )
    rules = tomllib.loads((tmp_path / "gemini-policy.toml").read_text())["rule"]
    assert rules[0]["toolName"] == "*" and rules[0]["decision"] == "deny"
    allowed = [r["toolName"] for r in rules if r["decision"] == "ask_user"]
    assert (
        "read_file" in allowed
        and "write_file" not in allowed
        and "run_shell_command" not in allowed
    )
    assert "--admin-policy" in command and "--approval-mode" in command
    settings = json.loads((tmp_path / "gemini-settings.json").read_text())
    assert settings["security"]["auth"]["enforcedType"] == "oauth-personal"
    assert settings["hooksConfig"]["enabled"] is False
    assert env["GEMINI_CLI_SYSTEM_SETTINGS_PATH"] == str(tmp_path / "gemini-settings.json")


def test_policy_removes_api_routing_and_filters_mcp(tmp_path):
    from Adapters.gemini.policy import prepare

    with (
        patch("Adapters.gemini.policy.SYSTEM_POLICIES", tmp_path / "absent"),
        patch("Adapters.gemini.policy.SYSTEM_SETTINGS", tmp_path / "absent.json"),
        patch.dict(
            "os.environ",
            {"GEMINI_API_KEY": "secret", "GOOGLE_GEMINI_BASE_URL": "https://elsewhere"},
        ),
        patch(
            "Adapters.gemini.policy.configurations",
            return_value={"gemini": {"drive": {}, "other": {}}},
        ),
    ):
        command, env = prepare(
            {"binary": "gemini", "integrations": ["mcp:drive"]}, tmp_path, {"internet": True}, "ask"
        )
    assert "GEMINI_API_KEY" not in env and "GOOGLE_GEMINI_BASE_URL" not in env
    assert command[command.index("--allowed-mcp-server-names") + 1] == "drive"
    rules = tomllib.loads((tmp_path / "gemini-policy.toml").read_text())["rule"]
    assert any(r.get("mcpName") == "drive" and r["decision"] == "ask_user" for r in rules)
    assert "secret" not in (tmp_path / "gemini-settings.json").read_text()


def test_existing_system_policy_is_not_bypassed(tmp_path):
    from Adapters.gemini.policy import prepare

    policies = tmp_path / "admin"
    policies.mkdir()
    (policies / "policy.toml").write_text("")
    with patch("Adapters.gemini.policy.SYSTEM_POLICIES", policies):
        with pytest.raises(ToolError, match="system_policy"):
            prepare({"binary": "gemini"}, tmp_path, {}, "ask")
