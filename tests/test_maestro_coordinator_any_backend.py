import pytest
from test_workspaces import config

from agent_service import maestro
from agent_service.tools import ToolError


def test_default_stays_codex_and_explicit_local_is_supported(tmp_path):
    cfg = config(tmp_path)
    assert maestro.coordinator(cfg, "p")["backend"] == "codex"
    cfg["maestro_coordinator"] = {"backend": "local", "model": "installed-model"}
    assert maestro.coordinator(cfg, "p")["backend"] == "local"


def test_coordinator_never_falls_back_from_denied_explicit_choice(tmp_path):
    cfg = config(tmp_path)
    cfg["maestro_coordinator"] = {"backend": "local", "model": "missing"}
    with pytest.raises(ToolError, match="maestro_coordinator_unavailable"):
        maestro.coordinator(cfg, "p")


def test_default_unavailable_preserves_legacy_error(tmp_path):
    cfg = config(tmp_path)
    cfg["services"]["codex"]["enabled"] = False
    with pytest.raises(ToolError, match="maestro_requires_enabled_codex_for_project"):
        maestro.coordinator(cfg, "p")
