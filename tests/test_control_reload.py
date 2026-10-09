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
    with pytest.raises(ValueError, match="restart the harness"):
        asyncio.run(manager.apply_settings(changed))
    asyncio.run(manager.apply_settings(manager.settings))
    assert (
        json.loads((tmp_path / "runtime.json").read_text())["services"]["codex"]["enabled"] is False
    )


def test_first_enabled_provider_starts_harness_without_manual_button(tmp_path):
    from unittest.mock import AsyncMock

    manager = Manager(tmp_path)
    manager.start = AsyncMock()
    asyncio.run(manager.apply_settings(manager.settings))
    manager.start.assert_not_awaited()
    settings = json.loads(json.dumps(manager.settings))
    settings["services"]["claude"].update(enabled=True, models=["claude-sonnet-4-6"])
    asyncio.run(manager.apply_settings(settings))
    manager.start.assert_awaited_once()


def old_state_with_renamed_integration(tmp_path):
    """A 0.15 settings file whose allow list names an integration that was renamed since."""
    settings = json.loads(json.dumps(Manager(tmp_path).settings))
    settings["personal_setup"] = True
    codex = settings["services"]["codex"]
    codex.update(enabled=True, models=["gpt-6-astra"], integrations=["mcp:sophia-engine"])
    settings["services"]["claude"]["integrations"] = ["plugin:gone@market"]
    (tmp_path / "settings.json").write_text(json.dumps(settings))
    return settings


def test_old_state_with_integrations_and_personal_setup_loads_without_them(tmp_path):
    old_state_with_renamed_integration(tmp_path)

    manager = Manager(tmp_path)

    assert "personal_setup" not in manager.settings
    assert not any("integrations" in spec for spec in manager.settings["services"].values())
    assert manager.settings["services"]["codex"]["enabled"] is True
    saved = manager.validate(manager.settings)
    assert "personal_setup" not in saved
    assert not any("integrations" in spec for spec in saved["services"].values())


@pytest.mark.parametrize("running", [False, True])
def test_harness_starts_on_a_state_where_an_integration_was_renamed(tmp_path, running):
    from unittest.mock import AsyncMock

    old_state_with_renamed_integration(tmp_path)
    manager = Manager(tmp_path)
    manager.inventory = {
        "network": {},
        "services": [{"id": "codex", "found": True, "binary": "codex"}],
        "binaries": {},
    }
    manager.plugin_catalog = []
    manager.check = AsyncMock(return_value={"authenticated": True, "models": {}})
    manager.start = AsyncMock()
    manager.proc = SimpleNamespace(returncode=None) if running else None
    # The renamed id comes back from the admin or an import; it must not be validated or kept.
    incoming = json.loads(json.dumps(manager.settings))
    incoming["personal_setup"] = True
    incoming["services"]["codex"]["integrations"] = ["mcp:ask-sophia-now", "mcp:sophia-engine"]

    asyncio.run(manager.apply_settings(incoming))

    on_disk = json.loads((tmp_path / "settings.json").read_text())
    assert "personal_setup" not in on_disk
    assert not any("integrations" in spec for spec in on_disk["services"].values())
    if not running:
        manager.start.assert_awaited_once()
