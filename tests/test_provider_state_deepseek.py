"""DeepSeek facade uses only its private Codex home and the existing CLI writer."""

import asyncio
import json
import os
from pathlib import Path

import pytest

from adapters.shared.provider_state import ProviderStateUnsupportedError, SecretStr
from control.provider_state import ProviderStateService


@pytest.fixture
def facade(tmp_path, monkeypatch):
    fake = Path(__file__).parent / "fixtures" / "fake-codex"
    monkeypatch.setenv("PATH", f"{fake}{os.pathsep}{os.environ['PATH']}")
    state = tmp_path / "state"
    home = state / "providers" / "deepseek"
    home.mkdir(parents=True)
    (state / "providers" / "home").mkdir()
    (home / "config.toml").write_text("[plugins.private]\nenabled = true\n")
    (home / "fake-app-server.json").write_text(
        json.dumps(
            {
                "plugins": [{"id": "private", "name": "Private"}],
                "profile_layers": [
                    {"profile": "work", "config": {"apps": {"mail": {"enabled": False}}}}
                ],
            }
        )
    )
    return ProviderStateService(state, lambda: [], track_notices=False), home


def test_deepseek_snapshot_and_writer_stay_in_private_home(facade):
    service, home = facade
    assert service.resolve("deepseek", "sem-projeto") is None
    adapter = service._adapter("deepseek")
    snapshot = adapter.read_state(None)
    assert (snapshot.provider, snapshot.engine) == ("deepseek", "codex")
    private = next(item for item in snapshot.items if item.id == "plugin:private")
    assert private.source == str(home / "config.toml")
    fresh = adapter.set_enabled(private.id, "user", False, snapshot.fingerprint)
    assert fresh.provider == "deepseek"
    assert not next(item for item in fresh.items if item.id == private.id).enabled
    assert "enabled = false" in (home / "config.toml").read_text()
    assert adapter.environment["HOME"] == str(home.parent / "home")
    assert all(str(home.parent) in str(path) for path in adapter.watch_paths(None))


def test_deepseek_profile_layer_remains_read_only_without_fabricated_kinds(facade):
    service, _ = facade
    adapter = service._adapter("deepseek")
    snapshot = adapter.read_state(None)
    profile = next(item for item in snapshot.items if item.id == "app:mail")
    assert (profile.scope, profile.writable) == ("profile", False)
    with pytest.raises(ProviderStateUnsupportedError):
        adapter.set_enabled(profile.id, "profile", True, snapshot.fingerprint)
    assert not any(item.kind in ("hook", "instructions") for item in snapshot.items)


def test_deepseek_set_api_key_reuses_private_atomic_storage(facade):
    service, _ = facade
    adapter = service._adapter("deepseek")
    adapter.set_api_key(SecretStr("fixture-facade-key-123456"))
    key = service.state / "deepseek.key"
    assert key.read_text() == "fixture-facade-key-123456"
    assert key.stat().st_mode & 0o777 == 0o600


def test_deepseek_service_read_returns_its_provider_identity(facade):
    service, _ = facade
    result = asyncio.run(service.read("deepseek", "sem-projeto"))
    assert result["snapshot"]["provider"] == "deepseek"
    assert result["snapshot"]["engine"] == "codex"


def test_deepseek_shared_root_is_private_and_named(facade):
    service, home = facade
    adapter = service._adapter("deepseek")
    root = home.parent / "home" / ".agents" / "skills"
    path = str(root / "notes" / "SKILL.md")
    fixture = home / "fake-app-server.json"
    data = json.loads(fixture.read_text())
    data["skills"] = [{"path": path, "name": "Notes", "scope": "user"}]
    fixture.write_text(json.dumps(data))
    item = next(item for item in adapter.read_state(None).items if item.kind == "skill")
    assert str(root) in item.reason
    assert "other providers using this root" in item.reason
    assert item.writable and item.affects == ()
    assert root in adapter.watch_paths(None)


@pytest.mark.parametrize("directory", ["deepseek", "home"])
def test_deepseek_facade_refuses_linked_home_before_cli(facade, tmp_path, directory):
    from adapters.shared.provider_state import ProviderStateSchemaError

    service, home = facade
    moved = tmp_path / "foreign"
    linked = home.parent / directory
    linked.rename(moved)
    linked.symlink_to(moved, target_is_directory=True)
    with pytest.raises(ProviderStateSchemaError):
        service._adapter("deepseek").read_state(None)
    assert not (moved / "fake-app-server.log").exists()


def test_deepseek_state_rpc_pins_file_store_without_a_key(facade, monkeypatch):
    from contextlib import asynccontextmanager

    import adapters.codex.state as codex_state

    service, _ = facade
    commands = []
    connection = codex_state.connection

    @asynccontextmanager
    async def capture(command, **kwargs):
        commands.append(command)
        async with connection(command, **kwargs) as rpc:
            yield rpc

    monkeypatch.setattr(codex_state, "connection", capture)
    adapter = service._adapter("deepseek")
    before = adapter.read_state(None)
    adapter.set_enabled("plugin:private", "user", False, before.fingerprint)
    assert len(commands) >= 3
    assert all('cli_auth_credentials_store="file"' in command for command in commands)
    assert adapter.credential_isolation()["home"] == adapter.environment["CODEX_HOME"]
    assert not (service.state / "deepseek.key").exists()


def test_deepseek_project_view_never_reads_or_writes_owner_trust(facade, monkeypatch, tmp_path):
    from agent_service.errors import APIError

    service, _ = facade
    service.projects = lambda: [{"id": "project", "root": str(tmp_path)}]

    def forbidden(*args):
        raise AssertionError("DeepSeek must not consult the owner's orchestration")

    monkeypatch.setattr(service, "_security_metadata", forbidden)
    result = asyncio.run(service.read("deepseek", "project"))
    assert result["snapshot"]["provider"] == "deepseek"
    assert "trust" not in result
    with pytest.raises(APIError):
        asyncio.run(service.security_write("deepseek", "project"))


@pytest.mark.parametrize("name", ["auth.json", "secrets/codex_auth.age", "config.toml"])
def test_deepseek_facade_refuses_credentials_and_config_aliases(facade, tmp_path, name):
    from adapters.shared.provider_state import ProviderStateSchemaError

    service, home = facade
    path = home / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if name == "config.toml":
        target = tmp_path / "owner-config"
        path.rename(target)
        path.symlink_to(target)
    else:
        path.write_bytes(b"foreign credentials must remain untouched")
    before = path.read_bytes()
    with pytest.raises(ProviderStateSchemaError):
        service._adapter("deepseek").read_state(None)
    assert not (home / "fake-app-server.log").exists()
    assert path.read_bytes() == before
