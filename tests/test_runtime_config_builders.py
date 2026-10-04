"""Unit checks for the pure runtime-config builders behind Manager.build_runtime_config."""

import sys

import pytest

from control import runtime_config


def cli_info(tmp_path):
    auth = tmp_path / "auth.json"
    auth.write_text("{}")
    return {"binary": sys.executable, "auth_file": str(auth)}


def test_builders_dispatch_and_default_to_the_cli_builder():
    assert runtime_config.BUILDERS["deepseek"] is runtime_config.build_deepseek
    assert runtime_config.BUILDERS["local"] is runtime_config.build_local
    for provider in ("codex", "claude", "gemini"):
        assert provider not in runtime_config.BUILDERS
    with pytest.raises(TypeError):
        runtime_config.BUILDERS["codex"] = runtime_config.build_cli_provider


def test_gemini_catalog_is_checked_and_models_listed(tmp_path):
    cfg = {"services": {}}
    spec = {"enabled": True, "models": ["gemini-pro"], "mode": "native"}
    info = cli_info(tmp_path)
    with pytest.raises(ValueError, match="catalog"):
        runtime_config.build_cli_provider(
            cfg, "gemini", spec, {"authenticated": True, "models": {}}, info, tmp_path
        )
    checked = {"authenticated": True, "models": {"gemini-pro": ["configured"]}}
    runtime_config.build_cli_provider(cfg, "gemini", spec, checked, info, tmp_path)
    assert cfg["gemini_models"] == ["gemini-pro"]
    assert cfg["gemini"]["python"] == sys.executable


def test_pending_native_claude_login_does_not_block_the_config(tmp_path):
    cfg = {}
    spec = {"enabled": True, "models": ["opus"], "mode": "native"}
    checked = {"authenticated": False, "models": {}}
    runtime_config.build_cli_provider(cfg, "claude", spec, checked, cli_info(tmp_path), tmp_path)
    assert cfg["claude_models"] == {"opus": ["configured"]}
    with pytest.raises(ValueError, match="Log in to codex"):
        runtime_config.build_cli_provider(
            {}, "codex", {**spec, "models": []}, checked, cli_info(tmp_path), tmp_path
        )


def test_mcp_defaults_and_unrestricted_marking():
    with pytest.raises(ValueError, match="MCP effort"):
        runtime_config.check_mcp_defaults(
            {
                "mcp_defaults": {"backend": "codex", "model": "m", "effort": "max"},
                "codex_models": {"m": ["low"]},
            }
        )
    calls = []

    def integrations():
        calls.append(True)
        return {"codex": [{"id": "plugin:x", "kind": "plugin"}, {"id": "mcp:y", "kind": "mcp"}]}

    cfg = {"claude": {}}
    runtime_config.mark_unrestricted(cfg, integrations)
    assert cfg["claude"]["unrestricted"] is True and not calls
    cfg = {"codex": {}}
    runtime_config.mark_unrestricted(cfg, integrations)
    assert cfg["codex"]["plugin_inventory"] == ["plugin:x"]


def test_clients_reuse_hashes_and_create_the_vpn_key_once(tmp_path):
    cfg = {"projects": {"sem-projeto": {}}, "clients": {}}
    settings = {"logins": ["person@example.com"]}
    previous = {"clients": {"vpn": {"sha256": "v"}, "local": {"sha256": "l"}}}
    runtime_config.build_clients(cfg, settings, tmp_path, previous)
    key = (tmp_path / "vpn.key").read_text()
    assert (tmp_path / "vpn.key").stat().st_mode & 0o777 == 0o600
    assert cfg["clients"]["vpn"] == {"sha256": "v", "projects": ["sem-projeto"]}
    assert cfg["clients"]["local"]["sha256"] == "l"
    assert list(cfg["tailscale_logins"]) == ["person@example.com"]
    runtime_config.build_clients(cfg, settings, tmp_path, {})
    assert (tmp_path / "vpn.key").read_text() == key


def test_non_local_clients_start_with_sem_projeto(tmp_path):
    cfg = {"projects": {"sem-projeto": {}, "registered": {}}, "clients": {}}
    settings = {"logins": ["person@example.com"]}
    runtime_config.build_clients(cfg, settings, tmp_path, {})
    assert cfg["clients"]["local"]["projects"] == ["sem-projeto", "registered"]
    others = {
        name: client["projects"] for name, client in cfg["clients"].items() if name != "local"
    }
    assert set(others) == {"vpn", cfg["tailscale_logins"]["person@example.com"]}
    assert all(projects == ["sem-projeto"] for projects in others.values())


def test_origins_include_the_tailnet_host_only_when_known():
    settings = {"port": 8095, "tailnet_port": 8096}
    cfg = {}
    runtime_config.build_origins(cfg, settings, {"network": {}})
    assert cfg["origins"] == [
        "http://127.0.0.1:8095",
        "http://127.0.0.1:8095",
        "http://localhost:8095",
    ]
    runtime_config.build_origins(cfg, settings, {"network": {"hostname": "host"}})
    assert cfg["origins"][-1] == "http://host:8096"


def codex_route(retired, current):
    """A config and spec for a codex service that still lists ``retired`` and ``current``."""
    models = [retired, current]
    cfg = {"services": {"codex": {"enabled": True, "models": list(models)}}}
    return cfg, {"enabled": True, "models": models, "mode": "native"}


def test_a_retired_codex_model_quarantines_only_its_route(tmp_path):
    cfg, spec = codex_route("retired-model", "current-model")
    checked = {"authenticated": True, "models": {"current-model": ["low"]}}
    runtime_config.build_cli_provider(cfg, "codex", spec, checked, cli_info(tmp_path), tmp_path)
    assert cfg["codex_models"] == {"current-model": ["low"]}
    assert cfg["services"]["codex"]["models"] == ["current-model"]
    assert cfg["unavailable_models"] == {
        "codex": {"retired-model": runtime_config.CATALOG_MISSING}
    }
    # The saved selection stays for the admin to repair; only this run's routes change.
    assert spec["models"] == ["retired-model", "current-model"]


def test_a_codex_catalog_without_losses_adds_no_quarantine(tmp_path):
    cfg, spec = codex_route("old-model", "current-model")
    models = {"old-model": ["low"], "current-model": ["low"]}
    runtime_config.build_cli_provider(
        cfg, "codex", spec, {"authenticated": True, "models": models}, cli_info(tmp_path), tmp_path
    )
    assert "unavailable_models" not in cfg
    assert cfg["services"]["codex"]["models"] == ["old-model", "current-model"]


def test_the_mcp_default_on_a_retired_model_does_not_stop_the_other_routes():
    cfg = {
        "mcp_defaults": {"backend": "codex", "model": "retired-model", "effort": "low"},
        "codex_models": {"current-model": ["low"]},
        "unavailable_models": {"codex": {"retired-model": runtime_config.CATALOG_MISSING}},
    }
    runtime_config.check_mcp_defaults(cfg)
    cfg["mcp_defaults"]["model"] = "other-model"
    with pytest.raises(ValueError, match="MCP effort"):
        runtime_config.check_mcp_defaults(cfg)
