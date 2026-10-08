"""W10 uses fake owner homes even when the inherited environment names harness homes."""

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from adapters.shared.provider_state import ProviderStateSchemaError, fingerprint
from control.provider_state import ProviderStateService, _owner_environment
from control.server import create_app
from tests.owner_session import sign_in
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


def reported_plugin(collection):
    return {"id": "fixture@local", "source": {"type": "local", "path": str(collection.parent)}}


@pytest.mark.parametrize("provider,folder", [("codex", ".codex"), ("claude", ".claude")])
@pytest.mark.parametrize("failure", ["loop", "unreadable"])
def test_unavailable_owner_cli_is_scoped_to_its_provider(
    owner, tmp_path, monkeypatch, provider, folder, failure
):
    broken = owner / folder
    if failure == "loop":
        broken.rmdir()
        broken.symlink_to(broken, target_is_directory=True)
    else:
        broken.chmod(0)
    healthy = "claude" if provider == "codex" else "codex"
    if healthy == "codex":
        seed(owner / ".codex")
    else:
        (owner / ".claude" / "settings.json").write_text('{"enabledPlugins":{"x@y":true}}')
    try:
        app = create_app(tmp_path / "state")
        service = app.state.manager.provider_state
        if healthy == "claude":
            service._adapter("claude").managed_dir = tmp_path / "managed"
        assert not any(item["enabled"] for item in app.state.manager.settings["services"].values())
        client = TestClient(app, base_url="http://127.0.0.1:18094")
        sign_in(client)
        assert client.get("/").status_code == 200
        url = f"/api/provider-state?provider={provider}&project_id=sem-projeto"
        response = client.get(url)
        assert response.status_code == 422
        assert response.json()["error"] == "provider_state_unreadable"
        assert str(broken) not in response.text
        body = dict(
            provider=provider,
            project_id="sem-projeto",
            item_id="plugin:x@y",
            scope="user",
            enabled=False,
            fingerprint="unreadable",
        )
        response = client.post("/api/provider-state", json=body, headers={"X-Harness-Admin": "1"})
        assert response.status_code == 422
        assert response.json()["error"] == "provider_state_unreadable"
        good = client.get(f"/api/provider-state?provider={healthy}&project_id=sem-projeto")
        assert good.status_code == 200
        body.update(
            provider=healthy,
            item_id=GITHUB if healthy == "codex" else "plugin:x@y",
            enabled=healthy == "codex",
            fingerprint=good.json()["snapshot"]["fingerprint"],
        )
        assert (
            client.post(
                "/api/provider-state", json=body, headers={"X-Harness-Admin": "1"}
            ).status_code
            == 200
        )
        if failure == "loop":
            broken.unlink()
            broken.mkdir()
        else:
            broken.chmod(0o700)
        if provider == "codex":
            seed(broken)
        else:
            (broken / "settings.json").write_text('{"enabledPlugins":{"x@y":true}}')
            service._adapter("claude").managed_dir = tmp_path / "managed"
        assert client.get(url).status_code == 200
    finally:
        if failure == "unreadable":
            broken.chmod(0o700)


@pytest.mark.parametrize("failure", ["loop", "unreadable"])
def test_unavailable_shared_home_does_not_abort_panel(owner, tmp_path, monkeypatch, failure):
    import pwd

    broken = tmp_path / "unavailable-home"
    if failure == "loop":
        broken.symlink_to(broken, target_is_directory=True)
    else:
        broken.mkdir(mode=0)
    seed(owner / ".codex")
    (owner / ".claude" / "settings.json").write_text('{"enabledPlugins":{"x@y":true}}')
    monkeypatch.setenv("HOME", str(broken))
    monkeypatch.setattr(pwd, "getpwuid", lambda uid: SimpleNamespace(pw_dir=str(broken)))
    try:
        app = create_app(tmp_path / "state")
        client = TestClient(app, base_url="http://127.0.0.1:18094")
        sign_in(client)
        assert client.get("/").status_code == 200
        for provider in ("codex", "claude"):
            response = client.get(f"/api/provider-state?provider={provider}&project_id=sem-projeto")
            assert response.status_code == 422
            assert response.json()["error"] == "provider_state_unreadable"
            assert str(broken) not in response.text
            response = client.post(
                "/api/provider-state",
                json=dict(
                    provider=provider,
                    project_id="sem-projeto",
                    item_id="plugin:x@y",
                    scope="user",
                    enabled=False,
                    fingerprint="unreadable",
                ),
                headers={"X-Harness-Admin": "1"},
            )
            assert response.status_code == 422
            assert response.json()["error"] == "provider_state_unreadable"
    finally:
        if failure == "unreadable":
            broken.chmod(0o700)


@pytest.mark.parametrize("location", ["inside_home", "ancestor_named_skills"])
def test_codex_known_project_collections_preserve_visible_skills(owner, tmp_path, location):
    home = owner / ".codex"
    seed(home)
    project = (
        home / "projects" / "repo" if location == "inside_home" else tmp_path / "skills" / "repo"
    )
    cwd = project if location == "inside_home" else project / "subdir"
    cwd.mkdir(parents=True)
    root = project / ".agents" / "skills"
    visible = root / "visible" / "SKILL.md"
    hidden = root / ".trash" / ".agents" / "skills" / "hidden" / "SKILL.md"
    configure(
        home,
        skills=[
            {"name": "visible", "path": str(visible), "scope": "repo"},
            {"name": "hidden", "path": str(hidden), "scope": "repo"},
        ],
    )
    snapshot = service_at(tmp_path / "state", tmp_path)._adapter("codex").read_state(cwd)
    assert [item.id for item in snapshot.items if item.kind == "skill"] == [f"skill:{visible}"]


def test_hidden_custom_codex_home_does_not_hide_visible_plugin_skills(owner, tmp_path, monkeypatch):
    home = tmp_path / "custom" / ".agents" / "skills" / ".config" / "codex"
    home.mkdir(parents=True)
    seed(home)
    monkeypatch.setenv("CODEX_HOME", str(home))
    visible = (
        home / "plugins" / "cache" / "store" / "plugin" / "v1" / "skills" / "visible" / "SKILL.md"
    )
    configure(
        home,
        plugins=[reported_plugin(visible.parents[1])],
        skills=[{"name": "visible", "path": str(visible), "scope": "user"}],
    )
    snapshot = read(service_at(tmp_path / "state", tmp_path), "codex")["snapshot"]
    assert [item["id"] for item in snapshot["items"] if item["kind"] == "skill"] == [
        f"skill:{visible}"
    ]


@pytest.mark.parametrize("location", ["nested", "external"])
def test_codex_uses_the_longest_reported_plugin_collection(owner, tmp_path, location):
    home = owner / ".codex"
    seed(home)
    base = home / "skills" if location == "nested" else tmp_path / "external"
    root = base / ".cache" / "plugin" / "skills"
    alias = tmp_path / "plugin-link"
    root.parent.mkdir(parents=True)
    alias.symlink_to(root.parent, target_is_directory=True)
    visible = root / "visible" / "SKILL.md"
    hidden = root / ".trash" / ".agents" / "skills" / "hidden" / "SKILL.md"
    configure(
        home,
        plugins=[reported_plugin(alias / "skills")],
        skills=[
            {"name": "visible", "path": str(visible), "scope": "user", "pluginId": "fixture@local"},
            {"name": "hidden", "path": str(hidden), "scope": "user", "pluginId": "fixture@local"},
        ],
    )
    snapshot = service_at(tmp_path / "state", tmp_path)._adapter("codex").read_state(None)
    assert [item.id for item in snapshot.items if item.kind == "skill"] == [f"skill:{visible}"]


def test_codex_preserves_reports_outside_known_collections(owner, tmp_path):
    home = owner / ".codex"
    seed(home)
    paths = [
        tmp_path / "unknown" / "skills" / ".trash" / "SKILL.md",
        home / "unknown" / ".system" / "skills" / "SKILL.md",
    ]
    configure(
        home,
        skills=[{"name": f"skill-{i}", "path": str(path)} for i, path in enumerate(paths)],
    )
    snapshot = service_at(tmp_path / "state", tmp_path)._adapter("codex").read_state(None)
    assert {item.id for item in snapshot.items if item.kind == "skill"} == {
        f"skill:{path}" for path in paths
    }


@pytest.mark.parametrize("collection", ["owner", "plugin", "project", "system"])
def test_hidden_skill_components_cannot_reset_the_collection(owner, tmp_path, collection):
    home = owner / ".codex"
    seed(home)
    project = tmp_path / "project"
    project.mkdir()
    root = {
        "owner": home / "skills",
        "plugin": home / "plugins" / "cache" / "store" / "plugin" / "v1" / "skills",
        "project": project / ".agents" / "skills",
        "system": home / "skills" / "bundled",
    }[collection]
    # Each generated path retains a hidden descendant before later collection-like names.
    hidden = [
        root.joinpath(*(["visible"] * depth), hidden_name, *suffix, "hidden", "SKILL.md")
        for depth in range(4)
        for hidden_name in (".trash", ".system", ".agents")
        for suffix in [("skills",), (".agents", "skills"), ("skills", ".agents", "skills")]
    ]
    visible = [root.joinpath(*(["skills"] * depth), "visible", "SKILL.md") for depth in range(4)]
    paths = hidden + visible
    configure(
        home,
        plugins=[reported_plugin(root)] if collection == "plugin" else [],
        skills=[
            {"name": f"skill-{i}", "path": str(path), "scope": "user"}
            for i, path in enumerate(paths)
        ],
    )
    snapshot = service_at(tmp_path / "state", tmp_path)._adapter("codex").read_state(project)
    assert {item.id for item in snapshot.items if item.kind == "skill"} == {
        f"skill:{path}" for path in visible
    }


@pytest.mark.parametrize("provider", ["codex", "claude"])
@pytest.mark.parametrize("redirect_home", [False, True])
@pytest.mark.parametrize("alias", ["none", "state", "environment"])
def test_service_ignores_harness_homes_and_writes_owner_state(
    owner, tmp_path, monkeypatch, provider, redirect_home, alias
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
    if alias == "state":
        link = tmp_path / "state-link"
        link.symlink_to(state, target_is_directory=True)
        state = link
    elif alias == "environment":
        link = tmp_path / "environment-link"
        link.symlink_to(harness, target_is_directory=True)
        monkeypatch.setenv("CODEX_HOME", str(link / ".codex"))
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(link / ".claude"))
        if redirect_home:
            monkeypatch.setenv("HOME", str(link))
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


@pytest.mark.parametrize("name", ["CODEX_HOME", "CLAUDE_CONFIG_DIR"])
def test_custom_owner_alias_and_missing_directory_keep_their_spelling(
    owner, tmp_path, monkeypatch, name
):
    target = tmp_path / "custom"
    target.mkdir()
    alias = tmp_path / "custom-link"
    alias.symlink_to(target, target_is_directory=True)
    monkeypatch.setenv(name, str(alias))
    assert _owner_environment(tmp_path / "state")[name] == str(alias)
    missing = alias / "not-created"
    monkeypatch.setenv(name, str(missing))
    assert _owner_environment(tmp_path / "state")[name] == str(missing)


@pytest.mark.parametrize(
    "name,folder", [("CODEX_HOME", ".codex"), ("CLAUDE_CONFIG_DIR", ".claude")]
)
@pytest.mark.parametrize("suffix", ["", "not-created"])
def test_unresolvable_custom_home_reports_a_provider_error(
    owner, tmp_path, monkeypatch, name, folder, suffix
):
    loop = tmp_path / "loop"
    loop.symlink_to(loop, target_is_directory=True)
    monkeypatch.setenv(name, str(loop / suffix))
    with pytest.raises(ProviderStateSchemaError):
        _owner_environment(tmp_path / "state")


@pytest.mark.parametrize(
    "name,folder", [("CODEX_HOME", ".codex"), ("CLAUDE_CONFIG_DIR", ".claude")]
)
def test_owner_default_alias_into_harness_is_refused(owner, tmp_path, monkeypatch, name, folder):
    state = tmp_path / "state"
    harness = state / "providers" / "home" / folder
    harness.mkdir(parents=True)
    default = owner / folder
    default.rmdir()
    default.symlink_to(harness, target_is_directory=True)
    monkeypatch.setenv(name, str(harness))
    with pytest.raises(ProviderStateSchemaError):
        _owner_environment(state)


def test_unresolvable_state_directory_is_refused(owner, tmp_path):
    loop = tmp_path / "loop"
    loop.symlink_to(loop, target_is_directory=True)
    with pytest.raises(ProviderStateSchemaError):
        _owner_environment(loop)


@pytest.mark.parametrize("nested", ["skills/SKILL.md", "skills/hidden/SKILL.md"])
@pytest.mark.parametrize("collection", ["owner", "project", "plugin", "system"])
def test_codex_hidden_ancestor_cannot_restart_the_collection_boundary(
    owner, tmp_path, monkeypatch, nested, collection
):
    # An ancestor named skills must not become the collection root either.
    home = tmp_path / "skills" / ".owner" / "codex"
    home.mkdir(parents=True)
    seed(home)
    monkeypatch.setenv("CODEX_HOME", str(home))
    project = tmp_path / "skills" / ".project"
    project.mkdir()
    roots = {
        "owner": home / "skills",
        "project": project / ".agents" / "skills",
        "plugin": home / "plugins" / "cache" / "store" / "plugin" / "v1" / "skills",
        "system": home / "skills" / "bundled",
    }
    root = roots[collection]
    hidden = root / ".trash" / nested
    visible = root / "skills" / "visible" / "SKILL.md"
    scope = {"owner": "user", "project": "repo", "plugin": "user", "system": "system"}[collection]
    configure(
        home,
        plugins=[reported_plugin(root)] if collection == "plugin" else [],
        skills=[
            {"name": "hidden", "path": str(hidden), "scope": scope},
            {"name": "visible", "path": str(visible), "scope": scope},
        ],
    )
    service = service_at(tmp_path / "state", tmp_path)
    snapshot = service._adapter("codex").read_state(project)
    assert [item.name for item in snapshot.items if item.kind == "skill"] == ["visible"]


@pytest.mark.parametrize("collection", ["owner", "project", "project_collection", "plugin"])
def test_codex_canonical_skill_paths_keep_alias_collection_boundaries(
    owner, tmp_path, monkeypatch, collection
):
    base = tmp_path / "skills" / ".locations"
    home = base / "codex"
    home.mkdir(parents=True)
    seed(home)
    home_alias = tmp_path / "codex-link"
    home_alias.symlink_to(home, target_is_directory=True)
    monkeypatch.setenv("CODEX_HOME", str(home_alias))
    project = base / "project"
    project.mkdir()
    project_alias = tmp_path / "project-link"
    project_alias.symlink_to(project, target_is_directory=True)
    root = home / "skills" if collection == "owner" else project / ".agents" / "skills"
    if collection == "plugin":
        root = home / "plugins" / "cache" / "store" / "plugin" / "v1" / "skills"
    if collection == "project_collection":
        target = base / "collection"
        target.mkdir()
        root.parent.mkdir()
        root.symlink_to(target, target_is_directory=True)
        root = target
    visible = root / "skills" / "visible" / "SKILL.md"
    hidden = root / ".trash" / "skills" / "hidden" / "SKILL.md"
    scope = "user" if collection in ("owner", "plugin") else "repo"
    configure(
        home,
        plugins=[reported_plugin(root)] if collection == "plugin" else [],
        skills=[
            {"name": "visible", "path": str(visible), "scope": scope},
            {"name": "hidden", "path": str(hidden), "scope": scope},
        ],
    )
    service = service_at(tmp_path / "state", tmp_path)
    snapshot = service._adapter("codex").read_state(project_alias)
    skills = [item for item in snapshot.items if item.kind == "skill"]
    assert [item.name for item in skills] == ["visible"]
    assert skills[0].id == f"skill:{visible}"
    assert service.environment["CODEX_HOME"] == str(home_alias)


def test_codex_unresolvable_project_collection_keeps_other_skill_roots(owner, tmp_path):
    project = tmp_path / "project"
    (project / ".agents").mkdir(parents=True)
    loop = project / ".agents" / "skills"
    loop.symlink_to(loop, target_is_directory=True)
    home = owner / ".codex"
    seed(home)
    visible = home / "skills" / "visible" / "SKILL.md"
    configure(home, skills=[{"name": "visible", "path": str(visible), "scope": "user"}])
    service = service_at(tmp_path / "state", tmp_path)
    snapshot = service._adapter("codex").read_state(project)
    assert [item.id for item in snapshot.items if item.kind == "skill"] == [f"skill:{visible}"]


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
