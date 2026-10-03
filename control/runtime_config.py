"""Pure builders for the agent-service runtime config that ``Manager`` writes.

``Manager.build_runtime_config`` stays the async orchestrator: it checks each enabled
provider (``await manager.check``) and hands the result to the matching builder here.
"""

import hashlib
import json
import logging
import platform
import secrets
import shutil
import sys
import uuid
from pathlib import Path
from types import MappingProxyType

from adapters.deepseek import account as deepseek

from .local_models import runtime_permissions, runtime_roots

logger = logging.getLogger(__name__)

CATALOG_MISSING = "Model not returned by the current provider's catalog."


def base_config(settings, state, admin_port, browser_url, provider_revisions):
    cfg = {
        "browser_url": browser_url,
        "state_dir": str(state / "runs"),
        "projects": {"sem-projeto": {"label": "No project"}},
        "catalogs": json.loads(json.dumps(settings.get("catalogs", []))),
        "clients": {},
        "services": json.loads(json.dumps(settings["services"])),
        "origins": [],
        "uploads_enabled": settings["uploads_enabled"],
        "mcp_defaults": settings.get("mcp_defaults", {}),
        "default_backend": settings.get("default_backend", ""),
        "maestro_enabled": settings.get("maestro_enabled", True),
        "maestro_instructions": settings.get("maestro_instructions", ""),
        "maestro_coordinator": settings.get("maestro_coordinator", {}),
        "shared_projects": True,
        "control_state_dir": str(state),
        "admin_url": f"http://127.0.0.1:{admin_port}/",
        "local_access": settings.get("vpn_bind", "127.0.0.1") == "127.0.0.1",
        "bind": settings.get("vpn_bind", "127.0.0.1"),
        "port": settings["port"],
        "config_revision": str(uuid.uuid4()),
        "provider_revisions": provider_revisions,
    }
    for key in ("integrations", "integration_bindings", "effect_integrations", "secret_vault_revision"):
        if key in settings:
            cfg[key] = json.loads(json.dumps(settings[key]))
    if settings.get("integration_bindings"):
        cfg["secret_vault_path"] = str(state / "harness.secrets.json")
    for project in settings["projects"]:
        cfg["projects"][project["id"]] = {
            **project,
            "test_commands": {},
            "additional_roots": [],
            "node_binary": shutil.which("node") or "node",
        }
    return cfg


# ``platform.machine()`` to the Node ``process.arch`` used in npm platform package names.
NODE_ARCHITECTURES = MappingProxyType(
    {"x86_64": "x64", "amd64": "x64", "aarch64": "arm64", "arm64": "arm64"}
)


def native_binary(binary):
    """The native binary behind ``npm install -g @openai/codex``'s Node wrapper.

    Isolated mode mounts only the binary, so the ``bin/codex.js`` entry point cannot run
    there; the platform package inside the npm package ships the real executable.
    """
    binary = Path(binary).resolve()
    if binary.parts[-4:] != ("@openai", "codex", "bin", "codex.js"):
        return binary
    machine = NODE_ARCHITECTURES.get(platform.machine().lower())
    name = f"codex-{platform.system().lower()}-{machine}"
    for candidate in sorted(
        binary.parents[1].glob(f"node_modules/@openai/{name}/vendor/*/bin/codex")
    ):
        with candidate.open("rb") as stream:
            if stream.read(4) == b"\x7fELF":
                return candidate
    return binary


def build_deepseek(cfg, provider, spec, checked, info, state):
    if any(m not in checked["models"] for m in spec["models"]):
        raise ValueError("DeepSeek model not available on the account.")
    cfg[provider] = {
        "binary": info["binary"],
        "api_provider": {
            "url": deepseek.API,
            "key_file": str(deepseek.key_file(state)),
        },
        "integrations": spec.get("integrations", []),
    }
    cfg["deepseek_models"] = {m: checked["models"][m] for m in spec["models"]}


def catalog_models(cfg, provider, models, catalog):
    """The ``models`` this run routes to once codex and gemini are checked against ``catalog``.

    A codex model the catalog no longer returns loses only its own route in ``cfg``; the saved
    settings keep the selection so the admin can repair it. Gemini still refuses to start.
    """
    if provider not in ("codex", "gemini"):
        return models
    kept = [model for model in models if model in catalog]
    retired = [model for model in models if model not in catalog]
    if not retired:
        return models
    if provider == "gemini":
        raise ValueError(CATALOG_MISSING)
    logger.warning(
        "%s: %s Routes unavailable until repaired: %s",
        provider, CATALOG_MISSING, ", ".join(retired),
    )
    cfg["services"][provider]["models"] = kept
    cfg.setdefault("unavailable_models", {})[provider] = dict.fromkeys(retired, CATALOG_MISSING)
    return kept


def build_cli_provider(cfg, provider, spec, checked, info, state):
    """A provider CLI (codex, claude, gemini, and the CLI half of local)."""
    # A pending native Claude login is an account condition; it must
    # not prevent the UI and other configured providers from starting.
    if not checked["authenticated"] and provider != "claude":
        raise ValueError(
            "Log in to "
            + provider
            + " and use local file authentication. Keychain is not supported by the current sandbox."
        )
    binary = native_binary(info["binary"])
    models = catalog_models(cfg, provider, spec["models"], checked["models"])
    cfg[provider] = {
        "binary": str(binary),
        "auth_file": info["auth_file"],
        "python": sys.executable,
        "integrations": spec.get("integrations", []),
    }
    if provider == "claude" and "global_hooks" in spec:
        cfg[provider]["global_hooks"] = spec["global_hooks"] is True
    if provider == "claude" and (state / "claude-cli-login").exists():
        cfg[provider]["use_cli_login"] = True
    cfg[provider + "_models"] = (
        {m: checked["models"].get(m, ["configured"]) for m in models}
        if provider in ("codex", "claude")
        else models
    )


def build_local(cfg, provider, spec, checked, info, state):
    build_cli_provider(cfg, provider, spec, checked, info, state)
    if any(m not in checked["models"] for m in spec["models"]):
        raise ValueError("Local model is not available. Refresh discovery.")
    cfg[provider]["local_provider"] = "ollama"
    cfg[provider]["local_models"] = {m["id"]: m for m in info.get("runtimes", [])}
    cfg["services"][provider]["model_permissions"] = runtime_permissions(
        state, info.get("runtimes", []), spec["models"]
    )
    cfg[provider]["model_roots"] = runtime_roots(state, info.get("runtimes", []), spec["models"])
    if any(
        permissions.get("upload")
        for permissions in cfg["services"][provider]["model_permissions"].values()
    ):
        cfg["uploads_enabled"] = True


# Any other provider goes through build_cli_provider.
BUILDERS = MappingProxyType({"deepseek": build_deepseek, "local": build_local})


def check_mcp_defaults(cfg):
    defaults = cfg.get("mcp_defaults") or {}
    if defaults:
        backend = defaults["backend"]
        model = defaults["model"]
        if model in cfg.get("unavailable_models", {}).get(backend, {}):
            return  # its route is quarantined: the default fails alone, not the start
        catalog = cfg.get(backend + "_models", {})
        supported = catalog.get(model, []) if isinstance(catalog, dict) else ["configured"]
        if defaults.get("effort") and defaults["effort"] not in supported:
            raise ValueError("Check the default MCP effort before starting.")


def mark_unrestricted(cfg, integrations):
    """``integrations`` is called only when codex or deepseek is configured."""
    for provider in ("codex", "claude", "deepseek"):
        if provider in cfg:
            cfg[provider]["unrestricted"] = True
            if provider in ("codex", "deepseek"):
                cfg[provider]["plugin_inventory"] = [
                    item["id"]
                    for item in integrations().get(provider, [])
                    if item.get("kind") == "plugin"
                ]


def build_clients(cfg, settings, state, previous):
    """Reuse known client hashes; creates ``vpn.key`` on first use (after provider checks)."""
    all_projects = list(cfg["projects"])
    vpnkey = state / "vpn.key"
    if not vpnkey.exists():
        vpnkey.write_text(secrets.token_urlsafe(48))
        vpnkey.chmod(0o600)
    clients = previous.get("clients", {})
    cfg["clients"]["vpn"] = {
        "sha256": clients.get("vpn", {}).get(
            "sha256", hashlib.sha256(vpnkey.read_text().encode()).hexdigest()
        ),
        "projects": all_projects,
    }
    cfg["clients"]["local"] = {
        "sha256": clients.get("local", {}).get(
            "sha256", hashlib.sha256(secrets.token_bytes(48)).hexdigest()
        ),
        "projects": all_projects,
    }
    cfg["tailscale_logins"] = {}
    for login in settings["logins"]:
        client = "tailnet-" + hashlib.sha256(login.encode()).hexdigest()[:16]
        cfg["clients"][client] = {
            "sha256": clients.get(client, {}).get(
                "sha256", hashlib.sha256(secrets.token_bytes(48)).hexdigest()
            ),
            "projects": all_projects,
        }
        cfg["tailscale_logins"][login] = client


def build_origins(cfg, settings, inventory):
    port = settings["port"]
    host = inventory["network"].get("hostname")
    remote = settings["tailnet_port"]
    bind = settings.get("vpn_bind", "127.0.0.1")
    cfg["origins"] = [
        f"http://{bind}:{port}",
        f"http://127.0.0.1:{port}",
        f"http://localhost:{port}",
    ] + ([f"http://{host}:{remote}"] if host else [])
