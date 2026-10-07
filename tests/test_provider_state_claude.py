"""Claude Code state reader: layering, skills, MCP, safety and the fake `claude` CLI."""

import json
import logging
import os
import subprocess
from pathlib import Path

import pytest

from adapters.claude.state import ClaudeStateAdapter
from adapters.shared.provider_state import (
    ProviderStateAdapter,
    ProviderStateSchemaError,
    ProviderStateUnsupportedError,
)

FAKE_CLAUDE_DIR = Path(__file__).parent / "fixtures" / "fake-claude"
SESSION = "sk-ant-oat01-SESSION-VALUE-1234"
SECRET_ENV = "ENV-SECRET-VALUE"
SECRET_HEADER = "Bearer HEADER-SECRET-VALUE"


def write_json(path: Path, document: dict, indent: int = 2) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=indent) + "\n")
    return path


def make_skill(folder: Path, name: str) -> Path:
    skill = folder / name
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(f"---\nname: {name}\n---\nBody.\n")
    return skill


@pytest.fixture(autouse=True)
def fake_claude_on_path(monkeypatch, isolated_provider_homes):
    monkeypatch.setenv("PATH", f"{FAKE_CLAUDE_DIR}{os.pathsep}{os.environ['PATH']}")


@pytest.fixture
def home(isolated_provider_homes) -> Path:
    return isolated_provider_homes


@pytest.fixture
def config_dir(home) -> Path:
    return home / ".claude"


@pytest.fixture
def project(tmp_path) -> Path:
    folder = tmp_path / "project"
    folder.mkdir()
    return folder


@pytest.fixture
def managed(tmp_path) -> Path:
    return tmp_path / "managed"


@pytest.fixture
def adapter(tmp_path, managed) -> ClaudeStateAdapter:
    return ClaudeStateAdapter(tmp_path / "state", managed)


def by_id(snapshot) -> dict:
    return {item.id: item for item in snapshot.items}


def test_adapter_satisfies_the_protocol(adapter):
    assert isinstance(adapter, ProviderStateAdapter)


def test_empty_home_gives_an_empty_snapshot(adapter, project):
    snapshot = adapter.read_state(project)
    assert snapshot.items == ()
    assert snapshot.warnings == ()
    assert (snapshot.provider, snapshot.engine, snapshot.project_root) == (
        "claude",
        "claude",
        str(project),
    )
    assert snapshot.cli_version == "2.1.292"


def test_plugin_layers_resolve_user_project_local_managed(
    adapter, config_dir, project, managed, home
):
    write_json(
        config_dir / "settings.json",
        {
            "enabledPlugins": {"a@m": True, "b@m": True, "c@m": True, "d@m": True, "e@m": False},
            "futureKey": {"x": 1},
        },
        indent=4,
    )
    write_json(project / ".claude" / "settings.json", {"enabledPlugins": {"b@m": False}})
    write_json(
        project / ".claude" / "settings.local.json",
        {"enabledPlugins": {"c@m": False, "e@m": True}},
    )
    write_json(managed / "managed-settings.json", {"enabledPlugins": {"a@m": False}})
    items = by_id(adapter.read_state(project))
    shape = {
        key: (item.enabled, item.scope, item.source, item.writable)
        for key, item in items.items()
        if item.kind == "plugin"
    }
    assert shape == {
        "plugin:a@m": (False, "managed", str(managed / "managed-settings.json"), False),
        "plugin:b@m": (False, "project", ".claude/settings.json", True),
        "plugin:c@m": (False, "local", ".claude/settings.local.json", True),
        "plugin:d@m": (True, "user", "~/.claude/settings.json", True),
        "plugin:e@m": (True, "local", ".claude/settings.local.json", True),
    }
    assert items["plugin:a@m"].reason
    assert items["plugin:b@m"].reason == ""


def test_without_a_project_only_user_and_managed_layers_count(
    adapter, config_dir, project, managed
):
    write_json(config_dir / "settings.json", {"enabledPlugins": {"a@m": True, "b@m": True}})
    write_json(project / ".claude" / "settings.json", {"enabledPlugins": {"b@m": False}})
    write_json(managed / "managed-settings.json", {"enabledPlugins": {"c@m": True}})
    snapshot = adapter.read_state(None)
    assert snapshot.project_root is None
    assert {k: (i.enabled, i.scope) for k, i in by_id(snapshot).items()} == {
        "plugin:a@m": (True, "user"),
        "plugin:b@m": (True, "user"),
        "plugin:c@m": (True, "managed"),
    }


def test_managed_drop_ins_override_the_base_file_in_name_order(adapter, managed):
    write_json(managed / "managed-settings.json", {"enabledPlugins": {"a@m": True}})
    write_json(managed / "managed-settings.d" / "10-one.json", {"enabledPlugins": {"a@m": False}})
    write_json(managed / "managed-settings.d" / "20-two.json", {"enabledPlugins": {"a@m": True}})
    item = by_id(adapter.read_state(None))["plugin:a@m"]
    assert (item.enabled, item.scope, item.writable) == (True, "managed", False)
    assert item.source.endswith("20-two.json")


def test_plugin_values_that_are_not_booleans_are_ignored_with_a_warning(adapter, config_dir):
    write_json(
        config_dir / "settings.json",
        {"enabledPlugins": {"ok@m": True, "list@m": ["a", "b"], "text@m": "yes"}},
    )
    snapshot = adapter.read_state(None)
    assert list(by_id(snapshot)) == ["plugin:ok@m"]
    assert len(snapshot.warnings) == 2
    assert all("ignored" in warning for warning in snapshot.warnings)


def test_skills_from_user_and_project_folders_with_overrides(adapter, config_dir, project):
    make_skill(config_dir / "skills", "alpha")
    make_skill(config_dir / "skills", "beta")
    make_skill(config_dir / "skills", "shared")
    make_skill(project / ".claude" / "skills", "gamma")
    make_skill(project / ".claude" / "skills", "shared")
    (config_dir / "skills" / "not-a-skill").mkdir()
    write_json(config_dir / "settings.json", {"skillOverrides": {"beta": "off", "alpha": "on"}})
    write_json(
        project / ".claude" / "settings.json",
        {"skillOverrides": {"gamma": "name-only", "alpha": "off"}},
    )
    snapshot = adapter.read_state(project)
    skills = {k: i for k, i in by_id(snapshot).items() if i.kind == "skill"}
    assert set(skills) == {"skill:alpha", "skill:beta", "skill:gamma", "skill:shared"}
    assert (skills["skill:alpha"].enabled, skills["skill:alpha"].scope) == (False, "project")
    assert skills["skill:beta"].enabled is False and skills["skill:beta"].scope == "user"
    assert skills["skill:gamma"].enabled is True
    assert "name-only" in skills["skill:gamma"].reason
    assert skills["skill:gamma"].writable is True
    # No override: on, decided by the folder it lives in; the user copy shadows the project one.
    assert (skills["skill:shared"].enabled, skills["skill:shared"].scope) == (True, "user")
    assert skills["skill:shared"].source == "~/.claude/skills/shared"
    assert any("shared" in warning and "shadow" in warning for warning in snapshot.warnings)


def test_managed_skill_override_is_read_only(adapter, config_dir, managed):
    make_skill(config_dir / "skills", "alpha")
    write_json(managed / "managed-settings.json", {"skillOverrides": {"alpha": "off"}})
    item = by_id(adapter.read_state(None))["skill:alpha"]
    assert (item.enabled, item.scope, item.writable) == (False, "managed", False)
    assert item.reason


def test_unknown_override_value_is_ignored_with_a_warning(adapter, config_dir):
    make_skill(config_dir / "skills", "alpha")
    write_json(config_dir / "settings.json", {"skillOverrides": {"alpha": "sometimes"}})
    snapshot = adapter.read_state(None)
    assert by_id(snapshot)["skill:alpha"].enabled is True
    assert snapshot.warnings


def test_skill_link_that_leaves_the_skills_folder_is_skipped(adapter, config_dir, tmp_path):
    outside = make_skill(tmp_path / "elsewhere", "escaped")
    make_skill(config_dir / "skills", "inside")
    (config_dir / "skills" / "escaped").symlink_to(outside)
    snapshot = adapter.read_state(None)
    assert list(by_id(snapshot)) == ["skill:inside"]
    assert any("escaped" in warning for warning in snapshot.warnings)


def test_plugin_skills_are_locked_to_their_plugin(adapter, config_dir):
    install = config_dir / "plugins" / "cache" / "mk" / "tool" / "1.0.0"
    make_skill(install / "skills", "pskill")
    write_json(
        config_dir / "plugins" / "installed_plugins.json",
        {"version": 2, "plugins": {"tool@mk": [{"scope": "user", "installPath": str(install)}]}},
    )
    write_json(config_dir / "settings.json", {"enabledPlugins": {"tool@mk": True}})
    items = by_id(adapter.read_state(None))
    skill = items["skill:tool:pskill"]
    assert (skill.enabled, skill.writable, skill.scope) == (True, False, "user")
    assert skill.reason == "Part of plugin tool@mk"
    write_json(config_dir / "settings.json", {"enabledPlugins": {"tool@mk": False}})
    assert by_id(adapter.read_state(None))["skill:tool:pskill"].enabled is False


def test_unknown_installed_plugins_layout_warns(adapter, config_dir):
    write_json(config_dir / "plugins" / "installed_plugins.json", {"surprise": []})
    snapshot = adapter.read_state(None)
    assert snapshot.items == ()
    assert any("installed_plugins.json" in warning for warning in snapshot.warnings)


def write_claude_json(config_dir: Path, project: Path, **entry) -> Path:
    document = {
        "numStartups": 7,
        "userID": SESSION,
        "oauthAccount": {"accessToken": SESSION, "emailAddress": "who@example.test"},
        "unknownFuture": {"nested": [1, 2]},
        "mcpServers": {"user-srv": {"command": "npx", "env": {"TOKEN": SECRET_ENV}}},
        "projects": {
            str(project): {
                "mcpServers": {
                    "local-srv": {
                        "url": "https://x.test",
                        "headers": {"Authorization": SECRET_HEADER},
                    }
                },
                "disabledMcpServers": ["user-srv"],
                "hasTrustDialogAccepted": True,
                **entry,
            },
            "/some/other/project": {"disabledMcpServers": ["local-srv"]},
        },
    }
    return write_json(config_dir / ".claude.json", document)


def test_mcp_servers_from_claude_json_and_mcp_json(adapter, config_dir, project):
    write_claude_json(config_dir, project)
    write_json(
        project / ".mcp.json",
        {"mcpServers": {"proj-srv": {"command": "x", "env": {"K": SECRET_ENV}}}},
    )
    snapshot = adapter.read_state(project)
    servers = {k: i for k, i in by_id(snapshot).items() if i.kind == "mcp"}
    assert {k: (i.enabled, i.scope, i.writable) for k, i in servers.items()} == {
        "mcp:user-srv": (False, "user", True),
        "mcp:local-srv": (True, "local", True),
        "mcp:proj-srv": (True, "project", True),
    }
    assert servers["mcp:proj-srv"].source == ".mcp.json"
    assert servers["mcp:user-srv"].source == "~/.claude/.claude.json"
    for secret in (SECRET_ENV, SECRET_HEADER, SESSION):
        assert secret not in repr(snapshot)


def test_local_mcp_server_wins_over_project_and_user(adapter, config_dir, project):
    write_claude_json(config_dir, project, mcpServers={"user-srv": {"command": "mine"}})
    write_json(project / ".mcp.json", {"mcpServers": {"user-srv": {"command": "theirs"}}})
    item = by_id(adapter.read_state(project))["mcp:user-srv"]
    assert item.scope == "local"


def test_mcp_rows_without_a_project_are_read_only(adapter, config_dir, project):
    write_claude_json(config_dir, project)
    snapshot = adapter.read_state(None)
    servers = [item for item in snapshot.items if item.kind == "mcp"]
    assert [item.id for item in servers] == ["mcp:user-srv"]
    assert servers[0].writable is False
    assert servers[0].reason == "Choose a project"
    assert servers[0].enabled is True


def test_invalid_json_in_one_file_warns_and_the_rest_is_read(adapter, config_dir, project):
    write_json(config_dir / "settings.json", {"enabledPlugins": {"a@m": True}})
    (project / ".claude").mkdir()
    (project / ".claude" / "settings.json").write_text('{"enabledPlugins": {"b@m": tru')
    (project / ".mcp.json").write_text("[1, 2]")
    snapshot = adapter.read_state(project)
    assert list(by_id(snapshot)) == ["plugin:a@m"]
    assert len(snapshot.warnings) == 2
    assert any(".claude/settings.json" in w and "not valid JSON" in w for w in snapshot.warnings)
    assert "tru" not in " ".join(snapshot.warnings).replace("true", "")


def test_nothing_readable_raises_the_schema_error(adapter, config_dir):
    (config_dir / "settings.json").write_text("{nope")
    (config_dir / ".claude.json").write_text("nope")
    with pytest.raises(ProviderStateSchemaError):
        adapter.read_state(None)


def test_one_good_source_is_enough(adapter, config_dir):
    (config_dir / "settings.json").write_text("{nope")
    write_json(config_dir / ".claude.json", {"mcpServers": {"s": {}}})
    assert list(by_id(adapter.read_state(None))) == ["mcp:s"]


def test_no_session_value_reaches_logs_warnings_or_the_snapshot(
    adapter, config_dir, project, caplog
):
    caplog.set_level(logging.DEBUG)
    write_claude_json(config_dir, project)
    snapshot = adapter.read_state(project)
    adapter.is_project_trusted(project)
    adapter.watch_paths(project)
    write_json(config_dir / "settings.json", {"enabledPlugins": {"a@m": True}})
    (config_dir / ".claude.json").write_text(f'{{"userID": "{SESSION}" broken')
    broken = adapter.read_state(project)
    for text in (caplog.text, repr(snapshot), repr(broken), " ".join(broken.warnings)):
        assert SESSION not in text
        assert SECRET_ENV not in text
    assert any(".claude.json" in warning for warning in broken.warnings)


def test_without_claude_config_dir_the_home_files_are_used(adapter, home, project, monkeypatch):
    monkeypatch.delenv("CLAUDE_CONFIG_DIR")
    write_json(home / ".claude" / "settings.json", {"enabledPlugins": {"a@m": True}})
    write_json(home / ".claude.json", {"mcpServers": {"s": {}}})
    write_json(home / ".claude" / ".claude.json", {"mcpServers": {"wrong": {}}})
    items = by_id(adapter.read_state(project))
    assert set(items) == {"plugin:a@m", "mcp:s"}
    assert items["mcp:s"].source == "~/.claude.json"
    assert adapter.watch_paths(None)[:2] == (
        home / ".claude" / "settings.json",
        home / ".claude.json",
    )


def test_a_home_reached_through_a_symlink_reads_the_same(adapter, tmp_path, monkeypatch, project):
    real = tmp_path / "dotfiles-home"
    (real / ".claude").mkdir(parents=True)
    link = tmp_path / "link-home"
    link.symlink_to(real)
    monkeypatch.setenv("HOME", str(link))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(link / ".claude"))
    stowed = write_json(tmp_path / "stow" / "settings.json", {"enabledPlugins": {"a@m": True}})
    (real / ".claude" / "settings.json").symlink_to(stowed)
    make_skill(real / ".claude" / "skills", "alpha")
    write_json(real / ".claude" / ".claude.json", {"mcpServers": {"s": {}}})
    assert set(by_id(adapter.read_state(project))) == {"plugin:a@m", "skill:alpha", "mcp:s"}


def test_watch_paths(adapter, config_dir, project):
    assert adapter.watch_paths(None) == (
        config_dir / "settings.json",
        config_dir / ".claude.json",
        config_dir / "plugins" / "installed_plugins.json",
        config_dir / "skills",
    )
    assert adapter.watch_paths(project)[4:] == (
        project / ".claude" / "settings.json",
        project / ".claude" / "settings.local.json",
        project / ".mcp.json",
        project / ".claude" / "skills",
    )
    assert not any(
        path.name in {".credentials.json", "auth.json"} for path in adapter.watch_paths(project)
    )


def test_fingerprint_ignores_session_fields_but_not_mcp_switches(adapter, config_dir, project):
    path = write_claude_json(config_dir, project)
    first = adapter.read_state(project).fingerprint
    assert adapter.read_state(project).fingerprint == first
    document = json.loads(path.read_text())
    document["numStartups"] += 1
    document["oauthAccount"]["accessToken"] = "rotated"
    document["projects"]["/some/other/project"]["disabledMcpServers"] = []
    path.write_text(json.dumps(document))
    assert adapter.read_state(project).fingerprint == first
    document["projects"][str(project)]["disabledMcpServers"] = []
    path.write_text(json.dumps(document))
    assert adapter.read_state(project).fingerprint != first


def test_fingerprint_changes_with_settings_mcp_json_and_skills(adapter, config_dir, project):
    seen = {adapter.read_state(project).fingerprint}
    write_json(config_dir / "settings.json", {"enabledPlugins": {"a@m": True}})
    seen.add(adapter.read_state(project).fingerprint)
    write_json(project / ".mcp.json", {"mcpServers": {"s": {}}})
    seen.add(adapter.read_state(project).fingerprint)
    make_skill(config_dir / "skills", "alpha")
    seen.add(adapter.read_state(project).fingerprint)
    write_json(config_dir / "settings.json", {"enabledPlugins": {"a@m": False}})
    seen.add(adapter.read_state(project).fingerprint)
    assert len(seen) == 5


@pytest.mark.parametrize(
    ("version", "warns"),
    [("2.1.292", False), ("2.1.400", False), ("2.1.291", True), ("2.2.0", True), ("3.0.1", True)],
)
def test_version_outside_the_tested_range_warns(adapter, config_dir, version, warns):
    (config_dir / "fake-claude-version").write_text(version)
    snapshot = adapter.read_state(None)
    assert snapshot.cli_version == version
    assert bool(snapshot.warnings) is warns
    assert not warns or version in snapshot.warnings[0]


def test_missing_cli_gives_an_empty_version_and_a_warning(adapter, monkeypatch):
    monkeypatch.setenv("PATH", "/nonexistent")
    snapshot = adapter.read_state(None)
    assert snapshot.cli_version == ""
    assert any("version" in warning for warning in snapshot.warnings)


def test_trust_is_read_never_written(adapter, config_dir, project, tmp_path):
    assert adapter.is_project_trusted(project) is False
    path = write_claude_json(config_dir, project)
    before = path.read_bytes()
    assert adapter.is_project_trusted(project) is True
    assert adapter.is_project_trusted(tmp_path / "unknown") is False
    assert path.read_bytes() == before
    path.write_text("not json")
    assert adapter.is_project_trusted(project) is False


def test_methods_that_belong_to_later_issues_say_so(adapter, project):
    calls = [
        lambda: adapter.set_enabled("plugin:a@m", "user", True, "x"),
        lambda: adapter.trust_project(project),
        lambda: adapter.approved_project_servers(project),
        lambda: adapter.run_environment(project, True, []),
        lambda: adapter.login_command(False),
        lambda: adapter.login_status(),
        lambda: adapter.set_api_key(None),
    ]
    for call in calls:
        with pytest.raises(ProviderStateUnsupportedError):
            call()
    assert adapter.credential_isolation() is None


def run_fake(*arguments, cwd, check=True):
    return subprocess.run(
        ["claude", *arguments], cwd=cwd, capture_output=True, text=True, check=check
    )


def test_fake_cli_enable_then_read_shows_enabled(adapter, config_dir, project):
    done = run_fake("plugin", "enable", "x@y", "-s", "project", "--json", cwd=project)
    assert json.loads(done.stdout) == {
        "command": "enable",
        "outcome": "ok",
        "plugin": "x@y",
        "pluginId": "x@y",
        "scope": "project",
        "message": "Successfully enabled plugin: x (scope: project)",
    }
    item = by_id(adapter.read_state(project))["plugin:x@y"]
    assert (item.enabled, item.scope) == (True, "project")
    run_fake("plugin", "disable", "x@y", "-s", "local", "--json", cwd=project)
    assert (project / ".claude" / "settings.local.json").read_text().startswith('{\n  "enabled')
    item = by_id(adapter.read_state(project))["plugin:x@y"]
    assert (item.enabled, item.scope) == (False, "local")
    run_fake("plugin", "enable", "u@y", cwd=project)  # default scope: user
    assert json.loads((config_dir / "settings.json").read_text()) == {
        "enabledPlugins": {"u@y": True}
    }


def test_fake_cli_failures(config_dir, project):
    bad = run_fake("plugin", "enable", "x@y", "-s", "bogus", "--json", cwd=project, check=False)
    assert bad.returncode == 1
    assert 'Invalid scope "bogus"' in bad.stderr and bad.stdout == ""
    (config_dir / "fake-claude-fail").write_text("")
    failed = run_fake("plugin", "enable", "x@y", "--json", cwd=project, check=False)
    outcome = json.loads(failed.stdout)
    assert failed.returncode == 1
    assert (outcome["outcome"], outcome["failureCode"]) == ("failed", "settings_write_failed")
    (config_dir / "fake-claude-fail").unlink()
    (project / ".claude").mkdir()
    (project / ".claude" / "settings.json").write_text("{broken")
    corrupt = run_fake(
        "plugin", "disable", "x@y", "-s", "project", "--json", cwd=project, check=False
    )
    assert json.loads(corrupt.stdout)["failureCode"] == "settings_write_failed"
    assert (project / ".claude" / "settings.json").read_text() == "{broken"
