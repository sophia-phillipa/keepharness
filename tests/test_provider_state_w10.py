"""W10 uses fake owner homes even when the inherited environment names harness homes."""

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from adapters.shared.provider_state import fingerprint
from control.provider_state import ProviderStateService
from tests.test_provider_state_codex import GITHUB, configure, seed


@pytest.fixture
def owner(isolated_provider_homes, monkeypatch):
    fixtures = Path(__file__).parent / "fixtures"
    monkeypatch.setenv(
        "PATH", f"{fixtures / 'fake-codex'}:{fixtures / 'fake-claude'}:{os.environ['PATH']}"
    )
    return isolated_provider_homes


def service_at(state, tmp_path):
    state.mkdir(parents=True, exist_ok=True)
    service = ProviderStateService(state, lambda: [], clock=lambda: 100)
    service._adapter("claude").managed_dir = tmp_path / "managed"
    return service


def read(service, provider):
    service.cache.clear()
    result = asyncio.run(service.read(provider, "sem-projeto"))
    assert isinstance(result, dict)
    return result


@pytest.mark.parametrize("provider", ["codex", "claude"])
@pytest.mark.parametrize("redirect_home", [False, True])
def test_service_ignores_harness_homes_and_writes_owner_state(
    owner, tmp_path, monkeypatch, provider, redirect_home
):
    state = tmp_path / "state"
    harness = state / "providers" / "home"
    for name in (".codex", ".claude"):
        (harness / name).mkdir(parents=True)
    seed(owner / ".codex")
    seed(harness / ".codex")
    settings = json.dumps({"enabledPlugins": {"x@y": True}})
    (owner / ".claude" / "settings.json").write_text(settings)
    (harness / ".claude" / "settings.json").write_text(settings)
    monkeypatch.setenv("CODEX_HOME", str(harness / ".codex"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(harness / ".claude"))
    if redirect_home:
        import pwd

        monkeypatch.setattr(pwd, "getpwuid", lambda uid: SimpleNamespace(pw_dir=str(owner)))
        monkeypatch.setenv("HOME", str(harness))
    service = service_at(state, tmp_path)
    before = dict(os.environ)
    first = read(service, provider)
    item_id = GITHUB if provider == "codex" else "plugin:x@y"
    enabled = provider == "codex"
    changed = asyncio.run(
        service.write(
            provider, "sem-projeto", item_id, "user", enabled, first["snapshot"]["fingerprint"]
        )
    )
    assert isinstance(changed, dict)
    if provider == "codex":
        assert (
            '[plugins."github@openai-curated"]\nenabled = true'
            in (owner / ".codex" / "config.toml").read_text()
        )
        assert "enabled = false  # turned off" in (harness / ".codex" / "config.toml").read_text()
        assert not (harness / ".codex" / "fake-app-server.log").exists()
    else:
        assert (
            json.loads((owner / ".claude" / "settings.json").read_text())["enabledPlugins"]["x@y"]
            is False
        )
        assert (harness / ".claude" / "settings.json").read_text() == settings
    assert all(
        not path.is_relative_to(harness) for path in service._adapter(provider).watch_paths(None)
    )
    assert dict(os.environ) == before


def test_service_captures_custom_owner_paths_and_home_json(owner, tmp_path, monkeypatch):
    custom = tmp_path / "custom"
    for name in ("codex", "claude"):
        (custom / name).mkdir(parents=True)
    seed(custom / "codex")
    (custom / "claude" / "settings.json").write_text('{"enabledPlugins":{"x@y":true}}')
    (owner / ".claude.json").write_text('{"mcpServers":{"owner":{}}}')
    (custom / "claude" / ".claude.json").write_text('{"mcpServers":{"wrong":{}}}')
    monkeypatch.setenv("CODEX_HOME", str(custom / "codex"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(custom / "claude"))
    service = service_at(tmp_path / "state", tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "later"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "later" / ".codex"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "later" / ".claude"))
    assert any(item["id"] == GITHUB for item in read(service, "codex")["snapshot"]["items"])
    ids = {item["id"] for item in read(service, "claude")["snapshot"]["items"]}
    assert ids == {"plugin:x@y", "mcp:owner"}
    assert service._adapter("codex").watch_paths(None)[0] == custom / "codex" / "config.toml"
    assert service._adapter("claude").watch_paths(None)[1] == owner / ".claude.json"


@pytest.mark.parametrize("provider", ["codex", "claude"])
def test_hidden_skills_and_harness_sync_do_not_make_notices(owner, tmp_path, monkeypatch, provider):
    state = tmp_path / "state"
    harness = state / "providers" / "home"
    directory = ".codex" if provider == "codex" else ".claude"
    visible = owner / directory / "skills" / "visible" / "SKILL.md"
    hidden = owner / directory / "skills" / ".trash" / "hidden" / "SKILL.md"
    for path in (visible, hidden):
        path.parent.mkdir(parents=True)
        path.write_text("# Test skill\n")
    if provider == "codex":
        seed(owner / directory)
        configure(
            owner / directory,
            skills=[
                {"name": "visible", "path": str(visible), "scope": "user"},
                {"name": "hidden", "path": str(hidden), "scope": "user"},
            ],
        )
    else:
        (owner / directory / "settings.json").write_text("{}")
        (hidden.parents[1] / "SKILL.md").write_text("# Hidden folder itself\n")
    (harness / directory).mkdir(parents=True)
    monkeypatch.setenv(
        "CODEX_HOME" if provider == "codex" else "CLAUDE_CONFIG_DIR", str(harness / directory)
    )
    service = service_at(state, tmp_path)
    first = read(service, provider)
    skills = [item for item in first["snapshot"]["items"] if item["kind"] == "skill"]
    assert [item["name"] for item in skills] == ["visible"]
    if provider == "claude":
        assert skills[0]["source"].endswith("/visible/SKILL.md")
    synced = harness / directory / "skills" / "synced"
    synced.mkdir(parents=True)
    (synced / "SKILL.md").write_text("# Harness sync\n")
    assert read(service, provider)["external_changes"] == []
    trash = synced.parent / ".trash"
    trash.mkdir()
    synced.rename(trash / synced.name)
    assert read(service, provider)["external_changes"] == []


def test_directory_fingerprint_ignores_hidden_metadata_but_tracks_each_visible_child(tmp_path):
    folder = tmp_path / "skills"
    folder.mkdir()
    first = folder / "first"
    second = folder / "second"
    first.mkdir()
    second.mkdir()
    os.utime(first, ns=(1, 1))
    os.utime(second, ns=(10, 10))
    before = fingerprint([folder])
    hidden = folder / ".trash"
    hidden.mkdir()
    assert fingerprint([folder]) == before
    os.utime(hidden, ns=(20, 20))
    assert fingerprint([folder]) == before
    hidden.rmdir()
    assert fingerprint([folder]) == before
    os.utime(first, ns=(2, 2))
    assert fingerprint([folder]) != before
    before = fingerprint([folder])
    first.rename(folder / "renamed")
    assert fingerprint([folder]) != before


def test_hidden_managed_policy_edits_still_make_notices(owner, tmp_path):
    service = service_at(tmp_path / "state", tmp_path)
    policy = tmp_path / "managed" / "managed-settings.d" / ".policy.json"
    policy.parent.mkdir(parents=True)
    policy.write_text('{"enabledPlugins":{"x@y":true}}')
    assert read(service, "claude")["external_changes"] == []
    policy.write_text('{"enabledPlugins":{"x@y":false}}')
    changes = read(service, "claude")["external_changes"]
    assert len(changes) == 1
    assert changes[0]["item_id"] == "plugin:x@y"
    assert changes[0]["after"] is False


@pytest.mark.parametrize("legacy", [True, False])
def test_source_migration_rebaselines_only_unknown_or_changed_homes(owner, tmp_path, legacy):
    state = tmp_path / "state"
    settings = owner / ".claude" / "settings.json"
    settings.write_text('{"enabledPlugins":{"x@y":true}}')
    service = service_at(state, tmp_path)
    assert read(service, "claude")["external_changes"] == []
    settings.write_text('{"enabledPlugins":{"x@y":false}}')
    pending = read(service, "claude")["external_changes"]
    assert len(pending) == 1
    seen_file = state / "provider-state-seen.json"
    seen = json.loads(seen_file.read_text())
    entry = seen["entries"]["claude|sem-projeto"]
    # Old rows retain only basenames, so a harness baseline has no recoverable home.
    if legacy:
        entry.pop("source_identity", None)
        entry["items"] = {
            "skill:harness": {
                "enabled": True,
                "name": "harness",
                "source": "harness",
                "scope": "user",
            }
        }
    else:
        entry["source_identity"] = "previous-harness-home"
    seen_file.write_text(json.dumps(seen))
    reopened = service_at(state, tmp_path)
    assert read(reopened, "claude")["external_changes"] == []
    settings.write_text("{}")
    changes = read(reopened, "claude")["external_changes"]
    assert len(changes) == 1
    assert changes[0]["item_id"] == "plugin:x@y"
    assert changes[0]["change"] == "removed"
    # Matching source identity must retain actual pending owner notices on restart.
    assert read(service_at(state, tmp_path), "claude")["external_changes"] == changes
