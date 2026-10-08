"""control.env: KEEPHARNESS_<NAME> wins; legacy aliases work with one deprecation warning."""

import asyncio
import json
import logging
import os
from pathlib import Path

import pytest

from control import env


def test_new_name_wins_when_both_are_set(monkeypatch):
    monkeypatch.setenv("KEEPHARNESS_AGENT_URL", "https://new")
    monkeypatch.setenv("LOCAL_AGENT_URL", "https://legacy")
    assert env.read("AGENT_URL") == "https://new"


def test_legacy_alias_is_used_with_a_deprecation_warning(monkeypatch):
    monkeypatch.delenv("KEEPHARNESS_AGENT_URL", raising=False)
    monkeypatch.setenv("LOCAL_AGENT_URL", "https://legacy")
    env._WARNED.discard("AGENT_URL")
    with pytest.warns(DeprecationWarning, match="LOCAL_AGENT_URL"):
        assert env.read("AGENT_URL") == "https://legacy"


def test_th_venv_is_the_legacy_alias_for_keepharness_venv(monkeypatch):
    monkeypatch.delenv("KEEPHARNESS_VENV", raising=False)
    monkeypatch.setenv("TH_VENV", "/opt/venv")
    env._WARNED.discard("VENV")
    with pytest.warns(DeprecationWarning, match="TH_VENV"):
        assert env.read("VENV") == "/opt/venv"


def test_default_is_returned_when_neither_name_is_set(monkeypatch):
    monkeypatch.delenv("KEEPHARNESS_AGENT_CLIENT", raising=False)
    monkeypatch.delenv("LOCAL_AGENT_CLIENT", raising=False)
    assert env.read("AGENT_CLIENT", "fallback") == "fallback"
    assert env.read("AGENT_CLIENT") is None


def test_unmapped_name_has_no_legacy_fallback(monkeypatch):
    monkeypatch.delenv("KEEPHARNESS_LOG_LEVEL", raising=False)
    assert env.read("LOG_LEVEL", "info") == "info"


def test_each_tail_harness_variable_left_set_is_named_once_at_startup(monkeypatch, caplog):
    for name in [name for name in os.environ if name.startswith("TAIL_HARNESS_")]:
        monkeypatch.delenv(name)
    monkeypatch.setenv("TAIL_HARNESS_LOG_LEVEL", "debug")
    monkeypatch.setenv("TAIL_HARNESS_AGENT_URL", "https://private.example")
    with caplog.at_level(logging.WARNING, logger="control.env"):
        env.warn_legacy_names()
    assert [record.getMessage() for record in caplog.records] == [
        "TAIL_HARNESS_AGENT_URL is no longer read; rename it to KEEPHARNESS_AGENT_URL.",
        "TAIL_HARNESS_LOG_LEVEL is no longer read; rename it to KEEPHARNESS_LOG_LEVEL.",
    ]
    assert "private.example" not in caplog.text


# Provider children (L04, SEC RC-06 and RC-09; decisions D01, D02).


def test_child_environment_allowlist(monkeypatch):
    from adapters.shared.process import child_environment

    listed = {
        "PATH": "/usr/bin",
        "HOME": "/home/fixture",
        "LANG": "en_US.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
        "HTTPS_PROXY": "http://proxy.invalid:3128",
        "SSL_CERT_FILE": "/etc/ssl/cert.pem",
    }
    unlisted = {
        "CLAUDE_CODE_OAUTH_TOKEN": "host-login",
        "CLAUDE_CODE_ENTRYPOINT": "cli",
        "ANTHROPIC_BASE_URL": "https://relay.invalid",
        "OPENAI_API_KEY": "host-key",
        "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
        "SSH_AUTH_SOCK": "/run/user/1000/ssh",
        "GIT_ASKPASS": "/usr/bin/askpass",
        "KEEPHARNESS_AGENT_CONFIG": "/secret/runtime.json",
        "SOME_SESSION_VARIABLE": "kept-by-a-denylist",
    }
    for name in list(os.environ):
        monkeypatch.delenv(name)
    for name, value in {**listed, **unlisted}.items():
        monkeypatch.setenv(name, value)
    assert child_environment() == listed
    homes = {"HOME": "/state/providers/home", "CODEX_HOME": "/state/providers/home/.codex"}
    assert child_environment({**os.environ, **homes}) == {**listed, **homes}


def test_provider_home_is_harness_owned(tmp_path, monkeypatch):
    from adapters.codex.native import build_command
    from adapters.shared import provider_setup
    from control.runtime_config import build_cli_provider, build_deepseek

    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    state = tmp_path / "state"
    state.mkdir()
    cfg = {"services": {}}
    for provider in ("codex", "claude"):
        build_cli_provider(
            cfg,
            provider,
            {"models": []},
            {"authenticated": True, "models": {}},
            {"binary": "/usr/bin/true", "auth_file": str(fake_home / "host-auth.json")},
            state,
        )
    build_deepseek(
        cfg, "deepseek", {"models": []}, {"models": {}}, {"binary": "/usr/bin/true"}, state
    )
    homes = {}
    for provider in ("codex", "claude", "deepseek"):
        assert cfg[provider]["provider_homes"] == str(state / "providers")
        homes[provider] = provider_setup.environment(cfg[provider], provider)
        for path in homes[provider].values():
            assert Path(path).is_relative_to(state / "providers")
            assert Path(path).stat().st_mode & 0o077 == 0
    assert set(homes["codex"]) == {"HOME", "CODEX_HOME"}
    assert set(homes["deepseek"]) == {"HOME", "CODEX_HOME"}
    assert set(homes["claude"]) == {"HOME", "CLAUDE_CONFIG_DIR"}
    # DeepSeek keeps its own Codex home: the ChatGPT login of the Codex home is not its own.
    assert homes["deepseek"]["CODEX_HOME"] != homes["codex"]["CODEX_HOME"]
    # Isolated runs copy the login the admin wrote into the harness home, not the terminal's.
    assert cfg["codex"]["auth_file"] == str(Path(homes["codex"]["CODEX_HOME"]) / "auth.json")
    assert cfg["claude"]["auth_file"] == str(
        Path(homes["claude"]["CLAUDE_CONFIG_DIR"]) / ".credentials.json"
    )
    # Hooks follow the hooks grant only for an owner who opted into the personal setup.
    granted = {"hooks": True}
    for personal, expected in ((False, "false"), (True, "true")):
        effective = provider_setup.command_permissions({"personal_setup": personal}, granted)
        assert "features.hooks=" + expected in build_command("codex", effective, host_config=False)
    assert provider_setup.command_permissions({"personal_setup": True}, {}) == {}
    assert list(fake_home.iterdir()) == []


def test_personal_setup_is_an_owner_opt_in_off_by_default(tmp_path):
    from adapters.shared.provider_setup import run_settings
    from control.runtime_config import base_config
    from control.server import Manager

    manager = Manager(tmp_path / "state")
    checked = manager.validate(manager.settings)
    assert checked["personal_setup"] is False
    assert manager.validate({**manager.settings, "personal_setup": True})["personal_setup"]
    with pytest.raises(ValueError, match="personal setup"):
        manager.validate({**manager.settings, "personal_setup": "yes"})
    # The Claude-only global_hooks key is replaced by the opt-in, not kept beside it.
    legacy = json.loads(json.dumps(manager.settings))
    legacy["services"]["claude"]["global_hooks"] = True
    assert "global_hooks" not in manager.validate(legacy)["services"]["claude"]
    runtime = base_config(checked, tmp_path / "state", 8094, "http://127.0.0.1:8095/", {})
    assert runtime["personal_setup"] is False
    on = {"personal_setup": True}

    def personal(config, data=None):
        return run_settings(config, "local", data=data or {})["personal_setup"]

    assert personal(on) is True
    assert personal(on, data={"schedule_id": "s1"}) is False
    assert personal({}) is False


def test_admin_sign_in_and_checks_use_the_harness_home(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock, patch

    from control.operations import Operations
    from control.routes import login_provider
    from control.server import Manager

    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "terminal-login")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "terminal-codex"))
    manager = Manager(tmp_path / "state")
    manager.inventory = {
        "binaries": {"codex": "/fixture/codex", "claude": "/fixture/claude"},
        "services": [
            {"id": "codex", "found": True, "binary": "/fixture/codex"},
            {"id": "claude", "found": True, "binary": "/fixture/claude"},
        ],
    }
    providers = tmp_path / "state" / "providers"
    launched = {}

    def launch(operations, args, timeout=300, **kwargs):
        launched[args[0]] = kwargs["env"]
        return {"id": args[0], "state": "running", "output": ""}

    with patch.object(Operations, "launch", launch):
        for provider in ("codex", "claude"):
            asyncio.run(login_provider(None, manager, {"provider": provider}))
    assert Path(launched["/fixture/codex"]["CODEX_HOME"]).is_relative_to(providers)
    assert Path(launched["/fixture/claude"]["CLAUDE_CONFIG_DIR"]).is_relative_to(providers)
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in launched["/fixture/claude"]

    with (
        patch("control.discovery.command", AsyncMock(return_value=(0, ""))) as command,
        patch("control.manager.metadata", AsyncMock(return_value={"data": []})) as listing,
    ):
        asyncio.run(manager.check("codex"))
    assert Path(command.call_args.kwargs["env"]["CODEX_HOME"]).is_relative_to(providers)
    assert Path(listing.call_args.kwargs["env"]["CODEX_HOME"]).is_relative_to(providers)
    with (
        patch(
            "control.discovery.command", AsyncMock(return_value=(0, '{"loggedIn":true}'))
        ) as command,
        patch(
            "adapters.claude.account.metadata",
            AsyncMock(return_value={"models": [{"value": "sonnet"}]}),
        ) as probe,
    ):
        asyncio.run(manager.check("claude"))
    assert Path(command.call_args.kwargs["env"]["CLAUDE_CONFIG_DIR"]).is_relative_to(providers)
    assert probe.call_args.args[0]["provider_homes"] == str(providers)
