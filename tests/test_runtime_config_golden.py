"""Golden characterization test for ``Manager.build_runtime_config`` (module-split commit 0).

See ``dossier/design`` (module-split refactor) section 3g: the runtime-builder split
keeps ``build_runtime_config`` as the async orchestrator, and this golden test is the
guard that later commit is measured against. It builds one representative settings
dict covering native codex, native claude, local (model-permission gated), deepseek
BYOK and one disabled service (gemini), plus one registered project, uploads and
tailnet fields, and diffs the exact resulting shape against an inline expected dict.
Non-deterministic bits (the random ``config_revision`` uuid, the interpreter path,
and paths under ``tmp_path``) are normalised before the comparison.

Note: ``Manager.validate`` hardcodes ``mode = "native"`` for every provider today
(see ``control/server.py``), so a "scoped" service is not observable through
``build_runtime_config`` right now; the "local" case below stands in for it and
exercises the model-permission-gated branch instead.
"""

import asyncio
import copy
import hashlib
import json
import re
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

from control import local_access
from control.server import Manager

PYTHON = sys.executable
PYTHON_RESOLVED = str(Path(sys.executable).resolve())


def _scrub(value, replacements):
    """Recursively replace non-deterministic substrings with stable placeholders."""
    if isinstance(value, dict):
        return {key: _scrub(item, replacements) for key, item in value.items()}
    if isinstance(value, list):
        return [_scrub(item, replacements) for item in value]
    if isinstance(value, str):
        for old, new in replacements:
            if old:
                value = value.replace(old, new)
        return value
    return value


def _build_manager(tmp_path):
    state = tmp_path / "control"
    project_root = tmp_path / "proj1"
    project_root.mkdir(parents=True)
    manager = Manager(state)
    manager.admin_port = 8094
    manager.inventory = {
        "network": {"hostname": "example-host"},
        "services": [
            {
                "id": "codex",
                "found": True,
                "binary": PYTHON,
                "auth_file": str(tmp_path / "codex-auth.json"),
            },
            {
                "id": "claude",
                "found": True,
                "binary": PYTHON,
                "auth_file": str(tmp_path / "claude-auth.json"),
            },
            {
                "id": "gemini",
                "found": True,
                "binary": PYTHON,
                "auth_file": str(tmp_path / "gemini-auth.json"),
            },
            {
                "id": "local",
                "found": True,
                "binary": PYTHON,
                "auth_file": str(tmp_path / "local-auth.json"),
                "runtimes": [{"id": "local-model"}],
            },
            {
                "id": "deepseek",
                "found": True,
                "binary": PYTHON,
                "auth_file": str(tmp_path / "deepseek-auth.json"),
            },
        ],
    }
    settings = copy.deepcopy(manager.settings)
    settings["port"] = 8095
    settings["tailnet_port"] = 8096
    settings["logins"] = ["person@example.com"]
    settings["projects"] = [{"id": "proj1", "label": "Project One", "root": str(project_root)}]
    settings["services"]["codex"].update(enabled=True, models=["gpt-5-codex"])
    settings["services"]["claude"].update(enabled=True, models=["claude-opus-4-6"])
    settings["services"]["local"].update(enabled=True, models=["local-model"])
    settings["services"]["deepseek"].update(enabled=True, models=["deepseek-chat"], added=True)
    # gemini is left disabled on purpose: the "one disabled service" case.

    client_id = "tailnet-" + hashlib.sha256(b"person@example.com").hexdigest()[:16]
    previous_runtime = {
        "clients": {
            "vpn": {"sha256": "0" * 64},
            "local": {"sha256": "1" * 64},
            client_id: {"sha256": "2" * 64},
        }
    }
    # Seeding a previous runtime.json makes the client hashes deterministic:
    # build_runtime_config only mints a fresh random hash when one is missing.
    (manager.state / "runtime.json").write_text(json.dumps(previous_runtime))
    (manager.state / "tailnet.json").write_text("{}")
    return manager, settings, client_id


async def _fake_check(provider):
    return {
        "codex": {"authenticated": True, "models": {"gpt-5-codex": ["low", "medium"]}},
        "claude": {"authenticated": True, "models": {}},
        "local": {"authenticated": True, "models": {"local-model": ["configured"]}},
        "deepseek": {"authenticated": True, "models": {"deepseek-chat": ["configured"]}},
    }[provider]


def test_build_runtime_config_matches_golden_shape(tmp_path):
    manager, settings, client_id = _build_manager(tmp_path)
    with (
        patch.object(manager, "check", AsyncMock(side_effect=_fake_check)),
        patch.object(manager, "integrations", lambda: {}),
        patch("control.server.shutil.which", return_value="/usr/bin/node"),
    ):
        cfg = asyncio.run(manager.build_runtime_config(settings))

    revision = cfg.pop("config_revision")
    assert re.fullmatch(r"[0-9a-f-]{36}", revision)
    # Only the digest of the per-install secret travels; the secret stays in its 0600 file.
    assert cfg.pop("local_secret_sha256") == local_access.digest(manager.local_secret)

    replacements = [
        (PYTHON_RESOLVED, "<PYTHON_RESOLVED>"),
        (PYTHON, "<PYTHON>"),
        (str(tmp_path.resolve()), "<TMP>"),
        (str(tmp_path), "<TMP>"),
    ]
    scrubbed = _scrub(cfg, replacements)

    all_projects = ["sem-projeto", "proj1"]
    full_permissions = {
        "read": True,
        "write": True,
        "upload": True,
        "tests": True,
        "internet": True,
        "shell": True,
        "hooks": True,
    }
    no_permissions = dict.fromkeys(full_permissions, False)

    expected = {
        "browser_url": "http://example-host:8096/",
        "state_dir": "<TMP>/control/runs",
        "sessions_dir": "<TMP>/control-sessions",
        "catalogs": [],
        "projects": {
            "sem-projeto": {"label": "No project"},
            "proj1": {
                "id": "proj1",
                "label": "Project One",
                "root": "<TMP>/proj1",
                "service_units": [],
                "permissions": {},
                "catalogs": [],
                "test_commands": {},
                "additional_roots": [],
                "node_binary": "/usr/bin/node",
            },
        },
        "clients": {
            "vpn": {"sha256": "0" * 64, "projects": all_projects},
            "local": {"sha256": "1" * 64, "projects": all_projects},
            client_id: {"sha256": "2" * 64, "projects": all_projects},
        },
        "services": {
            "codex": {
                "added": True,
                "mode": "native",
                "integrations": [],
                "enabled": True,
                "models": ["gpt-5-codex"],
                "projects": all_projects,
                "permissions": full_permissions,
            },
            "claude": {
                "added": True,
                "mode": "native",
                "integrations": [],
                "enabled": True,
                "models": ["claude-opus-4-6"],
                "projects": all_projects,
                "permissions": full_permissions,
            },
            "gemini": {
                "added": False,
                "mode": "native",
                "integrations": [],
                "enabled": False,
                "models": [],
                "projects": all_projects,
                "permissions": full_permissions,
            },
            "local": {
                "added": True,
                "mode": "native",
                "integrations": [],
                "enabled": True,
                "models": ["local-model"],
                "projects": all_projects,
                "permissions": no_permissions,
                "model_permissions": {"local-model": no_permissions},
            },
            "deepseek": {
                "added": True,
                "mode": "native",
                "integrations": [],
                "enabled": True,
                "models": ["deepseek-chat"],
                "projects": all_projects,
                "permissions": full_permissions,
            },
        },
        "origins": [
            "http://127.0.0.1:8095",
            "http://127.0.0.1:8095",
            "http://localhost:8095",
            "http://example-host:8096",
        ],
        "uploads_enabled": True,
        "mcp_defaults": {},
        "default_backend": "",
        "maestro_enabled": True,
        "maestro_instructions": "",
        "maestro_coordinator": {},
        "project_registration": True,
        "shared_projects": False,
        "control_state_dir": "<TMP>/control",
        "personal_setup": False,
        "full_access": False,
        "admin_url": "http://127.0.0.1:8094/",
        "local_access": True,
        "bind": "127.0.0.1",
        "port": 8095,
        "provider_revisions": {},
        "codex": {
            "binary": "<PYTHON_RESOLVED>",
            "auth_file": "<TMP>/control/providers/home/.codex/auth.json",
            "python": "<PYTHON>",
            "integrations": [],
            "provider_homes": "<TMP>/control/providers",
            "unrestricted": True,
            "plugin_inventory": [],
        },
        "codex_models": {"gpt-5-codex": ["low", "medium"]},
        "claude": {
            "binary": "<PYTHON_RESOLVED>",
            "auth_file": "<TMP>/control/providers/home/.claude/.credentials.json",
            "python": "<PYTHON>",
            "integrations": [],
            "provider_homes": "<TMP>/control/providers",
            "unrestricted": True,
        },
        "claude_models": {"claude-opus-4-6": ["configured"]},
        "local": {
            "binary": "<PYTHON_RESOLVED>",
            "auth_file": "<TMP>/local-auth.json",
            "python": "<PYTHON>",
            "integrations": [],
            "local_provider": "ollama",
            "local_models": {"local-model": {"id": "local-model"}},
            "model_roots": {"local-model": []},
        },
        "local_models": ["local-model"],
        "deepseek": {
            "binary": "<PYTHON>",
            "api_provider": {
                "url": "https://api.deepseek.com",
                "key_file": "<TMP>/control/deepseek.key",
            },
            "integrations": [],
            "provider_homes": "<TMP>/control/providers",
            "unrestricted": True,
            "plugin_inventory": [],
        },
        "deepseek_models": {"deepseek-chat": ["configured"]},
        "tailscale_logins": {"person@example.com": client_id},
    }
    assert scrubbed == expected
