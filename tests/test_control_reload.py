import asyncio
import json
from types import SimpleNamespace

import pytest

from control.server import Manager


def test_apply_settings_writes_atomic_runtime_and_preserves_client_hashes(tmp_path):
    manager = Manager(tmp_path)
    manager.proc = SimpleNamespace(returncode=None)
    manager.inventory = {"network": {"hostname": None}, "services": [], "binaries": {}}
    runtime = {
        "state_dir": str(tmp_path / "runs"),
        "bind": "127.0.0.1",
        "port": 8095,
        "clients": {"local": {"sha256": "preserve-me", "projects": ["sem-projeto"]}},
        "services": {},
        "projects": {"sem-projeto": {}},
        "origins": [],
    }
    (tmp_path / "runtime.json").write_text(json.dumps(runtime))

    asyncio.run(manager.apply_settings(manager.settings))

    written = json.loads((tmp_path / "runtime.json").read_text())
    assert written["clients"]["local"]["sha256"] == "preserve-me"
    assert written["config_revision"]
    assert not (tmp_path / "runtime.tmp").exists()


def test_running_harness_rejects_bind_or_port_change_but_allows_empty_services(
    tmp_path,
):
    manager = Manager(tmp_path)
    manager.proc = SimpleNamespace(returncode=None)
    manager.inventory = {"network": {"hostname": None}, "services": [], "binaries": {}}
    (tmp_path / "runtime.json").write_text(
        json.dumps(
            {
                "state_dir": str(tmp_path / "runs"),
                "bind": "127.0.0.1",
                "port": 8095,
                "clients": {},
                "services": {},
                "projects": {"sem-projeto": {}},
                "origins": [],
            }
        )
    )
    changed = {**manager.settings, "port": 8096}
    with pytest.raises(ValueError, match="reinicie"):
        asyncio.run(manager.apply_settings(changed))
    asyncio.run(manager.apply_settings(manager.settings))
    assert (
        json.loads((tmp_path / "runtime.json").read_text())["services"]["codex"][
            "enabled"
        ]
        is False
    )


def test_first_enabled_provider_starts_harness_without_manual_button(tmp_path):
    from unittest.mock import AsyncMock
    manager = Manager(tmp_path)
    manager.start = AsyncMock()
    asyncio.run(manager.apply_settings(manager.settings))
    manager.start.assert_not_awaited()
    settings = json.loads(json.dumps(manager.settings))
    settings['services']['claude'].update(enabled=True, models=['sonnet'])
    asyncio.run(manager.apply_settings(settings))
    manager.start.assert_awaited_once()


@pytest.mark.parametrize('running', [False, True])
def test_settings_can_remove_an_integration_that_is_no_longer_installed(tmp_path, running):
    import copy
    manager = Manager(tmp_path)
    manager.inventory = {'network':{}, 'services':[], 'binaries':{}}
    manager.plugin_catalog = []
    manager.settings['services']['codex']['integrations'] = ['plugin:removed@market']
    manager.proc = SimpleNamespace(returncode=None) if running else None
    repaired = copy.deepcopy(manager.settings)
    repaired['services']['codex']['integrations'] = []
    asyncio.run(manager.apply_settings(repaired))
    assert manager.settings['services']['codex']['integrations'] == []
