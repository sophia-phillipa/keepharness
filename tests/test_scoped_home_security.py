"""Persistent worker homes cannot redirect privileged harness writes."""

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

from adapters.shared.scoped import prepare_scoped
from agent_service.tools import ToolError


@pytest.fixture
def scoped_config(tmp_path):
    binary = tmp_path / "fixture"
    binary.write_text("fixture")
    auth = tmp_path / "provider.json"
    auth.write_text("{}")
    return {"binary": str(binary), "auth_file": str(auth), "python": "/usr/bin/python3"}


@pytest.mark.parametrize("name", ["config.toml", "auth.json", ".credentials.json"])
def test_planted_home_symlinks_refused_without_modifying_host(tmp_path, scoped_config, name):
    host = tmp_path / "host-secret"
    host.write_text("unchanged")
    host.chmod(0o640)
    home = tmp_path / "session"
    home.mkdir()
    (home / name).symlink_to(host)
    auth_name = ".credentials.json" if name == ".credentials.json" else "auth.json"
    with pytest.raises(ToolError, match="unsafe_scoped_home"):
        with prepare_scoped(scoped_config, {}, {}, home, "codex", auth_name):
            pass
    assert host.read_text() == "unchanged"
    assert host.stat().st_mode & 0o777 == 0o640


@pytest.mark.parametrize("ancestor", [False, True])
def test_home_symlink_or_ancestor_refused(tmp_path, scoped_config, ancestor):
    host = tmp_path / "host"
    host.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(host, target_is_directory=True)
    home = alias / "session" if ancestor else alias
    with pytest.raises(ToolError, match="unsafe_scoped_home"):
        with prepare_scoped(scoped_config, {}, {}, home, "codex", "auth.json"):
            pass
    assert list(host.iterdir()) == []


def test_home_hardlink_refused_without_modifying_host(tmp_path, scoped_config):
    host = tmp_path / "host-secret"
    host.write_text("unchanged")
    home = tmp_path / "session"
    home.mkdir()
    os.link(host, home / "auth.json")
    with pytest.raises(ToolError, match="unsafe_scoped_home"):
        with prepare_scoped(scoped_config, {}, {}, home, "codex", "auth.json"):
            pass
    assert host.read_text() == "unchanged"


def test_replacement_race_cannot_truncate_host(tmp_path, monkeypatch):
    from adapters.shared.scoped import scoped_home_write

    home = tmp_path / "session"
    home.mkdir()
    target = home / "config.toml"
    target.write_text("old")
    host = tmp_path / "host"
    host.write_text("unchanged")
    replace = os.replace

    def planted_replace(source, destination, **kwargs):
        target.unlink()
        target.symlink_to(host)
        return replace(source, destination, **kwargs)

    monkeypatch.setattr(os, "replace", planted_replace)
    scoped_home_write(home, "config.toml", "new")
    assert host.read_text() == "unchanged"
    assert target.read_text() == "new"
    assert not target.is_symlink()
    assert target.stat().st_mode & 0o777 == 0o600


def test_directory_swap_does_not_redirect_write(tmp_path, monkeypatch):
    from adapters.shared.scoped import scoped_home_write

    home = tmp_path / "session"
    home.mkdir()
    original = tmp_path / "original-session"
    host = tmp_path / "host"
    host.mkdir()
    (host / "config.toml").write_text("unchanged")
    original_open = os.open

    def swapped_open(path, flags, *args, **kwargs):
        if str(path).startswith(".harness-"):
            home.rename(original)
            home.symlink_to(host, target_is_directory=True)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swapped_open)
    scoped_home_write(home, "config.toml", "new")
    assert (host / "config.toml").read_text() == "unchanged"
    assert (original / "config.toml").read_text() == "new"


def test_thread_marker_rejects_planted_links(tmp_path):
    from adapters.shared.scoped import scoped_home_read, scoped_home_write

    host = tmp_path / "host"
    host.write_text("unchanged")
    home = tmp_path / "session"
    home.mkdir()
    (home / "remote-thread.json").symlink_to(host)
    for operation in (
        lambda: scoped_home_read(home, "remote-thread.json"),
        lambda: scoped_home_write(home, "remote-thread.json", "new"),
    ):
        with pytest.raises(ToolError, match="unsafe_scoped_home"):
            operation()
    assert host.read_text() == "unchanged"


# Harness-owned provider homes (L04; PRD-R4-2 acceptance; decisions D01, D02, D30).

SENTINEL = "SENTINEL-PERSONAL-SETUP"

FAKE_CODEX = """
import json, os, sys
from pathlib import Path
record = {"argv": sys.argv, "env": dict(os.environ), "requests": []}
home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
def emit(value): print(json.dumps(value), flush=True)
for line in sys.stdin:
    request = json.loads(line)
    record["requests"].append(request)
    Path(RECORD_FILE).write_text(json.dumps(record))
    method, ident = request.get("method"), request.get("id")
    if method == "thread/start":
        sessions = home / "sessions" / "2026"
        sessions.mkdir(parents=True, exist_ok=True)
        (sessions / "rollout-thread-1.jsonl").write_text(line)
        emit({"id": ident, "result": {"thread": {"id": "thread-1"}}})
    elif method == "turn/start":
        emit({"method": "turn/started", "params": {"turn": {"id": "turn-1"}}})
        emit({"method": "item/agentMessage/delta", "params": {"delta": "done"}})
        emit({"method": "turn/completed", "params": {"turn": {"status": "completed"}}})
    elif ident is not None:
        emit({"id": ident, "result": {}})
"""

FAKE_CLAUDE = """
import json, os, sys
from pathlib import Path
prompt = sys.stdin.readline()
mcp = Path(sys.argv[sys.argv.index("--mcp-config") + 1]).read_text()
record = {"argv": sys.argv, "env": dict(os.environ), "requests": [prompt, mcp]}
home = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
projects = home / "projects" / "-workspace"
projects.mkdir(parents=True, exist_ok=True)
(projects / "session-1.jsonl").write_text(prompt)
Path(RECORD_FILE).write_text(json.dumps(record))
print(json.dumps({"type": "result", "subtype": "success", "result": "done", "session_id": "session-1"}), flush=True)
"""


def write_executable(path, body, record):
    # The record path is baked in: the child environment is allow-listed.
    path.write_text("#!" + sys.executable + "\nRECORD_FILE = " + repr(str(record)) + "\n" + body)
    path.chmod(0o700)
    return path


@pytest.fixture
def personal_home(tmp_path, monkeypatch):
    """A fake owner HOME holding instructions, a skill, a hook and an MCP server."""
    home = tmp_path / "personal-home"
    files = {
        ".codex/AGENTS.md": SENTINEL + " Codex instructions",
        ".codex/skills/demo/SKILL.md": "---\nname: demo\ndescription: " + SENTINEL + "\n---\nBody",
        ".codex/config.toml": '[mcp_servers.sentinel_mcp]\ncommand = "' + SENTINEL + '"\n',
        ".agents/skills/shared/SKILL.md": "---\nname: shared\ndescription: Shared\n---\nBody",
        ".claude/CLAUDE.md": SENTINEL + " Claude instructions",
        ".claude/skills/demo/SKILL.md": "---\nname: demo\ndescription: " + SENTINEL + "\n---\nBody",
        ".claude/settings.json": json.dumps(
            {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": SENTINEL}]}]}}
        ),
        ".claude.json": json.dumps({"mcpServers": {"sentinel_mcp": {"command": SENTINEL}}}),
    }
    for name, text in files.items():
        (home / name).parent.mkdir(parents=True, exist_ok=True)
        (home / name).write_text(text)
    monkeypatch.setenv("HOME", str(home))
    for name in ("CODEX_HOME", "CLAUDE_CONFIG_DIR"):
        monkeypatch.delenv(name, raising=False)
    return home


def snapshot(root):
    return sorted((str(path.relative_to(root)), path.stat().st_mtime_ns) for path in root.rglob("*"))


def provider_turn(tmp_path, provider, *, personal=False):
    """One stubbed native turn; returns what the fake CLI saw."""
    import adapters
    from adapters.shared.provider_setup import run_settings

    record = tmp_path / (provider + "-record.json")
    body = FAKE_CLAUDE if provider == "claude" else FAKE_CODEX
    binary = write_executable(tmp_path / ("fake-" + provider), body, record)
    config = {
        "binary": str(binary),
        "provider_homes": str(tmp_path / "state" / "providers"),
        "integrations": ["mcp:sentinel_mcp"],
        # What the dispatch adds for an owner's own conversation (guest=False, not scheduled).
        **run_settings({"personal_setup": personal}, provider, guest=False, data={}),
    }
    if provider == "deepseek":
        key = tmp_path / "deepseek.key"
        key.write_text("fixture-key")
        config["api_provider"] = {"url": "http://127.0.0.1:9/v1", "key_file": str(key)}
    session = tmp_path / "sessions" / provider
    session.mkdir(parents=True)

    async def approve(*_args):
        return {"approved": True}

    result = asyncio.run(
        adapters.run_native(
            config,
            "Hello",
            lambda *_: None,
            {"permissions": {"read": True, "hooks": True}, "access_mode": "auto"},
            "fixture",
            "low" if provider != "claude" else "configured",
            session,
            provider,
            approve,
        )
    )
    assert result["answer"] == "done"
    return json.loads(record.read_text())


def seen_text(record):
    return json.dumps(record)


@pytest.mark.parametrize("provider", ["codex", "deepseek", "claude"])
def test_sentinel_home_untouched_and_no_personal_setup_reaches_the_cli(
    tmp_path, personal_home, provider
):
    from adapters.shared.provider_setup import LANGUAGE_RULE

    before = snapshot(personal_home)
    record = provider_turn(tmp_path, provider)
    assert SENTINEL not in seen_text(record)
    assert snapshot(personal_home) == before
    assert not (personal_home / ".codex" / "sessions").exists()
    assert not (personal_home / ".claude" / "projects").exists()
    providers = tmp_path / "state" / "providers"
    assert Path(record["env"]["HOME"]).is_relative_to(providers)
    home_key = "CLAUDE_CONFIG_DIR" if provider == "claude" else "CODEX_HOME"
    assert Path(record["env"][home_key]).is_relative_to(providers)
    assert [path for path in providers.rglob("*") if path.suffix == ".jsonl"]
    assert LANGUAGE_RULE in seen_text(record)
    if provider == "claude":
        argv = record["argv"]
        assert argv[argv.index("--setting-sources") + 1] == "project"
        assert "hooks" not in json.loads(argv[argv.index("--settings") + 1])
    else:
        assert "features.hooks=false" in record["argv"]


@pytest.mark.parametrize("provider", ["codex", "claude"])
def test_owner_opt_in_brings_the_personal_setup_into_the_harness_home(
    tmp_path, personal_home, provider
):
    before = snapshot(personal_home)
    record = provider_turn(tmp_path, provider, personal=True)
    text = seen_text(record)
    assert SENTINEL + (" Claude" if provider == "claude" else " Codex") + " instructions" in text
    assert "sentinel_mcp" in text
    if provider == "claude":
        argv = record["argv"]
        assert argv[argv.index("--setting-sources") + 1] == "user,project"
        assert json.loads(argv[argv.index("--settings") + 1])["hooks"]["Stop"]
    else:
        assert "features.hooks=true" in record["argv"]
    # Session copies still stay inside the harness state (D01).
    assert snapshot(personal_home) == before


@pytest.mark.parametrize("personal", [False, True])
def test_user_scope_resources_follow_the_personal_setup(tmp_path, personal_home, personal):
    from agent_service import resources

    project = tmp_path / "project"
    project.mkdir()
    for backend in ("codex", "claude"):
        config = {
            "projects": {"p": {"root": str(project)}},
            "services": {backend: {"mode": "native"}},
            "control_state_dir": str(tmp_path / "state"),
            "personal_setup": personal,
        }
        users = [
            item
            for item in resources.discover(config, "p", backend)["items"]
            if item["scope"] == "user"
        ]
        assert bool(users) is personal
