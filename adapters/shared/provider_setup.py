"""Provider run environment and the shared response-language rule."""

import os

from .process import harness_authority

# Decision D30, rule A: the answer language follows the person, not the host's private files.
LANGUAGE_RULE = "Answer in the language of the user's latest message unless they ask otherwise."


def login_environment():
    """The host environment for a sign-in or status check; a host browser may open."""
    return {key: value for key, value in os.environ.items() if not harness_authority(key)}


def command_permissions(permissions):
    """DeepSeek's own home runs no hooks (decision D01); Codex and Claude follow the CLI."""
    return {**permissions, "hooks": False}


def version_notice(binary, provider, event, environment=None):
    """Warn without blocking runs when the installed CLI has drifted from tested versions."""
    from adapters.codex.state import _cli_version

    if provider == "codex":
        from adapters.codex.state import TESTED_VERSIONS, _tested
    else:
        from adapters.claude.state import TESTED_VERSIONS, _tested, _version_tuple
    version = _cli_version(binary, environment)
    tested = bool(version) and _tested(
        version if provider == "codex" else _version_tuple(version) or ()
    )
    if not tested:
        event(
            "provider_warning",
            {
                "backend": provider,
                "code": "provider_version_untested",
                "message": f"{provider.title()} {version or 'unknown'} is outside the tested range {TESTED_VERSIONS}. The run will continue.",
            },
        )


def codex_access_settings(mode, permissions, unrestricted=False, isolated=False):
    """Native settings shared by the app-server request and the Access menu."""
    ask = mode == "ask" and not isolated
    return {
        "sandbox": (
            "danger-full-access"
            if unrestricted
            else "workspace-write"
            if permissions.get("write") and not ask
            else "read-only"
        ),
        "approvalPolicy": (
            "never" if mode in ("full", "read_only") and not isolated else "on-request"
        ),
    }


def claude_access_settings(mode, permissions, unrestricted=False):
    """Native permission mode shared by the CLI command and the Access menu."""
    if mode == "full":
        return {
            "permissionMode": "bypassPermissions"
            if unrestricted and permissions.get("shell")
            else "dontAsk"
        }
    return {"permissionMode": {"ask": "default", "auto": "acceptEdits", "read_only": "plan"}[mode]}
