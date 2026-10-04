"""A provider that is not signed in (or has no CLI) is quarantined; the harness still starts."""

import asyncio
import copy
import sys
from unittest.mock import AsyncMock, patch

from adapters.shared.provider_setup import homes_root
from agent_service.config import validate_runtime_config
from control import runtime_config
from control.server import Manager

# Stand-ins for the CLIs: ``login status`` / ``auth status --json`` find no login in a new home.
SIGNED_OUT_CLI = '#!/bin/sh\necho \'{"loggedIn": false}\'\nexit 1\n'


def signed_out_cli(tmp_path, name):
    binary = tmp_path / name
    binary.write_text(SIGNED_OUT_CLI)
    binary.chmod(0o700)
    return str(binary)


def manager_with(tmp_path, *, found=True):
    manager = Manager(tmp_path / "control")
    manager.admin_port = 8094
    services = [
        {
            "id": provider,
            "found": found,
            "binary": signed_out_cli(tmp_path, provider) if found else None,
            "auth_file": str(tmp_path / f"{provider}.json"),
        }
        for provider in ("codex", "claude")
    ]
    services.append(
        {"id": "deepseek", "found": True, "binary": sys.executable, "auth_file": ""}
    )
    manager.inventory = {"network": {}, "services": services}
    settings = copy.deepcopy(manager.settings)
    settings["services"]["codex"].update(enabled=True, models=["gpt-5.5"])
    settings["services"]["claude"].update(enabled=True, models=["claude-opus-4-6"])
    settings["services"]["deepseek"].update(enabled=True, models=["deepseek-chat"])
    return manager, settings


def build(manager, settings):
    deepseek = {"authenticated": True, "models": {"deepseek-chat": ["configured"]}}
    with (
        patch("control.manager.deepseek.check", AsyncMock(return_value=deepseek)),
        patch.object(manager, "integrations", lambda: {}),
    ):
        return asyncio.run(manager.build_runtime_config(settings))


def test_signed_out_providers_are_quarantined_and_the_harness_config_builds(tmp_path):
    manager, settings = manager_with(tmp_path)
    assert not any(homes_root(manager.state).glob("home/.*/*"))  # the new homes are empty
    cfg = build(manager, settings)
    assert validate_runtime_config(cfg)
    assert cfg["unavailable_models"] == {"codex": {"gpt-5.5": runtime_config.SIGN_IN_REQUIRED}}
    assert cfg["services"]["codex"]["models"] == [] and "codex_models" not in cfg
    # A pending Claude login keeps its own, older rule: it never blocked the start.
    assert cfg["services"]["claude"]["models"] == ["claude-opus-4-6"]
    assert cfg["services"]["deepseek"]["models"] == ["deepseek-chat"]
    assert cfg["deepseek_models"] == {"deepseek-chat": ["configured"]}
    assert "deepseek" not in cfg["unavailable_models"]
    assert settings["services"]["codex"]["models"] == ["gpt-5.5"]  # the saved choice is kept


def test_a_missing_cli_is_quarantined_with_its_own_reason(tmp_path):
    manager, settings = manager_with(tmp_path, found=False)
    settings["services"]["claude"]["enabled"] = False  # Claude's own check still refuses
    cfg = build(manager, settings)
    assert cfg["unavailable_models"]["codex"] == {"gpt-5.5": runtime_config.CLI_MISSING}
    assert cfg["services"]["deepseek"]["models"] == ["deepseek-chat"]


def test_a_signed_in_provider_is_not_quarantined(tmp_path):
    manager, settings = manager_with(tmp_path)
    for provider in ("claude", "deepseek"):
        settings["services"][provider]["enabled"] = False
    signed_in = {"authenticated": True, "models": {"gpt-5.5": ["low"]}}
    with patch.object(manager, "check", AsyncMock(return_value=signed_in)):
        cfg = build(manager, settings)
    assert "codex" not in cfg.get("unavailable_models", {})
    assert cfg["codex_models"] == {"gpt-5.5": ["low"]}


def test_the_admin_status_names_the_reason_after_the_runtime_is_written(tmp_path):
    manager, settings = manager_with(tmp_path)
    assert manager.status()["unavailable_models"] == {}
    manager._write_runtime(build(manager, settings))
    reasons = manager.status()["unavailable_models"]
    assert reasons["codex"] == {"gpt-5.5": runtime_config.SIGN_IN_REQUIRED}
    assert runtime_config.SIGN_IN_REQUIRED.startswith("Sign in required")

