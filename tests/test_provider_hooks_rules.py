"""Hooks/rules facade contracts; all sources and CLI responses are fake."""

import asyncio
import dataclasses
import json
from pathlib import Path

import pytest

from adapters.claude.state import ClaudeStateAdapter
from adapters.codex.state import CodexStateAdapter
from control.provider_state import _items_of, _merge, diff_items


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) if isinstance(value, dict) else value)
    return path


@pytest.fixture
def claude(tmp_path, isolated_provider_homes, monkeypatch):
    adapter = ClaudeStateAdapter(tmp_path / "state", tmp_path / "managed")
    monkeypatch.setattr(adapter, "_version", lambda _: "2.1.292")
    monkeypatch.setattr(adapter, "is_project_trusted", lambda _: False)
    return adapter, isolated_provider_homes


def test_claude_hook_layers_native_fields_trust_and_redaction(claude, tmp_path):
    adapter, home = claude
    project = tmp_path / "project"
    hook = {
        "type": "command",
        "command": 'check --token "hide-me" --mode safe',
        "args": ["--password", "arg-secret"],
        "env": {"API_KEY": "env-secret", "MODE": "ok"},
        "timeout": 12,
        "async": True,
        "statusMessage": "Checking",
    }
    for scope, path in [
        ("user", home / ".claude/settings.json"),
        ("project", project / ".claude/settings.json"),
        ("local", project / ".claude/settings.local.json"),
        ("managed", adapter.managed_dir / "managed-settings.json"),
    ]:
        put(path, {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [hook]}]}})
    snapshot = adapter.read_state(project)
    rows = [x for x in snapshot.items if x.kind == "hook"]
    assert {x.scope for x in rows} == {"user", "project", "local", "managed"}
    assert all(not x.enabled for x in rows if x.scope in ("project", "local"))
    assert all(x.details["event"] == "PreToolUse" and x.details["matcher"] == "Bash" for x in rows)
    assert all(x.details["timeout"] == 12 and not x.writable for x in rows)
    serialized = json.dumps(dataclasses.asdict(snapshot))
    for secret in ("hide-me", "arg-secret", "env-secret"):
        assert secret not in serialized
    assert "pending project trust" in serialized


def test_claude_disable_all_hooks_precedence(claude, tmp_path, monkeypatch):
    adapter, home = claude
    project = tmp_path / "project"
    monkeypatch.setattr(adapter, "is_project_trusted", lambda _: True)
    put(
        home / ".claude/settings.json",
        {
            "disableAllHooks": True,
            "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo safe"}]}]},
        },
    )
    put(project / ".claude/settings.local.json", {"disableAllHooks": False})
    assert next(x for x in adapter.read_state(project).items if x.kind == "hook").enabled
    put(adapter.managed_dir / "managed-settings.json", {"disableAllHooks": True})
    assert not next(x for x in adapter.read_state(project).items if x.kind == "hook").enabled


def test_claude_rules_imports_preview_size_and_outside_change(claude, tmp_path):
    adapter, home = claude
    project = tmp_path / "project"
    put(project / "CLAUDE.md", "# Project\n@docs/more.md\n")
    imported = put(project / "docs/more.md", "# More\nNever deploy.\n")
    put(project / ".claude/rules/python.md", '---\npaths: ["**/*.py"]\n---\n# Python\n')
    put(project / "CLAUDE.local.md", "# Local\n")
    first = adapter.read_state(project)
    rows = [x for x in first.items if x.kind == "instructions"]
    assert len(rows) == 4
    row = next(x for x in rows if x.source.endswith("more.md"))
    assert row.details["size_bytes"] == imported.stat().st_size
    assert row.details["preview"].startswith("# More")
    assert row.details["imported_from"].endswith("CLAUDE.md")
    imported.write_text("# More\nNever release.\n")
    second = adapter.read_state(project)
    assert first.fingerprint != second.fingerprint
    changes = diff_items(_items_of(first), _items_of(second), {})
    assert len(changes) == 1 and changes[0]["change"] == "changed"
    assert _merge([], changes, "claude", "p", "now")
    assert "Never release" not in json.dumps(changes)


def test_codex_native_hooks_trust_and_rules_chain(tmp_path, isolated_provider_homes, monkeypatch):
    import adapters.codex.state as module

    home = isolated_provider_homes
    project = tmp_path / "project"
    put(home / ".codex/AGENTS.md", "# Global\n")
    put(project / "AGENTS.md", "# Base\n")
    put(project / "AGENTS.override.md", "# Override\n")
    put(home / ".codex/rules/default.rules", 'prefix_rule(pattern=["git"], decision="allow")\n')
    source = put(project / ".codex/hooks.json", {"hooks": {}})

    async def ask(*args, **kwargs):
        return {
            "config": {"layers": []},
            "hooks": {
                "data": [
                    {
                        "hooks": [
                            {
                                "key": "one",
                                "eventName": "preToolUse",
                                "matcher": "Bash",
                                "handlerType": "command",
                                "command": "check --api-key hidden",
                                "source": "project",
                                "sourcePath": str(source),
                                "enabled": True,
                                "trustStatus": "modified",
                                "currentHash": "abc",
                                "displayOrder": 0,
                                "timeoutSec": 600,
                                "isManaged": False,
                                "async": False,
                            }
                        ]
                    }
                ]
            },
        }, {}

    monkeypatch.setattr(module, "_ask", ask)
    monkeypatch.setattr(module, "_cli_version", lambda *_: "0.157.1")
    adapter = CodexStateAdapter()
    monkeypatch.setattr(adapter, "_binary", lambda: "fake")
    snapshot = adapter.read_state(project)
    hook = next(x for x in snapshot.items if x.kind == "hook")
    assert not hook.enabled and hook.details["status"] == "pending review"
    assert hook.details["trustStatus"] == "modified"
    assert hook.details["timeoutSec"] == 600
    assert "hidden" not in json.dumps(dataclasses.asdict(snapshot))
    rules = [x for x in snapshot.items if x.kind == "instructions"]
    assert {Path(x.source).name for x in rules} == {
        "AGENTS.md",
        "AGENTS.override.md",
        "default.rules",
    }
    assert any(x.details.get("status") == "shadowed by AGENTS.override.md" for x in rules)


def test_deepseek_read_only_facade_uses_isolated_codex_home(
    tmp_path, isolated_provider_homes, monkeypatch
):
    import adapters.codex.state as module
    from adapters.deepseek.state import DeepSeekStateAdapter
    from control.provider_state import ProviderStateService

    state = tmp_path / "state"
    put(state / "providers/deepseek/AGENTS.md", "# DeepSeek instructions\n")
    put(isolated_provider_homes / ".codex/AGENTS.md", "# Must not leak into DeepSeek\n")

    async def ask(*args, **kwargs):
        assert kwargs["environment"]["CODEX_HOME"] == str(state / "providers/deepseek")
        return {"config": {"layers": []}, "hooks": {"data": []}}, {}

    monkeypatch.setattr(module, "_ask", ask)
    monkeypatch.setattr(module, "_cli_version", lambda *_: "0.157.1")
    monkeypatch.setattr(DeepSeekStateAdapter, "_binary", lambda _: "fake")
    service = ProviderStateService(state, lambda: [])
    assert service.resolve("deepseek", "sem-projeto") is None
    adapter = service._adapter("deepseek")
    snapshot = adapter.read_state(None)
    assert snapshot.provider == "deepseek" and snapshot.engine == "codex"
    assert len(snapshot.items) == 1
    assert snapshot.items[0].name == "DeepSeek instructions"
    assert all(not row.writable for row in snapshot.items)
    payload = asyncio.run(service.read('deepseek', 'sem-projeto'))
    assert isinstance(payload, dict)
    assert payload['snapshot']['provider'] == 'deepseek'
    assert payload['snapshot']['items'][0]['details']['preview'].startswith('# DeepSeek')


def test_claude_managed_hooks_ignore_user_disable_all(claude):
    adapter, home = claude
    put(home / ".claude/settings.json", {"disableAllHooks": True})
    put(
        adapter.managed_dir / "managed-settings.json",
        {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "true"}]}]}},
    )
    assert next(row for row in adapter.read_state(None).items if row.kind == "hook").enabled


def test_instruction_symlinks_and_external_imports_never_preview_credentials(claude, tmp_path):
    adapter, home = claude
    project = tmp_path / "project"
    secret = put(tmp_path / "private/auth.json", '{"token":"private-content"}')
    put(project / "CLAUDE.md", f"@{secret}\n")
    (project / ".claude").symlink_to(secret.parent, target_is_directory=True)
    put(secret.parent / "CLAUDE.md", "private-content")
    snapshot = adapter.read_state(project)
    assert "private-content" not in json.dumps(dataclasses.asdict(snapshot))
    assert any("external import pending" in warning for warning in snapshot.warnings)


def test_hook_redaction_http_headers_and_exec_arguments():
    from adapters.shared.orchestration_state import safe_details

    result = safe_details(
        {
            "command": 'curl --password="two words" https://user:pass@example.com?token=secret',
            "headers": {"Authorization": "Bearer hidden", "X-Api-Key": "hidden"},
            "args": ["--token", "hidden", "--mode", "plain"],
            "input": {"nested": {"password": "hidden"}},
        }
    )
    serialized = json.dumps(result)
    assert not any(
        secret in serialized for secret in ("two words", "user:pass", "hidden", "token=secret")
    )
    assert "plain" in serialized


def test_codex_untrusted_project_hooks_remain_visible_when_native_list_skips_them(
    tmp_path, isolated_provider_homes, monkeypatch
):
    import adapters.codex.state as module

    project = tmp_path / "project"
    put(
        project / ".codex/hooks.json",
        {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo safe"}]}]}},
    )

    async def ask(*args, **kwargs):
        return {
            "config": {
                "layers": [
                    {
                        "name": {"type": "project", "dotCodexFolder": str(project / ".codex")},
                        "version": "v1",
                        "config": {},
                        "disabledReason": "untrusted",
                    }
                ]
            },
            "hooks": {"data": []},
        }, {}

    monkeypatch.setattr(module, "_ask", ask)
    monkeypatch.setattr(module, "_cli_version", lambda *_: "0.157.1")
    adapter = CodexStateAdapter()
    monkeypatch.setattr(adapter, "_binary", lambda: "fake")
    hook = next(row for row in adapter.read_state(project).items if row.kind == "hook")
    assert hook.scope == "project" and not hook.enabled
    assert hook.details["status"] == "pending project trust"
