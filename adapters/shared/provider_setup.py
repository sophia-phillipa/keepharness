"""Provider run environment and the shared response-language rule."""

import os
from pathlib import Path

from .process import harness_authority

# Decision D30, rule A: the answer language follows the person, not the host's private files.
LANGUAGE_RULE = "Answer in the language of the user's latest message unless they ask otherwise."
MAX_INSTRUCTIONS_BYTES = 64 * 1024
# Codex and Claude share one home; DeepSeek's Codex home never holds the ChatGPT login.
CONFIG_FOLDERS = {
    "codex": ("CODEX_HOME", "home/.codex"),
    "deepseek": ("CODEX_HOME", "deepseek"),
    "claude": ("CLAUDE_CONFIG_DIR", "home/.claude"),
}
PERSONAL_INSTRUCTIONS = {
    "codex": ".codex/AGENTS.md",
    "deepseek": ".codex/AGENTS.md",
    "claude": ".claude/CLAUDE.md",
}


def homes_root(state):
    return Path(state) / "providers"


def environment(config, provider):
    """Native Codex/Claude inherit the host; DeepSeek keeps its dedicated home."""
    if provider in ("codex", "claude") or not config.get("provider_homes"):
        return {}
    root = Path(config["provider_homes"])
    name, folder = CONFIG_FOLDERS[provider]
    paths = {"HOME": root / "home", name: root / folder}
    for path in (root, *paths.values()):
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return {key: str(path) for key, path in paths.items()}


def child_source(config, provider):
    """The source for ``child_environment``; None inherits the owner environment."""
    homes = environment(config, provider)
    return {**os.environ, **homes} if homes else None


def login_environment(state, provider):
    """Sign-in uses the same home as runs; a host browser may open."""
    homes = {"provider_homes": str(homes_root(state))} if state is not None else {}
    return {
        **{key: value for key, value in os.environ.items() if not harness_authority(key)},
        **environment(homes, provider),
    }


def credential_file(state, provider):
    """Where the admin's sign-in leaves the login that isolated runs copy."""
    name = "auth.json" if provider == "codex" else ".credentials.json"
    _, folder = CONFIG_FOLDERS[provider]
    return homes_root(state) / folder / name


def personal_setup_on(config, *, owner, schedule_id=None):
    """The effective personal setup of a run or view: opted in, the owner's own, not scheduled."""
    return config.get("personal_setup") is True and owner and not schedule_id


def run_settings(config, backend, *, data):
    """Only separate-home providers still need KeepHarness to supply personal instructions."""
    if backend in ("codex", "claude"):
        return {}
    personal = personal_setup_on(config, owner=True, schedule_id=data.get("schedule_id"))
    settings = {"personal_setup": personal}
    if personal and backend in PERSONAL_INSTRUCTIONS:
        settings["personal_instructions"] = owner_file(PERSONAL_INSTRUCTIONS[backend])
    return settings


def owner_file(name):
    """A bounded text file of the owner's own setup; empty when it is missing or unreadable."""
    try:
        with (Path.home() / name).open("rb") as stream:
            return stream.read(MAX_INSTRUCTIONS_BYTES).decode("utf-8", errors="replace")
    except OSError:
        return ""


def command_permissions(config, permissions):
    """Codex hooks run only for an owner who opted into the personal setup (decision D01)."""
    if config.get("personal_setup") is True:
        return permissions
    return {**permissions, "hooks": False}


def instructions(config, *, provider=None):
    """Native homes load their own instructions; DeepSeek retains its separate-home policy."""
    personal = (config.get("personal_instructions") or "").strip()
    if provider == "deepseek" and config.get("personal_setup") is True and personal:
        return LANGUAGE_RULE + "\nThe owner's personal instructions:\n" + personal
    return LANGUAGE_RULE


def version_notice(binary, provider, event, environment=None):
    """Warn without blocking runs when the installed CLI has drifted from tested versions."""
    from adapters.codex.state import _cli_version
    if provider == "codex":
        from adapters.codex.state import TESTED_VERSIONS, _tested
    else:
        from adapters.claude.state import TESTED_VERSIONS, _tested, _version_tuple
    version = _cli_version(binary, environment)
    tested = bool(version) and _tested(version if provider == "codex" else _version_tuple(version) or ())
    if not tested:
        event("provider_warning", {
            "backend": provider,
            "code": "provider_version_untested",
            "message": f"{provider.title()} {version or 'unknown'} is outside the tested range {TESTED_VERSIONS}. The run will continue.",
        })
