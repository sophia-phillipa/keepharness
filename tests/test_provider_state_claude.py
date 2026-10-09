"""Claude Code state reader: layering, skills, MCP, safety and the fake `claude` CLI."""

import hashlib
import json
import logging
import os
import stat
import subprocess
from pathlib import Path

import pytest
from jsonschema import Draft7Validator

from adapters.claude import state as claude_state
from adapters.claude.state import ClaudeStateAdapter
from adapters.shared.provider_state import (
    MISSING_FILE,
    ProviderCommandError,
    ProviderStateAdapter,
    ProviderStateConflictError,
    ProviderStateSchemaError,
    ProviderStateUnsupportedError,
    ProviderStateValidationError,
    ProviderStateVersionError,
    write_json_atomic,
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
    # a checkout or sdist without the extensionless fake CLI must fail loudly, not run the real one
    assert (FAKE_CLAUDE_DIR / "claude").is_file()
    monkeypatch.setenv(
        "PATH",
        f"{FAKE_CLAUDE_DIR}{os.pathsep}{FAKE_CLAUDE_DIR.parent / 'fake-codex'}{os.pathsep}{os.environ['PATH']}",
    )


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
    assert skills["skill:shared"].source == "~/.claude/skills/shared/SKILL.md"
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
    assert skill.plugin == "tool@mk"
    make_skill(config_dir / "skills", "own")
    assert by_id(adapter.read_state(None))["skill:own"].plugin == ""
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


def test_watch_paths(adapter, config_dir, project, managed):
    from adapters.shared.orchestration_state import claude_instructions

    user_instructions = claude_instructions(
        Path.home(), config_dir, None, managed, read_content=False
    ).paths
    project_instructions = claude_instructions(
        Path.home(), config_dir, project, managed, read_content=False
    ).paths
    managed_paths = (managed / "managed-settings.json", managed / "managed-settings.d")  # R40-8
    assert adapter.watch_paths(None) == (
        config_dir / "settings.json",
        config_dir / ".claude.json",
        config_dir / "plugins" / "installed_plugins.json",
        config_dir / "skills",
        *managed_paths,
        *user_instructions,
    )
    assert adapter.watch_paths(project)[4:] == (
        project / ".claude" / "settings.json",
        project / ".claude" / "settings.local.json",
        project / ".mcp.json",
        project / ".claude" / "skills",
        *managed_paths,
        *project_instructions,
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
    assert adapter.run_environment(project, True, []).environment == {}
    calls = [
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


# --- set_enabled -------------------------------------------------------------------------------


def fingerprint_of(adapter, project):
    return adapter.read_state(project).fingerprint


def toggle(adapter, project, item_id, scope, enabled, **options):
    fingerprint = options.pop("fingerprint", None) or fingerprint_of(adapter, project)
    return adapter.set_enabled(
        item_id, scope, enabled, fingerprint, project_root=project, **options
    )


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize(
    ("scope", "relative"),
    [
        ("user", None),
        ("project", ".claude/settings.json"),
        ("local", ".claude/settings.local.json"),
    ],
)
def test_plugin_switch_goes_through_the_cli_in_each_scope(
    adapter, config_dir, project, scope, relative
):
    path = config_dir / "settings.json" if relative is None else project / relative
    snapshot = toggle(adapter, project, "plugin:x@y", scope, True)
    item = by_id(snapshot)["plugin:x@y"]
    assert (item.enabled, item.scope) == (True, scope)
    assert json.loads(path.read_text()) == {"enabledPlugins": {"x@y": True}}
    snapshot = toggle(
        adapter, project, "plugin:x@y", scope, False, fingerprint=snapshot.fingerprint
    )
    assert by_id(snapshot)["plugin:x@y"].enabled is False


def test_a_failing_plugin_command_raises_a_redacted_command_error(
    adapter, config_dir, project, monkeypatch
):
    monkeypatch.setenv("KEEPHARNESS_TEST_SECRET", SECRET_ENV)
    write_json(config_dir / "settings.json", {})
    (project / ".claude").mkdir()
    (project / ".claude" / "settings.json").write_text("{broken")
    with pytest.raises(ProviderCommandError) as caught:
        toggle(adapter, project, "plugin:x@y", "project", True)
    assert caught.value.exit_code == 1
    text = str(caught.value) + repr(caught.value.args)
    assert "Invalid JSON syntax in settings file" in text
    for leaked in (str(project), str(config_dir), SECRET_ENV, "/settings.json"):
        assert leaked not in text
    assert (project / ".claude" / "settings.json").read_text() == "{broken"


def test_a_cli_that_reports_failure_in_json_is_a_command_error(adapter, config_dir, project):
    (config_dir / "fake-claude-fail").write_text("")
    with pytest.raises(ProviderCommandError) as caught:
        toggle(adapter, project, "plugin:x@y", "user", True)
    assert caught.value.exit_code == 1 and str(config_dir) not in str(caught.value)


def test_a_cli_without_json_output_is_reported_as_redacted_text(adapter, project, monkeypatch):
    def fake_run(command, **kwargs):
        text = f"Invalid scope at {project}/x"
        return subprocess.CompletedProcess(command, 1, stdout="", stderr=text)

    monkeypatch.setattr(claude_state.subprocess, "run", fake_run)
    with pytest.raises(ProviderCommandError) as caught:
        adapter._set_plugin("x@y", "user", True, project)
    assert str(project) not in str(caught.value) and "<path>" in str(caught.value)


def test_a_plugin_command_that_cannot_run_or_times_out_is_a_command_error(
    adapter, project, monkeypatch
):
    for error in (FileNotFoundError("claude"), subprocess.TimeoutExpired(["claude"], 10)):

        def fake_run(command, **kwargs):
            raise error

        monkeypatch.setattr(claude_state.subprocess, "run", fake_run)
        with pytest.raises(ProviderCommandError):
            adapter._set_plugin("x@y", "user", True, project)


def test_a_stronger_layer_that_still_decides_is_explained_in_a_warning(
    adapter, config_dir, project
):  # R40-7
    write_json(config_dir / "settings.json", {"enabledPlugins": {"a@m": True}})
    write_json(project / ".claude" / "settings.json", {"enabledPlugins": {"a@m": True}})
    snapshot = toggle(adapter, project, "plugin:a@m", "user", False)
    assert json.loads((config_dir / "settings.json").read_text()) == {
        "enabledPlugins": {"a@m": False}
    }
    item = by_id(snapshot)["plugin:a@m"]
    assert (item.enabled, item.scope) == (True, "project")
    assert "a@m stays enabled: the project setting in .claude/settings.json decides" in (
        snapshot.warnings
    )


def test_no_override_warning_when_the_written_value_wins(adapter, config_dir, project):  # R40-7
    write_json(config_dir / "settings.json", {"enabledPlugins": {"a@m": True}})
    assert toggle(adapter, project, "plugin:a@m", "user", False).warnings == ()


def test_plugin_switch_is_allowed_outside_the_tested_versions(adapter, config_dir, project):
    (config_dir / "fake-claude-version").write_text("2.2.0")
    snapshot = toggle(adapter, project, "plugin:x@y", "user", True)
    assert by_id(snapshot)["plugin:x@y"].enabled is True


@pytest.mark.parametrize(
    ("scope", "relative"),
    [
        ("user", None),
        ("project", ".claude/settings.json"),
        ("local", ".claude/settings.local.json"),
    ],
)
def test_skill_off_and_on_keep_unknown_keys_and_the_indent(
    adapter, config_dir, project, scope, relative
):
    make_skill(config_dir / "skills", "deploy")
    path = config_dir / "settings.json" if relative is None else project / relative
    original = {"futureKey": {"x": [1, None]}, "skillOverrides": {"other": "name-only"}, "z": 1}
    write_json(path, original, indent=4)
    snapshot = toggle(adapter, project, "skill:deploy", scope, False)
    expected = {**original, "skillOverrides": {"other": "name-only", "deploy": "off"}}
    assert path.read_text() == json.dumps(expected, indent=4) + "\n"
    assert by_id(snapshot)["skill:deploy"].enabled is False
    snapshot = toggle(
        adapter, project, "skill:deploy", scope, True, fingerprint=snapshot.fingerprint
    )
    expected["skillOverrides"]["deploy"] = "on"
    assert path.read_text() == json.dumps(expected, indent=4) + "\n"
    assert by_id(snapshot)["skill:deploy"].enabled is True


def test_enabling_a_skill_writes_on_so_a_weaker_layer_cannot_bring_it_back_off(
    adapter, config_dir, project
):
    make_skill(config_dir / "skills", "deploy")
    write_json(config_dir / "settings.json", {"skillOverrides": {"deploy": "off"}})
    (project / ".claude").mkdir()
    snapshot = toggle(adapter, project, "skill:deploy", "local", True)
    assert by_id(snapshot)["skill:deploy"].enabled is True


def test_enabling_a_skill_keeps_a_restricted_override(adapter, config_dir, project):  # R40-3
    make_skill(config_dir / "skills", "deploy")
    path = write_json(config_dir / "settings.json", {"skillOverrides": {"deploy": "name-only"}})
    before = path.read_bytes()
    snapshot = toggle(adapter, project, "skill:deploy", "user", True)
    assert path.read_bytes() == before
    assert by_id(snapshot)["skill:deploy"].enabled is True


def test_a_missing_settings_file_is_created_exclusively_with_mode_0600(
    adapter, config_dir, project
):
    make_skill(config_dir / "skills", "deploy")
    (project / ".claude").mkdir()
    toggle(adapter, project, "skill:deploy", "local", False)
    created = project / ".claude" / "settings.local.json"
    assert json.loads(created.read_text()) == {"skillOverrides": {"deploy": "off"}}
    assert stat.S_IMODE(created.stat().st_mode) == 0o600
    assert sorted(p.name for p in (project / ".claude").iterdir()) == ["settings.local.json"]


def test_a_settings_file_that_appears_meanwhile_is_a_conflict(
    adapter, config_dir, project, monkeypatch
):
    make_skill(config_dir / "skills", "deploy")
    (project / ".claude").mkdir()
    target = project / ".claude" / "settings.local.json"
    real_link = os.link

    def appear_then_link(source, destination, *args, **kwargs):
        Path(destination).write_text('{"theirs": 1}')
        return real_link(source, destination, *args, **kwargs)

    monkeypatch.setattr(os, "link", appear_then_link)
    with pytest.raises(ProviderStateConflictError):
        toggle(adapter, project, "skill:deploy", "local", False)
    monkeypatch.undo()
    assert target.read_text() == '{"theirs": 1}'
    assert [p.name for p in (project / ".claude").iterdir()] == ["settings.local.json"]


def test_a_missing_file_has_no_digest_that_could_pass_for_create_mode(tmp_path):  # R40-6
    absent = tmp_path / "absent.json"
    assert claude_state._read_json_object(absent) == (None, None, "missing")
    assert MISSING_FILE not in {"missing", "unreadable"}
    with pytest.raises(ProviderStateConflictError):
        write_json_atomic(absent, lambda document: document, "missing")
    assert not absent.exists()


def test_a_missing_project_folder_is_not_created_for_a_skill_switch(adapter, config_dir, project):
    make_skill(config_dir / "skills", "deploy")
    with pytest.raises(ProviderStateUnsupportedError):
        toggle(adapter, project, "skill:deploy", "project", False)
    assert not (project / ".claude").exists()


def test_a_schema_invalid_result_is_refused_and_the_file_keeps_its_bytes(
    adapter, config_dir, project, monkeypatch
):
    make_skill(config_dir / "skills", "bad_name")
    strict = {
        "type": "object",
        "properties": {"skillOverrides": {"propertyNames": {"pattern": "^[a-z]+$"}}},
    }
    monkeypatch.setattr(claude_state, "_settings_validator", lambda: Draft7Validator(strict))
    path = write_json(config_dir / "settings.json", {"a": 1})
    before = path.read_bytes()
    with pytest.raises(ProviderStateValidationError) as caught:
        toggle(adapter, project, "skill:bad_name", "user", False)
    assert path.read_bytes() == before
    assert caught.value.errors == ("/skillOverrides: pattern",)


def test_the_real_schema_accepts_an_override_and_an_old_schema_error_does_not_block(
    adapter, config_dir, project
):
    make_skill(config_dir / "skills", "deploy")
    path = write_json(config_dir / "settings.json", {"skillOverrides": {"old": "bogus-value"}})
    toggle(adapter, project, "skill:deploy", "user", False)
    assert json.loads(path.read_text())["skillOverrides"] == {"old": "bogus-value", "deploy": "off"}
    assert claude_state._settings_errors(b'{"skillOverrides": {"x": "SECRET-VALUE"}}') == [
        "/skillOverrides/x: enum"
    ]


def test_mcp_switch_edits_only_disabled_servers_and_backs_up_outside_claude(
    adapter, config_dir, project, tmp_path, caplog
):
    caplog.set_level(logging.DEBUG)
    path = write_claude_json(config_dir, project)
    original = json.loads(path.read_text())
    snapshot = toggle(adapter, project, "mcp:local-srv", "local", False)
    original["projects"][str(project)]["disabledMcpServers"] = ["user-srv", "local-srv"]
    assert path.read_text() == json.dumps(original, indent=2) + "\n"
    assert by_id(snapshot)["mcp:local-srv"].enabled is False
    backups = sorted((tmp_path / "state" / "backups" / "claude-json").iterdir())
    assert len(backups) == 1 and stat.S_IMODE(backups[0].stat().st_mode) == 0o600
    assert SESSION in backups[0].read_text()
    assert not (config_dir / "backups").exists()
    snapshot = toggle(
        adapter, project, "mcp:user-srv", "user", True, fingerprint=snapshot.fingerprint
    )
    original["projects"][str(project)]["disabledMcpServers"] = ["local-srv"]
    assert path.read_text() == json.dumps(original, indent=2) + "\n"
    assert by_id(snapshot)["mcp:user-srv"].enabled is True
    for secret in (SESSION, SECRET_ENV, SECRET_HEADER):
        assert secret not in caplog.text and secret not in repr(snapshot)


def test_mcp_switch_never_invents_a_project_entry(adapter, config_dir, project, tmp_path):  # R40-2
    path = write_json(config_dir / ".claude.json", {"mcpServers": {"s": {"command": "x"}}})
    before = path.read_bytes()
    snapshot = adapter.read_state(project)
    row = by_id(snapshot)["mcp:s"]
    assert row.writable is False and "Open this project in Claude Code" in row.reason
    assert str(project) not in row.reason
    with pytest.raises(ProviderStateUnsupportedError, match="Open this project"):
        toggle(adapter, project, "mcp:s", "user", False)
    assert path.read_bytes() == before
    assert not (tmp_path / "state").exists()


def test_mcp_switch_writes_into_the_entry_found_by_the_resolved_path(
    adapter, config_dir, project, tmp_path
):  # R40-2
    link = tmp_path / "link"
    link.symlink_to(project)
    path = write_claude_json(config_dir, project)
    keys = set(json.loads(path.read_text())["projects"])
    assert str(link) not in keys
    snapshot = toggle(adapter, link, "mcp:local-srv", "local", False)
    document = json.loads(path.read_text())
    assert set(document["projects"]) == keys
    assert document["projects"][str(project)]["disabledMcpServers"] == ["user-srv", "local-srv"]
    assert by_id(snapshot)["mcp:local-srv"].enabled is False


def test_mcp_entries_for_both_paths_use_the_given_one_and_warn(
    adapter, config_dir, project, tmp_path
):  # R40-2
    link = tmp_path / "link"
    link.symlink_to(project)
    path = write_claude_json(config_dir, project)
    document = json.loads(path.read_text())
    document["projects"][str(link)] = {
        **document["projects"][str(project)],
        "disabledMcpServers": [],
    }
    write_json(path, document)
    snapshot = toggle(adapter, link, "mcp:local-srv", "local", False)
    after = json.loads(path.read_text())["projects"]
    assert after[str(link)]["disabledMcpServers"] == ["local-srv"]
    assert after[str(project)] == document["projects"][str(project)]
    assert any("both the given and the resolved" in warning for warning in snapshot.warnings)
    assert not any(str(tmp_path) in warning for warning in snapshot.warnings)


@pytest.mark.parametrize(
    ("item_id", "enabled", "entry"),
    [
        ("mcp:local-srv", True, {}),  # already enabled
        ("mcp:user-srv", False, {}),  # already disabled
        ("mcp:local-srv", True, {"disabledMcpServers": None}),  # no key: none is added
    ],
)
def test_a_mcp_request_for_the_state_it_is_already_in_writes_nothing(
    adapter, config_dir, project, tmp_path, item_id, enabled, entry
):  # R40-1
    path = write_claude_json(config_dir, project)
    document = json.loads(path.read_text())
    if entry:
        del document["projects"][str(project)]["disabledMcpServers"]
        write_json(path, document)
    before = path.read_bytes()
    snapshot = toggle(adapter, project, item_id, "local", enabled)
    assert path.read_bytes() == before
    assert not (tmp_path / "state" / "backups").exists()
    assert by_id(snapshot)[item_id].enabled is enabled


def test_mcp_switch_needs_a_project_and_an_existing_claude_json(adapter, config_dir, project):
    write_json(project / ".mcp.json", {"mcpServers": {"s": {"command": "x"}}})
    fingerprint = fingerprint_of(adapter, project)
    with pytest.raises(ProviderStateUnsupportedError, match="does not exist"):
        adapter.set_enabled("mcp:s", "project", False, fingerprint, project_root=project)
    assert not (config_dir / ".claude.json").exists()
    write_claude_json(config_dir, project)
    with pytest.raises(ProviderStateUnsupportedError):
        adapter.set_enabled("mcp:user-srv", "user", False, fingerprint_of(adapter, None))


def test_a_symlinked_claude_json_stays_a_symlink(adapter, config_dir, project, tmp_path):
    real = write_claude_json(tmp_path / "dotfiles", project)
    (config_dir / ".claude.json").symlink_to(real)
    toggle(adapter, project, "mcp:local-srv", "local", False)
    assert (config_dir / ".claude.json").is_symlink()
    assert (
        "local-srv" in json.loads(real.read_text())["projects"][str(project)]["disabledMcpServers"]
    )


def test_direct_edits_are_refused_outside_the_tested_versions(adapter, config_dir, project):
    make_skill(config_dir / "skills", "deploy")
    write_claude_json(config_dir, project)
    (config_dir / "fake-claude-version").write_text("2.2.0")
    for item_id, scope in (("skill:deploy", "user"), ("mcp:local-srv", "local")):
        with pytest.raises(ProviderStateVersionError):
            toggle(adapter, project, item_id, scope, False)
    assert not (config_dir / "settings.json").exists()
    assert (
        "local-srv"
        not in json.loads((config_dir / ".claude.json").read_text())["projects"][str(project)][
            "disabledMcpServers"
        ]
    )


def test_a_stale_fingerprint_is_a_conflict_and_writes_nothing(adapter, config_dir, project):
    fingerprint = fingerprint_of(adapter, project)
    write_json(config_dir / "settings.json", {"enabledPlugins": {"a@m": True}})
    with pytest.raises(ProviderStateConflictError):
        toggle(adapter, project, "plugin:x@y", "user", True, fingerprint=fingerprint)
    assert json.loads((config_dir / "settings.json").read_text()) == {
        "enabledPlugins": {"a@m": True}
    }


def change_before(adapter, monkeypatch, method, rewrite):
    """Run ``rewrite()`` after set_enabled has read the state and before ``method`` writes it."""
    real = getattr(adapter, method)

    def late(*args, **kwargs):
        rewrite()
        return real(*args, **kwargs)

    monkeypatch.setattr(adapter, method, late)


def test_a_settings_file_changed_after_the_read_is_a_conflict(
    adapter, config_dir, project, monkeypatch
):  # R40-4
    make_skill(config_dir / "skills", "deploy")
    path = write_json(config_dir / "settings.json", {"a": 1})
    changed = json.dumps({"a": 2}).encode()
    change_before(adapter, monkeypatch, "_set_skill", lambda: path.write_bytes(changed))
    with pytest.raises(ProviderStateConflictError):
        toggle(adapter, project, "skill:deploy", "user", False)
    assert path.read_bytes() == changed


def test_claude_json_changed_after_the_read_is_a_conflict_even_in_a_session_field(
    adapter, config_dir, project, monkeypatch, tmp_path
):  # R40-4
    path = write_claude_json(config_dir, project)

    def bump_session_field():
        document = json.loads(path.read_text())
        document["numStartups"] += 1
        write_json(path, document)

    change_before(adapter, monkeypatch, "_set_mcp", bump_session_field)
    with pytest.raises(ProviderStateConflictError):
        toggle(adapter, project, "mcp:local-srv", "local", False)
    assert (
        "local-srv"
        not in json.loads(path.read_text())["projects"][str(project)]["disabledMcpServers"]
    )
    assert not (tmp_path / "state").exists()


def test_the_version_is_read_before_any_file(adapter, config_dir, project, monkeypatch):  # R40-4
    write_claude_json(config_dir, project)
    seen = []
    real = adapter._version

    def spy(reading):
        seen.append(list(reading.parts))
        return real(reading)

    monkeypatch.setattr(adapter, "_version", spy)
    adapter.read_state(project)
    assert seen == [[]]


def test_a_value_that_did_not_stick_is_a_conflict(adapter, config_dir, project, monkeypatch):
    make_skill(config_dir / "skills", "deploy")
    write_claude_json(config_dir, project)
    monkeypatch.setattr(claude_state, "write_json_atomic", lambda *args, **kwargs: "")
    for item_id, scope in (("skill:deploy", "user"), ("mcp:local-srv", "local")):
        with pytest.raises(ProviderStateConflictError):
            toggle(adapter, project, item_id, scope, False)
    monkeypatch.undo()
    monkeypatch.setattr(
        claude_state.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, stdout="{}", stderr=""),
    )
    with pytest.raises(ProviderStateConflictError):
        adapter._set_plugin("x@y", "user", True, project)


def test_managed_and_plugin_skill_rows_cannot_be_written(adapter, config_dir, project, managed):
    make_skill(config_dir / "skills", "deploy")
    write_json(
        managed / "managed-settings.json",
        {"enabledPlugins": {"m@m": False}, "skillOverrides": {"deploy": "off"}},
    )
    install = config_dir / "plugins" / "cache" / "mk" / "tool" / "1.0.0"
    make_skill(install / "skills", "pskill")
    write_json(
        config_dir / "plugins" / "installed_plugins.json",
        {"version": 2, "plugins": {"tool@mk": [{"scope": "user", "installPath": str(install)}]}},
    )
    write_json(config_dir / "settings.json", {"enabledPlugins": {"tool@mk": True}})
    for item_id in ("plugin:m@m", "skill:deploy", "skill:tool:pskill"):
        with pytest.raises(ProviderStateUnsupportedError):
            toggle(adapter, project, item_id, "user", True)
    for scope in ("managed", "profile"):
        with pytest.raises(ProviderStateUnsupportedError):
            toggle(adapter, project, "plugin:x@y", scope, True)
    assert not (config_dir / "plugins" / "x").exists()


def test_unknown_items_kinds_and_scopes_without_a_project_are_unsupported(
    adapter, config_dir, project
):
    for item_id, scope, root in (
        ("skill:ghost", "user", project),
        ("mcp:ghost", "user", project),
        ("hook:x", "user", project),
        ("plugin:-x", "user", project),
        ("plugin:x@y", "local", None),
    ):
        with pytest.raises(ProviderStateUnsupportedError):
            adapter.set_enabled(
                item_id, scope, True, fingerprint_of(adapter, root), project_root=root
            )


def test_enabling_a_skill_that_is_already_on_creates_nothing(adapter, config_dir, project):  # R40-9
    make_skill(config_dir / "skills", "deploy")
    (project / ".claude").mkdir()
    snapshot = toggle(adapter, project, "skill:deploy", "local", True)
    assert list((project / ".claude").iterdir()) == []
    assert by_id(snapshot)["skill:deploy"].enabled is True


def test_disabling_a_skill_that_is_already_off_writes_nothing(
    adapter, config_dir, project
):  # R40-10
    make_skill(config_dir / "skills", "deploy")
    path = write_json(config_dir / "settings.json", {"skillOverrides": {"deploy": "off"}})
    before = path.stat().st_ino, path.read_bytes()
    toggle(adapter, project, "skill:deploy", "user", False)
    assert (path.stat().st_ino, path.read_bytes()) == before


def test_enabling_in_a_stronger_scope_still_overrides_a_weaker_off(adapter, config_dir, project):
    make_skill(config_dir / "skills", "deploy")
    write_json(config_dir / "settings.json", {"skillOverrides": {"deploy": "off"}})
    (project / ".claude").mkdir()
    snapshot = toggle(adapter, project, "skill:deploy", "local", True)
    local = json.loads((project / ".claude" / "settings.local.json").read_text())
    assert local == {"skillOverrides": {"deploy": "on"}}
    assert by_id(snapshot)["skill:deploy"].enabled is True
