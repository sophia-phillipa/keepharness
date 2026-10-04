"""What a provider CLI may see of its host: a harness-owned home and the owner's opt-in.

Every Codex, DeepSeek and Claude run gets HOME and its config folder (CODEX_HOME,
CLAUDE_CONFIG_DIR) under the state folder (decisions D01, D02). The admin's sign-in writes
the login there and the CLIs keep their sessions there, so nothing is read from or copied
into the owner's ~/.codex or ~/.claude. The owner may opt into the personal setup: that run
then gets the owner's instructions, hooks and MCP servers on top, still in the same home.
"""

import json
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
    """HOME and the provider's config folder, created private; empty for older runtime files."""
    if not config.get("provider_homes"):
        return {}
    root = Path(config["provider_homes"])
    name, folder = CONFIG_FOLDERS[provider]
    paths = {"HOME": root / "home", name: root / folder}
    for path in (root, *paths.values()):
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return {key: str(path) for key, path in paths.items()}


def child_source(config, provider):
    """The source environment for ``child_environment``; None keeps the host's (older configs)."""
    homes = environment(config, provider)
    return {**os.environ, **homes} if homes else None


def login_environment(state, provider):
    """Sign-in and status checks keep the host session (a browser may open) in the harness home."""
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


def run_settings(config, backend, *, guest, data):
    """The per-run opt-in keys of a provider config (decision D01).

    Only the owner's own conversations carry the personal setup; guests and scheduled runs
    stay isolated. The owner's files are read here, once per run, so adapters never read them.
    """
    personal = config.get("personal_setup") is True and not guest and not data.get("schedule_id")
    settings = {"personal_setup": personal}
    if personal and backend in PERSONAL_INSTRUCTIONS:
        settings["personal_instructions"] = owner_file(PERSONAL_INSTRUCTIONS[backend])
    if personal and backend == "claude":
        try:
            hooks = json.loads(owner_file(".claude/settings.json") or "{}").get("hooks")
        except (ValueError, AttributeError):
            hooks = None
        settings["personal_hooks"] = hooks if isinstance(hooks, dict) else {}
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


def instructions(config):
    """The language rule, plus the owner's own instructions when this run carries them."""
    personal = (config.get("personal_instructions") or "").strip()
    if config.get("personal_setup") is not True or not personal:
        return LANGUAGE_RULE
    return LANGUAGE_RULE + "\nThe owner's personal instructions:\n" + personal
