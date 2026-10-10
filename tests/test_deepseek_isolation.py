"""DeepSeek's private home, credential guard and atomic key updates."""

import asyncio
import hashlib
import json
import os
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pydantic import SecretStr

from adapters.deepseek import account, backend
from adapters.deepseek.credentials import keyring_entry_exists
from adapters.deepseek.state import DeepSeekStateAdapter
from adapters.shared.provider_state import ProviderStateSchemaError
from agent_service.errors import UserMessageError
from agent_service.tools import ToolError


def configuration(tmp_path):
    key = tmp_path / "deepseek.key"
    key.write_text("fixture-deepseek-secret-123")
    (tmp_path / "providers" / "deepseek").mkdir(mode=0o700, parents=True)
    (tmp_path / "providers" / "home").mkdir(mode=0o700, parents=True)
    return {"binary": "codex", "api_provider": {"url": "http://127.0.0.1:9", "key_file": str(key)}}


@pytest.mark.parametrize("scheduled", [False, True])
def test_home_is_anchored_to_key_state_not_owner_or_stale_config(tmp_path, monkeypatch, scheduled):
    config = configuration(tmp_path)
    foreign = tmp_path / "chatgpt"
    foreign.mkdir()
    (foreign / "auth.json").write_text("foreign-credential")
    config.update(provider_homes=str(foreign), schedule_id="fixture" if scheduled else None)
    monkeypatch.setenv("CODEX_HOME", str(foreign))
    runtime = backend.runtime_options(config, {})
    assert runtime.environment["CODEX_HOME"] == str(tmp_path / "providers" / "deepseek")
    assert runtime.environment["KEEPHARNESS_API_KEY"] == "fixture-deepseek-secret-123"
    assert 'cli_auth_credentials_store="file"' in runtime.command
    assert (foreign / "auth.json").read_text() == "foreign-credential"


@pytest.mark.parametrize("name", ["auth.json", "secrets/codex_auth.age"])
def test_foreign_credential_file_refuses_without_changes(tmp_path, name):
    config = configuration(tmp_path)
    home = tmp_path / "providers" / "deepseek"
    forbidden = home / name
    forbidden.parent.mkdir(parents=True, exist_ok=True)
    forbidden.write_bytes(b"foreign credential must not be read or removed")
    before = {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    with pytest.raises(ToolError, match="^deepseek_credential_isolation$"):
        backend.runtime_options(config, {})
    assert {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before


def test_invalid_private_home_refuses(tmp_path):
    config = configuration(tmp_path)
    (tmp_path / "providers" / "deepseek").rmdir()
    (tmp_path / "providers" / "deepseek").write_text("not a directory")
    with pytest.raises(ToolError, match="^deepseek_credential_isolation$"):
        backend.runtime_options(config, {})


def test_missing_private_home_refuses_without_recreation(tmp_path):
    config = configuration(tmp_path)
    home = tmp_path / "providers" / "deepseek"
    home.rmdir()
    with pytest.raises(ToolError, match="^deepseek_credential_isolation$"):
        backend.runtime_options(config, {})
    assert not home.exists()


@pytest.mark.parametrize("location", ["relative.key", [], "/missing-fixture-state/key"])
def test_invalid_key_anchor_never_falls_back_to_environment(tmp_path, location):
    config = configuration(tmp_path)
    config["api_provider"]["key_file"] = location
    with pytest.raises(
        ToolError, match="^deepseek_(credential_isolation|api_configuration_required)$"
    ):
        backend.runtime_options(config, {})


def test_set_key_uses_private_directory_and_preserves_unrelated_settings(tmp_path):
    tmp_path.chmod(0o755)
    settings = tmp_path / "settings.json"
    settings.write_bytes(b'{"keep": true}\n')
    account.store_key(tmp_path, "fixture-new-key-123")
    assert tmp_path.stat().st_mode & 0o777 == 0o700
    assert account.key_file(tmp_path).stat().st_mode & 0o777 == 0o600
    assert settings.read_bytes() == b'{"keep": true}\n'
    account.store_key(tmp_path, "fixture-replacement-key-123")
    assert account.key_file(tmp_path).read_text() == "fixture-replacement-key-123"
    assert account.key_file(tmp_path).stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "providers" / "deepseek").stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize("link", ["symlink", "hardlink"])
def test_set_key_refuses_unsafe_links(tmp_path, link):
    target = tmp_path / "unrelated"
    target.write_text("old credential")
    key = account.key_file(tmp_path)
    if link == "symlink":
        key.symlink_to(target)
    else:
        os.link(target, key)
    with pytest.raises(UserMessageError, match="Could not safely store"):
        account.store_key(tmp_path, "fixture-new-key-123")
    assert target.read_text() == "old credential"
    assert key.read_text() == "old credential"


def test_set_key_refuses_linked_state_directory(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    account.store_key(real, "fixture-old-key-123")
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)
    with pytest.raises(UserMessageError, match="Could not safely store"):
        account.store_key(linked, "fixture-new-key-123")
    assert account.key_file(real).read_text() == "fixture-old-key-123"


def test_failed_key_write_preserves_previous_bytes(tmp_path):
    account.store_key(tmp_path, "fixture-old-key-123")
    with patch("os.replace", side_effect=OSError("fixture failure")):
        with pytest.raises(UserMessageError, match="Could not safely store") as caught:
            account.store_key(tmp_path, "fixture-new-key-123")
    assert account.key_file(tmp_path).read_text() == "fixture-old-key-123"
    assert "fixture-new-key" not in str(caught.value)
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("probe", [True, None, OSError("probe refused")])
@pytest.mark.parametrize("scheduled", [False, True])
def test_keyring_credential_or_failed_probe_refuses_before_spawn(
    tmp_path, monkeypatch, probe, scheduled
):
    config = configuration(tmp_path)
    config["schedule_id"] = "fixture" if scheduled else None
    foreign = tmp_path / "chatgpt"
    foreign.mkdir()
    (foreign / "auth.json").write_bytes(b"unrelated ChatGPT credential")
    (tmp_path / "providers" / "deepseek" / "config.toml").write_bytes(b"# preserve\n")
    before = {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}

    def query(*args):
        if isinstance(probe, Exception):
            raise probe
        return probe

    monkeypatch.setattr("adapters.deepseek.credentials.keyring_entry_exists", query)
    with patch("adapters.codex.native.connection") as connection:
        with pytest.raises(ToolError, match="^deepseek_credential_isolation$"):
            asyncio.run(
                backend.run_native(
                    config,
                    "prompt",
                    lambda *_: None,
                    {"permissions": {}},
                    "deepseek-flash",
                    "high",
                    tmp_path / "session",
                    None,
                )
            )
    connection.assert_not_called()
    assert {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before


def test_encrypted_keyring_identity_is_also_refused(tmp_path, monkeypatch):
    config = configuration(tmp_path)
    monkeypatch.setattr(
        "adapters.deepseek.credentials.keyring_entry_exists", lambda service, _: service == "codex"
    )
    with pytest.raises(ToolError, match="^deepseek_credential_isolation$"):
        backend.runtime_options(config, {})


@pytest.mark.parametrize("status,expected", [(0, True), (44, False), (1, None)])
def test_macos_probe_distinguishes_absence_and_errors(monkeypatch, status, expected):
    monkeypatch.setattr("adapters.deepseek.credentials.sys.platform", "darwin")
    with patch(
        "adapters.deepseek.credentials.subprocess.run",
        return_value=subprocess.CompletedProcess([], status),
    ) as run:
        if expected is None:
            with pytest.raises(ValueError):
                keyring_entry_exists("Codex Auth", "cli|fixture")
        else:
            assert keyring_entry_exists("Codex Auth", "cli|fixture") is expected
    assert "-w" not in run.call_args.args[0]


def test_unavailable_platform_probe_refuses(monkeypatch):
    monkeypatch.setattr("adapters.deepseek.credentials.sys.platform", "unsupported")
    with patch("adapters.deepseek.credentials.subprocess.run") as run:
        with pytest.raises(ValueError, match="unavailable"):
            keyring_entry_exists("Codex Auth", "cli|fixture")
    run.assert_not_called()


def test_keyring_queries_only_exact_private_home_identifiers(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "adapters.deepseek.credentials.keyring_entry_exists",
        lambda *args: calls.append(args) or False,
    )
    backend.runtime_options(configuration(tmp_path), {})
    digest = hashlib.sha256(str(tmp_path / "providers" / "deepseek").encode()).hexdigest()[:16]
    assert calls == [("Codex Auth", "cli|" + digest), ("codex", "secrets|" + digest)]


@pytest.mark.parametrize(
    "output,expected",
    [
        ("(@ao [], @ao [])\n", False),
        ("([objectpath '/org/freedesktop/secrets/collection/login/1'], @ao [])\n", True),
    ],
)
def test_linux_probe_searches_metadata_without_reading_secrets(monkeypatch, output, expected):
    monkeypatch.setattr("adapters.deepseek.credentials.sys.platform", "linux")
    with patch(
        "adapters.deepseek.credentials.subprocess.run",
        return_value=subprocess.CompletedProcess([], 0, output),
    ) as run:
        assert keyring_entry_exists("Codex Auth", "cli|fixture") is expected
    args = run.call_args.args[0]
    assert "org.freedesktop.Secret.Service.SearchItems" in args
    assert args[-1] == "{'service': 'Codex Auth', 'username': 'cli|fixture'}"
    assert run.call_args.kwargs["check"] is True


def test_probe_unknown_output_is_failure(monkeypatch):
    monkeypatch.setattr("adapters.deepseek.credentials.sys.platform", "linux")
    with patch(
        "adapters.deepseek.credentials.subprocess.run",
        return_value=subprocess.CompletedProcess([], 0, "unexpected"),
    ):
        with pytest.raises(ValueError, match="inconclusive"):
            keyring_entry_exists("Codex Auth", "cli|fixture")


def test_vault_cannot_override_deepseek_transport_environment(tmp_path):
    from adapters.shared.process import child_environment
    from agent_service.secret_vault import execution_environment

    runtime = backend.runtime_options(configuration(tmp_path), {})
    with execution_environment(
        {"CODEX_HOME": "/foreign", "HOME": "/foreign", "KEEPHARNESS_API_KEY": "foreign"}
    ):
        effective = child_environment(runtime.environment, provider="deepseek")
    for name in ("HOME", "CODEX_HOME", "KEEPHARNESS_API_KEY"):
        assert effective[name] == runtime.environment[name]


def test_conflicting_key_write_preserves_concurrent_value(tmp_path):
    account.store_key(tmp_path, "fixture-old-key-123")
    original = os.fsync

    def conflict(fd):
        account.key_file(tmp_path).write_text("fixture-concurrent-key-123")
        return original(fd)

    with patch("os.fsync", side_effect=conflict):
        with pytest.raises(UserMessageError, match="Could not safely store"):
            account.store_key(tmp_path, "fixture-new-key-123")
    assert account.key_file(tmp_path).read_text() == "fixture-concurrent-key-123"


def test_overlapping_key_updates_refuse_the_second_writer(tmp_path):
    account.store_key(tmp_path, "fixture-old-key-123")
    entered, release = threading.Event(), threading.Event()
    errors = []
    original = os.fsync

    def hold(fd):
        entered.set()
        assert release.wait(timeout=5)
        return original(fd)

    def write():
        try:
            account.store_key(tmp_path, "fixture-first-key-123")
        except Exception as exc:
            errors.append(exc)

    with patch("os.fsync", side_effect=hold):
        worker = threading.Thread(target=write)
        worker.start()
        try:
            assert entered.wait(timeout=5)
            with pytest.raises(UserMessageError, match="Could not safely store"):
                account.store_key(tmp_path, "fixture-second-key-123")
        finally:
            release.set()
            worker.join(timeout=5)
    assert not worker.is_alive() and errors == []
    assert account.key_file(tmp_path).read_text() == "fixture-first-key-123"


@pytest.mark.parametrize("channel", ["answer_delta", "reasoning_delta", "reasoning_summary"])
def test_key_echoes_are_redacted_in_events_errors_and_logs(tmp_path, channel):
    from agent_service.log_config import redact
    from control.routes import read_logs

    config = configuration(tmp_path)
    sentinel = Path(config["api_provider"]["key_file"]).read_text()
    events = []

    async def turn(*args):
        event = args[1]
        event(channel, {"text": sentinel[:10]})
        event(channel, {"text": sentinel[10:]})
        event("provider_retrying", {"message": sentinel})
        assert sentinel not in redact("provider logged " + sentinel)
        from adapters.shared.process import StderrCapture

        diagnostic = StderrCapture({"KEEPHARNESS_API_KEY": sentinel})
        diagnostic.buffer.extend(("provider stderr " + sentinel).encode())
        assert sentinel not in diagnostic.text()
        (tmp_path / "harness.log").write_text(diagnostic.text())
        exported = await read_logs(
            SimpleNamespace(query_params={}), SimpleNamespace(state=tmp_path)
        )
        assert "provider stderr" in json.dumps(exported)
        assert sentinel not in json.dumps(exported)
        raise ToolError("deepseek_execution_failed: " + sentinel)

    with patch("adapters.deepseek.backend.run_turn", turn):
        with pytest.raises(ToolError) as caught:
            asyncio.run(
                backend.run_native(
                    config,
                    "prompt",
                    lambda kind, data: events.append((kind, data)),
                    {"permissions": {}},
                    "deepseek-flash",
                    "high",
                    tmp_path / "session",
                    None,
                )
            )
    assert sentinel not in str(caught.value)
    assert sentinel not in json.dumps(events)
    assert sentinel not in "".join(data.get("text", "") for _, data in events)
    # Persisted diagnostics remain safe after the transient redaction scope ends.
    exported = asyncio.run(
        read_logs(SimpleNamespace(query_params={}), SimpleNamespace(state=tmp_path))
    )
    assert sentinel not in json.dumps(exported)


def test_saved_key_without_private_home_is_provisioned_at_startup(tmp_path):
    config = configuration(tmp_path)
    home = tmp_path / "providers" / "deepseek"
    home.rmdir()
    account.ensure_private_home(tmp_path)
    assert home.stat().st_mode & 0o777 == 0o700
    runtime = backend.runtime_options(config, {})
    assert runtime.environment["CODEX_HOME"] == str(home)


def test_private_home_is_not_provisioned_without_saved_key(tmp_path):
    account.ensure_private_home(tmp_path)
    assert not (tmp_path / "providers" / "deepseek").exists()


def test_symlinked_state_root_provisions_and_anchors_private_home(tmp_path, monkeypatch):
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    (real / "deepseek.key").write_text("fixture-deepseek-secret-123")
    linked = tmp_path / "link"
    linked.symlink_to(real, target_is_directory=True)
    monkeypatch.setattr("adapters.deepseek.credentials.keyring_entry_exists", lambda *_: False)
    account.ensure_private_home(linked)
    home = real / "providers" / "deepseek"
    assert home.stat().st_mode & 0o777 == 0o700
    config = {
        "binary": "codex",
        "api_provider": {"url": "http://127.0.0.1:9", "key_file": str(linked / "deepseek.key")},
    }
    runtime = backend.runtime_options(config, {})
    assert runtime.environment["CODEX_HOME"] == str(home.resolve())
    assert runtime.environment["KEEPHARNESS_API_KEY"] == "fixture-deepseek-secret-123"


def test_set_api_key_on_symlinked_state_root_stores_in_real_home(tmp_path):
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    linked = tmp_path / "link"
    linked.symlink_to(real, target_is_directory=True)
    DeepSeekStateAdapter(linked).set_api_key(SecretStr("fixture-linked-key-123456"))
    assert account.key_file(real).read_text() == "fixture-linked-key-123456"
    assert (real / "providers" / "deepseek").stat().st_mode & 0o777 == 0o700


def _fake_codex_on_path(monkeypatch):
    fake = Path(__file__).parent / "fixtures" / "fake-codex"
    monkeypatch.setenv("PATH", f"{fake}{os.pathsep}{os.environ['PATH']}")


def test_listing_through_symlinked_parent_state_root_succeeds(tmp_path, monkeypatch):
    _fake_codex_on_path(monkeypatch)
    real = tmp_path / "real"
    home = real / "providers" / "deepseek"
    home.mkdir(mode=0o700, parents=True)
    (home / "fake-app-server.json").write_text(
        json.dumps({"plugins": [{"id": "private", "name": "Private"}]})
    )
    linked = tmp_path / "link"
    linked.symlink_to(real, target_is_directory=True)
    snapshot = DeepSeekStateAdapter(linked).read_state(None)
    assert any(item.id == "plugin:private" for item in snapshot.items)


@pytest.mark.parametrize("via_link", [False, True])
def test_symlink_planted_under_state_is_refused(tmp_path, monkeypatch, via_link):
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    (real / "deepseek.key").write_text("fixture-deepseek-secret-123")
    (real / "providers").mkdir(mode=0o700)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir(mode=0o700)
    (real / "providers" / "deepseek").symlink_to(elsewhere, target_is_directory=True)
    state = tmp_path / "link"
    if via_link:
        state.symlink_to(real, target_is_directory=True)
    else:
        state = real
    monkeypatch.setattr("adapters.deepseek.credentials.keyring_entry_exists", lambda *_: False)
    with pytest.raises(ToolError, match="^unsafe_scoped_home$"):
        account.ensure_private_home(state)
    config = {
        "binary": "codex",
        "api_provider": {"url": "http://127.0.0.1:9", "key_file": str(state / "deepseek.key")},
    }
    with pytest.raises(ToolError, match="^deepseek_credential_isolation$"):
        backend.runtime_options(config, {})
    assert list(elsewhere.iterdir()) == []


@pytest.mark.parametrize("via_link", [False, True])
def test_listing_refuses_symlink_planted_under_state_root(tmp_path, monkeypatch, via_link):
    _fake_codex_on_path(monkeypatch)
    real = tmp_path / "real"
    (real / "providers").mkdir(mode=0o700, parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir(mode=0o700)
    (real / "providers" / "deepseek").symlink_to(elsewhere, target_is_directory=True)
    state = tmp_path / "link"
    if via_link:
        state.symlink_to(real, target_is_directory=True)
    else:
        state = real
    with pytest.raises(ProviderStateSchemaError, match="must not contain symlinks"):
        DeepSeekStateAdapter(state).read_state(None)
    assert list(elsewhere.iterdir()) == []
