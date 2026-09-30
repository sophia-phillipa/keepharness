"""Per-run Gemini CLI restrictions, above workspace and user policy tiers.

Validated against Gemini CLI 0.60.0 policy/config and settings merge code.
This is native tool authorization, not an operating-system sandbox.
"""

import json
import os
import uuid
from pathlib import Path

from adapters.shared.process import child_environment
from agent_service.tools import ToolError
from control.integrations import configurations

SYSTEM_POLICIES = Path("/etc/gemini-cli/policies")
SYSTEM_SETTINGS = Path("/etc/gemini-cli/settings.json")
TOOLS = {
    "read": ("read_file", "read_many_files", "list_directory", "glob", "grep_search"),
    "write": ("write_file", "replace"),
    "shell": ("run_shell_command",),
    "internet": ("google_web_search", "web_fetch"),
}


def prepare(config, home, permissions, access_mode):
    """Return command/env without modifying the user's CLI settings or credentials."""
    if access_mode not in ("ask", "auto", "full", "read_only"):
        raise ToolError("gemini_access_mode_invalid")
    # CLI ignores --admin-policy whenever system policy files exist. Refuse this
    # unsupported combination instead of silently running without our restrictions.
    if any(SYSTEM_POLICIES.glob("*.toml")):
        raise ToolError("gemini_system_policy_conflict")
    settings_path = Path(os.environ.get("GEMINI_CLI_SYSTEM_SETTINGS_PATH", SYSTEM_SETTINGS))
    try:
        settings = json.loads(settings_path.read_text()) if settings_path.exists() else {}
        if not isinstance(settings, dict):
            raise ValueError()
        if settings.get("adminPolicyPaths"):
            raise ToolError("gemini_system_policy_conflict")
        security = settings.setdefault("security", {})
        auth = security.setdefault("auth", {})
        if auth.get("enforcedType") not in (None, "oauth-personal"):
            raise ToolError("gemini_system_auth_conflict")
        auth.update(selectedType="oauth-personal", enforcedType="oauth-personal", useExternal=False)
        settings.setdefault("hooksConfig", {})["enabled"] = (
            bool(permissions.get("hooks")) and access_mode != "read_only"
        )
        settings.setdefault("skills", {})["enabled"] = False
        settings.setdefault("experimental", {}).update(enableAgents=False, autoMemory=False)
    except (ValueError, OSError, TypeError, AttributeError):
        raise ToolError("gemini_system_settings_invalid") from None
    selected = config.get("integrations", [])
    available = configurations().get("gemini", {}) if selected else {}
    if not isinstance(selected, list) or any(
        not isinstance(s, str) or not s.startswith("mcp:") or s[4:] not in available
        for s in selected
    ):
        raise ToolError("gemini_integration_unavailable")
    if selected and not permissions.get("internet"):
        raise ToolError("gemini_integration_denied")
    names = [s[4:] for s in selected] if access_mode != "read_only" else []
    rules = ['[[rule]]\ntoolName = "*"\ndecision = "deny"\npriority = 900\n']
    for permission, tools in TOOLS.items():
        if not permissions.get(permission) or (
            access_mode == "read_only" and permission in ("write", "shell")
        ):
            continue
        for tool in tools:
            rules.append(
                f'[[rule]]\ntoolName = {json.dumps(tool)}\ndecision = "ask_user"\npriority = 950\n'
            )
    for name in names:
        rules.append(
            f'[[rule]]\nmcpName = {json.dumps(name)}\ntoolName = "*"\ndecision = "ask_user"\npriority = 950\n'
        )
    home = Path(home)
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    policy_file, override = home / "gemini-policy.toml", home / "gemini-settings.json"
    policy_file.write_text("\n".join(rules))
    policy_file.chmod(0o600)
    override.write_text(json.dumps(settings))
    override.chmod(0o600)
    env = child_environment()
    for key in (
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "GOOGLE_GEMINI_BASE_URL",
        "GOOGLE_VERTEX_BASE_URL",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GEMINI_CLI_USE_COMPUTE_ADC",
        "CLOUD_SHELL",
        "GEMINI_CLI_HOME",
    ):
        env.pop(key, None)
    env.update(
        GEMINI_CLI_SYSTEM_SETTINGS_PATH=str(override), GEMINI_CLI_NO_RELAUNCH="1", NO_BROWSER="true"
    )
    command = [
        config["binary"],
        "--acp",
        "--extensions",
        "none",
        "--allowed-mcp-server-names",
        *(names or ["tail-harness-none-" + uuid.uuid4().hex]),
        "--admin-policy",
        str(policy_file),
        "--approval-mode",
        "default",
    ]
    return command, env
