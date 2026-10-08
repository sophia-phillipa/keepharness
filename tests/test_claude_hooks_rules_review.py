"""Claude hooks and instruction discovery against fake native files."""

import json

import pytest

from adapters.claude.state import ClaudeStateAdapter


def put(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data) if isinstance(data, dict) else data)
    return path


@pytest.fixture
def setup(tmp_path, isolated_provider_homes, monkeypatch):
    home = isolated_provider_homes
    project = tmp_path / "project"
    project.mkdir()
    adapter = ClaudeStateAdapter(tmp_path / "state", tmp_path / "managed")
    monkeypatch.setattr(adapter, "_version", lambda _: "2.1.294")
    monkeypatch.setattr(adapter, "is_project_trusted", lambda _: False)
    return adapter, home, project


def test_untrusted_claude_project_and_local_instructions_are_disabled(setup, monkeypatch):
    adapter, home, project = setup
    put(project / "CLAUDE.md", "# Project")
    put(project / "CLAUDE.local.md", "# Local")
    put(project / ".claude/rules/policy.md", "# Rules")
    put(home / ".claude/CLAUDE.md", "# User")
    before = adapter.read_state(project)
    rows = [item for item in before.items if item.kind == "instructions"]
    assert all(
        not item.enabled and item.details["status"] == "pending project trust"
        for item in rows
        if item.scope in ("project", "local")
    )
    assert next(item for item in rows if item.scope == "user").enabled
    monkeypatch.setattr(adapter, "is_project_trusted", lambda _: True)
    assert all(
        item.enabled for item in adapter.read_state(project).items if item.kind == "instructions"
    )


def install_plugin(adapter, home, *, scope="user", enabled=True):
    plugin = home / ".claude/plugins/cache/fixture"
    put(
        home / ".claude/plugins/installed_plugins.json",
        {"plugins": {"fixture@market": [{"installPath": str(plugin), "scope": scope}]}},
    )
    put(home / ".claude/settings.json", {"enabledPlugins": {"fixture@market": enabled}})
    return plugin


def test_claude_plugin_hooks_merge_default_manifest_files_and_inline_maps(setup, monkeypatch):
    adapter, home, project = setup
    monkeypatch.setattr(adapter, "is_project_trusted", lambda _: True)
    plugin = install_plugin(adapter, home)

    def hook(command):
        return {"Stop": [{"hooks": [{"type": "command", "command": command}]}]}

    default = put(
        plugin / "hooks/hooks.json",
        {"description": "Default policy", "hooks": hook("default --token private-default")},
    )
    custom = put(plugin / "extra.json", {"hooks": hook("custom")})
    put(
        plugin / ".claude-plugin/plugin.json",
        {"name": "fixture", "hooks": ["./extra.json", hook("inline")]},
    )
    first = adapter.read_state(project)
    rows = [item for item in first.items if item.kind == "hook"]
    assert len(rows) == 3 and all(item.enabled for item in rows)
    assert all(item.details["pluginId"] == "fixture@market" for item in rows)
    assert {item.details["command"] for item in rows} >= {"custom", "inline"}
    assert "private-default" not in json.dumps([item.details for item in rows])
    assert default in adapter.watch_paths(project) and custom in adapter.watch_paths(project)
    put(custom, {"hooks": hook("changed")})
    second = adapter.read_state(project)
    assert first.fingerprint != second.fingerprint


def test_claude_project_plugin_hooks_wait_for_trust_and_disabled_plugin_stays_off(
    setup, monkeypatch
):
    adapter, home, project = setup
    plugin = install_plugin(adapter, home, scope="project")
    put(
        plugin / "hooks/hooks.json",
        {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "true"}]}]}},
    )
    hook = next(item for item in adapter.read_state(project).items if item.kind == "hook")
    assert not hook.enabled and hook.details["status"] == "pending project trust"
    monkeypatch.setattr(adapter, "is_project_trusted", lambda _: True)
    put(home / ".claude/settings.json", {"enabledPlugins": {"fixture@market": False}})
    hook = next(item for item in adapter.read_state(project).items if item.kind == "hook")
    assert not hook.enabled and hook.details["status"] == "plugin disabled"


def test_claude_managed_forced_plugin_hooks_ignore_user_disable_and_managed_only(setup):
    adapter, home, project = setup
    plugin = install_plugin(adapter, home)
    put(
        plugin / "hooks/hooks.json",
        {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "true"}]}]}},
    )
    put(
        home / ".claude/settings.json",
        {"disableAllHooks": True, "enabledPlugins": {"fixture@market": False}},
    )
    put(
        adapter.managed_dir / "managed-settings.json",
        {"allowManagedHooksOnly": True, "enabledPlugins": {"fixture@market": True}},
    )
    hook = next(item for item in adapter.read_state(project).items if item.kind == "hook")
    assert hook.enabled and hook.scope == "managed"


def test_claude_agents_fallback_ignores_user_memory_but_respects_ancestor_claude_files(
    setup, monkeypatch
):
    adapter, home, project = setup
    monkeypatch.setattr(adapter, "is_project_trusted", lambda _: True)
    put(home / ".claude/CLAUDE.md", "# User memory")
    agents = put(project / "AGENTS.md", "# Agent fallback\n@extra.md")
    put(project / ".claude/AGENTS.md", "# Dot folder agent fallback")
    put(project / "extra.md", "# Imported fallback")
    rows = [item for item in adapter.read_state(project).items if item.kind == "instructions"]
    assert {"User memory", "Agent fallback", "Dot folder agent fallback", "Imported fallback"} <= {
        item.name for item in rows
    }
    assert agents in adapter.watch_paths(project)
    before = adapter.read_state(project)
    agents.write_text("# Agent fallback changed\n@extra.md")
    assert adapter.read_state(project).fingerprint != before.fingerprint
    put(project.parent / "CLAUDE.local.md", "# Ancestor local")
    assert not any(item.name == "Agent fallback" for item in adapter.read_state(project).items)


@pytest.mark.parametrize(
    "mode, expected",
    [
        ("claude-md-and-agents-md", {"Project", "Agents"}),
        ("claude-md", {"Project"}),
        ("claude-md-or-agents-md", {"Project"}),
    ],
)
def test_claude_agents_instruction_mode_uses_user_settings_not_project_overrides(
    setup, monkeypatch, mode, expected
):
    adapter, home, project = setup
    monkeypatch.setattr(adapter, "is_project_trusted", lambda _: True)
    put(project / "CLAUDE.md", "# Project")
    put(project / "AGENTS.md", "# Agents")
    put(
        home / ".claude/settings.json",
        {"pluginConfigs": {"cc-plugin-agents-md@builtin": {"options": {"instructionFiles": mode}}}},
    )
    put(
        project / ".claude/settings.json",
        {
            "pluginConfigs": {
                "cc-plugin-agents-md@builtin": {
                    "options": {"instructionFiles": "claude-md-and-agents-md"}
                }
            }
        },
    )
    names = {item.name for item in adapter.read_state(project).items if item.kind == "instructions"}
    assert names == expected


@pytest.mark.parametrize("plugin_id", ["cc-plugin-agents-md@builtin", "agents-md@builtin"])
def test_claude_disabled_agents_builtin_has_no_fallback(setup, monkeypatch, plugin_id):
    adapter, home, project = setup
    monkeypatch.setattr(adapter, "is_project_trusted", lambda _: True)
    put(project / "AGENTS.md", "# Agents")
    put(home / ".claude/settings.json", {"enabledPlugins": {plugin_id: False}})
    assert not any(item.name == "Agents" for item in adapter.read_state(project).items)


def test_claude_managed_only_instructions_keep_other_sources_disabled(setup, monkeypatch):
    adapter, home, project = setup
    monkeypatch.setattr(adapter, "is_project_trusted", lambda _: True)
    put(home / ".claude/CLAUDE.md", "# User")
    put(project / "CLAUDE.md", "# Project\n@extra.md")
    put(project / "extra.md", "# Imported project")
    put(project / ".claude/rules/rule.md", "# Project rule")
    put(adapter.managed_dir / "CLAUDE.md", "# Managed")
    put(adapter.managed_dir / "rules/rule.md", "# Managed rule")
    put(
        home / ".claude/settings.json",
        {
            "pluginConfigs": {
                "cc-plugin-agents-md@builtin": {"options": {"instructionFiles": "managed-only"}}
            }
        },
    )
    rows = [item for item in adapter.read_state(project).items if item.kind == "instructions"]
    assert next(item for item in rows if item.name == "Managed").enabled
    assert all(
        not item.enabled and item.details["status"] == "disabled by instructionFiles=managed-only"
        for item in rows
        if item.name != "Managed"
    )


def test_claude_untrusted_project_plugin_override_cannot_disable_user_hook(setup):
    adapter, home, project = setup
    plugin = install_plugin(adapter, home)
    put(
        plugin / "hooks/hooks.json",
        {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "true"}]}]}},
    )
    put(project / ".claude/settings.json", {"enabledPlugins": {"fixture@market": False}})
    hook = next(item for item in adapter.read_state(project).items if item.kind == "hook")
    assert hook.enabled and hook.scope == "user"


def test_claude_project_plugin_index_filters_other_project_paths(setup, tmp_path):
    adapter, home, project = setup
    other = tmp_path / "other-project"
    other.mkdir()
    current_plugin = home / ".claude/plugins/cache/current-project"
    other_plugin = home / ".claude/plugins/cache/other-project"
    for root, command in ((current_plugin, "current-hook"), (other_plugin, "other-hook")):
        put(
            root / "hooks/hooks.json",
            {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": command}]}]}},
        )
    put(home / ".claude/settings.json", {"enabledPlugins": {"fixture@market": True}})
    # This index is the existing adapter contract, not a verified native persistence schema.
    put(
        home / ".claude/plugins/installed_plugins.json",
        {
            "plugins": {
                "fixture@market": [
                    {
                        "scope": "project",
                        "projectPath": str(project),
                        "installPath": str(current_plugin),
                    },
                    {
                        "scope": "project",
                        "projectPath": str(other),
                        "installPath": str(other_plugin),
                    },
                ]
            }
        },
    )
    hooks = [item for item in adapter.read_state(project).items if item.kind == "hook"]
    assert len(hooks) == 1 and hooks[0].details["command"] == "current-hook"
    assert hooks[0].details["status"] == "pending project trust"
    assert not hooks[0].enabled
