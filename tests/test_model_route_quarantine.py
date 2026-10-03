"""A retired catalog model takes only its own route offline; the other providers still start."""

import asyncio
import copy
import sys
from unittest.mock import AsyncMock, patch

from control import runtime_config
from control.server import Manager

CATALOGS = {
    "codex": {"authenticated": True, "models": {"current-model": ["low", "high"]}},
    "claude": {"authenticated": True, "models": {}},
}


def configured_manager(tmp_path, codex_models):
    manager = Manager(tmp_path / "control")
    manager.admin_port = 8094
    manager.inventory = {
        "network": {},
        "services": [
            {
                "id": provider,
                "found": True,
                "binary": sys.executable,
                "auth_file": str(tmp_path / f"{provider}.json"),
            }
            for provider in CATALOGS
        ],
    }
    settings = copy.deepcopy(manager.settings)
    settings["services"]["codex"].update(enabled=True, models=codex_models)
    settings["services"]["claude"].update(enabled=True, models=["claude-opus-4-6"])
    return manager, settings


def build(manager, settings):
    async def check(provider):
        return CATALOGS[provider]

    with (
        patch.object(manager, "check", AsyncMock(side_effect=check)),
        patch.object(manager, "integrations", lambda: {}),
    ):
        return asyncio.run(manager.build_runtime_config(settings))


def test_one_retired_codex_model_does_not_stop_the_other_providers(tmp_path):
    manager, settings = configured_manager(tmp_path, ["retired-model", "current-model"])
    cfg = build(manager, settings)
    assert cfg["claude_models"] == {"claude-opus-4-6": ["configured"]}
    assert cfg["services"]["claude"]["models"] == ["claude-opus-4-6"]
    assert cfg["services"]["codex"]["models"] == ["current-model"]
    assert cfg["codex_models"] == {"current-model": ["low", "high"]}
    assert cfg["unavailable_models"] == {
        "codex": {"retired-model": runtime_config.CATALOG_MISSING}
    }
    assert settings["services"]["codex"]["models"] == ["retired-model", "current-model"]


def test_every_codex_model_retired_leaves_codex_without_routes_not_the_harness(tmp_path):
    manager, settings = configured_manager(tmp_path, ["retired-model"])
    cfg = build(manager, settings)
    assert cfg["services"]["codex"]["models"] == [] and cfg["codex_models"] == {}
    assert cfg["services"]["claude"]["models"] == ["claude-opus-4-6"]
    assert cfg["unavailable_models"]["codex"] == {"retired-model": runtime_config.CATALOG_MISSING}
