"""Admin-panel state: settings, provider checks and the agent-service process."""

import asyncio
import ipaddress
import json
import os
import re
import secrets
import socket
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import httpx

from adapters.claude import account as claude
from adapters.claude.auth import cli_login_environment
from adapters.codex.rpc import metadata
from adapters.deepseek import account as deepseek
from adapters.gemini import account as gemini
from agent_service.config import VERSION_FILE
from agent_service.work_items import validate_pattern

from . import discovery, env, integration_catalog, integrations, runtime_config
from .dashboard import DashboardReader
from .operations import Operations
from .persistence import ControlStateRepository, private_file

ROOT = env.REPOSITORY_ROOT
PERMISSIONS = ("read", "write", "upload", "tests", "internet", "shell", "hooks")


def migrate_local_ai_directory(root: Path, state: Path) -> None:
    """One-time rename of the legacy ``local-ai`` runtime directory to ``local_ai``.

    Also rewrites any local model profile in ``state`` that still points at the old path.
    Safe to call on every startup: a no-op once the rename and the profile rewrite are done.
    """
    legacy = root / "local-ai"
    current = root / "local_ai"
    if legacy.is_dir() and not current.exists():
        legacy.rename(current)
    for name in ("local-profiles.json", "local-profile.json"):
        target = state / name
        try:
            text = target.read_text()
        except (FileNotFoundError, OSError):
            continue
        if "local-ai/" not in text:
            continue
        target.write_text(text.replace("local-ai/", "local_ai/"))


class Manager:
    def __init__(self, state):
        self.state = Path(state)
        self.state.mkdir(parents=True, exist_ok=True, mode=0o700)
        # mkdir keeps an existing folder's mode (e.g. one a venv created as 0755).
        if self.state.stat().st_uid == os.getuid():
            self.state.chmod(0o700)
        migrate_local_ai_directory(env.LOCAL_AI_ROOT, self.state)
        self.state_repository = ControlStateRepository(self.state)
        self.path = self.state_repository.settings_path
        self.cookie = secrets.token_urlsafe(32)
        self.admin_port = 8094
        self.dashboard = DashboardReader(self.state)
        self.inventory = None
        self.plugin_catalog = None
        self.proc = None
        self.lock = asyncio.Lock()
        self.settings = (
            json.loads(self.path.read_text())
            if self.path.exists()
            else {
                "services": {
                    p: {
                        "enabled": False,
                        "models": [],
                        "projects": ["sem-projeto"],
                        "mode": "native",
                        "integrations": [],
                        "permissions": {k: False for k in PERMISSIONS},
                    }
                    for p in ("codex", "claude", "gemini", "local", "deepseek")
                },
                "projects": [],
                "catalogs": [],
                "uploads_enabled": False,
                "port": 8095,
                "tailnet_port": 8095,
                "logins": [],
            }
        )
        self.settings["services"].setdefault(
            "deepseek",
            {
                "enabled": False,
                "added": False,
                "models": [],
                "projects": ["sem-projeto"],
                "mode": "native",
                "integrations": [],
                "permissions": {k: False for k in PERMISSIONS},
            },
        )
        self.settings["services"].setdefault(
            "gemini",
            {
                "enabled": False,
                "added": False,
                "models": [],
                "projects": ["sem-projeto"],
                "mode": "native",
                "integrations": [],
                "permissions": {k: False for k in PERMISSIONS},
            },
        )
        self.provider_models = {}
        self.auth = {}
        self.applied = None
        self.operations = Operations()
        self.startup_error = None
        self.provider_revisions = {}

    def audit(self, action):
        self.state_repository.audit(action)

    async def refresh(self):
        self.inventory = await discovery.scan()
        binary = self.inventory.get("binaries", {}).get("codex")
        if binary:
            plugins = await integration_catalog.installed_plugins(binary)
            if plugins is not None:
                self.plugin_catalog = plugins
            else:
                self.inventory["integration_warnings"] = [
                    "Could not confirm the installed plugins; kept the last known inventory."
                ]
        return self.inventory

    def integrations(self):
        result = integrations.inventory()
        if self.plugin_catalog is not None:
            result["codex"] = [
                item for item in result.get("codex", []) if item.get("kind") != "plugin"
            ] + self.plugin_catalog
            result["local"] = result["deepseek"] = result["codex"]
        return result

    def busy(self):
        return self.state_repository.harness_busy()

    def running(self):
        return self.proc is not None and self.proc.returncode is None

    async def claude_login_completed(self):
        # No token is copied or stored here. Claude owns the renewed credential.
        marker = self.state / "claude-cli-login"
        marker.touch(mode=0o600)
        runtime = self._previous_runtime()
        if "claude" in runtime:
            runtime["claude"]["use_cli_login"] = True
            self._write_runtime(runtime)
        self.auth["claude"] = True
        self.audit("claude_login_completed")

    def validate(self, data):
        if not isinstance(data, dict):
            raise ValueError("Invalid configuration.")
        out = {}
        for key in ("port", "tailnet_port"):
            value = data.get(key, 8095)
            if type(value) is not int or not 1024 <= value <= 65535 or value == self.admin_port:
                raise ValueError("Invalid port, or reserved by the management panel.")
            out[key] = value
        bind = data.get("vpn_bind", "127.0.0.1")
        if not isinstance(bind, str):
            raise ValueError("Provide the private IP as text.")
        address = ipaddress.ip_address(bind)
        if (
            address.version != 4
            or not (address.is_loopback or address.is_private)
            or address.is_unspecified
            or address.is_multicast
        ):
            raise ValueError(
                "Use only the private IP specific to the VPN interface, never 0.0.0.0."
            )
        out["vpn_bind"] = bind
        out["uploads_enabled"] = data.get("uploads_enabled") is True or any(
            data.get("services", {}).get(p, {}).get("enabled") is True
            for p in ("codex", "claude", "gemini", "deepseek")
        )
        out["maestro_enabled"] = data.get("maestro_enabled", True) is True
        policy = data.get("maestro_instructions", "")
        if not isinstance(policy, str) or len(policy) > 12000:
            raise ValueError("Maestro instructions: maximum of 12,000 characters.")
        out["maestro_instructions"] = policy
        default = data.get("default_backend", "")
        if default not in ("", "codex", "claude", "gemini", "local", "deepseek"):
            raise ValueError("Invalid default executor.")
        out["default_backend"] = default
        catalogs = []
        catalog_ids = set()
        saved_catalog_roots = {
            catalog.get("id"): catalog.get("root")
            for catalog in self.settings.get("catalogs", [])
        }
        raw_catalogs = data.get("catalogs", [])
        if not isinstance(raw_catalogs, list) or len(raw_catalogs) > 50:
            raise ValueError("Invalid catalog configuration.")
        for catalog in raw_catalogs:
            if not isinstance(catalog, dict):
                raise ValueError("Invalid catalog configuration.")
            catalog_id = catalog.get("id", "")
            namespace = catalog.get("namespace", "")
            kind = catalog.get("kind", "folder")
            catalog_root_value = catalog.get("root", "")
            if (
                not isinstance(catalog_id, str)
                or not isinstance(namespace, str)
                or not isinstance(catalog_root_value, str)
                or not re.fullmatch(r"[a-z0-9_-]{1,64}", catalog_id)
                or catalog_id in catalog_ids
                or not re.fullmatch(r"[a-z0-9_-]{1,64}", namespace)
                or kind not in ("folder", "git")
                or type(catalog.get("trusted", False)) is not bool
            ):
                raise ValueError("Invalid catalog configuration.")
            raw = Path(catalog_root_value).expanduser()
            saved_missing = (
                not raw.exists() and saved_catalog_roots.get(catalog_id) == str(raw)
            )
            if not raw.is_absolute() or (not raw.is_dir() and not saved_missing):
                raise ValueError("Choose an existing, absolute catalog folder.")
            catalog_root = raw.resolve()
            home = Path.home().resolve()
            forbidden = (
                Path("/"),
                home,
                home / ".ssh",
                home / ".codex",
                home / ".claude",
                home / ".gemini",
                home / ".config",
                self.state.resolve(),
            )
            if catalog_root in forbidden or any(
                catalog_root.is_relative_to(path) or path.is_relative_to(catalog_root)
                for path in forbidden[2:]
            ):
                raise ValueError("A broad or credentials folder cannot be a catalog.")
            pin = catalog.get("pin", "")
            if not isinstance(pin, str) or len(pin) > 160:
                raise ValueError("Invalid catalog pin.")
            catalogs.append(
                {
                    "id": catalog_id,
                    "root": str(catalog_root),
                    "kind": kind,
                    "trusted": catalog.get("trusted", False),
                    "namespace": namespace,
                    **({"pin": pin} if pin else {}),
                    **(
                        {"work_item_pattern": validate_pattern(catalog["work_item_pattern"])}
                        if "work_item_pattern" in catalog
                        else {}
                    ),
                }
            )
            catalog_ids.add(catalog_id)
        out["catalogs"] = catalogs
        projects = []
        ids = set()
        saved_roots = {p.get("id"): p.get("root") for p in self.settings.get("projects", [])}
        for project in data.get("projects", []):
            pid = project.get("id", "")
            label = project.get("label", "")
            raw = Path(project.get("root", "")).expanduser()
            if not re.fullmatch("[a-z0-9_-]{1,64}", pid) or pid == "sem-projeto" or pid in ids:
                raise ValueError("Invalid or repeated project identifier.")
            # A removed, previously registered folder must not stop unrelated projects.
            saved_missing = not raw.exists() and saved_roots.get(pid) == str(raw)
            if not raw.is_absolute() or (not raw.is_dir() and not saved_missing):
                raise ValueError("Choose an existing, absolute folder.")
            root = raw.resolve()
            home = Path.home().resolve()
            forbidden = [
                Path("/"),
                home,
                home / ".ssh",
                home / ".codex",
                home / ".claude",
                home / ".gemini",
                home / ".config",
                self.state.resolve(),
            ]
            if root in forbidden or any(
                root.is_relative_to(x) or x.is_relative_to(root) for x in forbidden[2:]
            ):
                raise ValueError("A broad or credentials folder cannot be shared.")
            if len(label) > 100:
                raise ValueError("Project name too long.")
            units = project.get("service_units", [])
            if (
                not isinstance(units, list)
                or len(units) > 20
                or any(
                    not isinstance(u, str)
                    or not re.fullmatch(r"[A-Za-z0-9_@.][A-Za-z0-9_@.-]*\.service", u)
                    for u in units
                )
            ):
                raise ValueError("Services must be user .service unit names.")
            overrides = project.get("permissions", {})
            if (
                not isinstance(overrides, dict)
                or set(overrides) - {*PERMISSIONS, "delegate"}
                or any(type(value) is not bool for value in overrides.values())
            ):
                raise ValueError(
                    "Project permissions must be known boolean values; omit to inherit."
                )
            project_catalogs = project.get("catalogs", [])
            if (
                not isinstance(project_catalogs, list)
                or len(project_catalogs) > 50
                or any(
                    not isinstance(value, str) or value not in catalog_ids
                    for value in project_catalogs
                )
            ):
                raise ValueError("Catalog not registered.")
            projects.append(
                {
                    "id": pid,
                    "label": label or pid,
                    "root": str(root),
                    "service_units": list(dict.fromkeys(units)),
                    "permissions": dict(overrides),
                    "catalogs": list(dict.fromkeys(project_catalogs)),
                    **(
                        {"work_item_pattern": validate_pattern(project["work_item_pattern"])}
                        if "work_item_pattern" in project
                        else {}
                    ),
                }
            )
            ids.add(pid)
        out["projects"] = projects
        out["services"] = {}
        for provider in ("codex", "claude", "gemini", "local", "deepseek"):
            spec = data.get("services", {}).get(provider, {})
            models = spec.get("models", [])
            allowed_projects = spec.get("projects", [])
            model_pattern = (
                r"[a-zA-Z0-9_./:-]{1,160}(?:\[1m\])?"
                if provider == "claude"
                else r"[a-zA-Z0-9_./:-]{1,160}"
            )
            if (
                not isinstance(models, list)
                or len(models) > 50
                or any(not isinstance(x, str) or not re.fullmatch(model_pattern, x) for x in models)
            ):
                raise ValueError("Invalid model list.")
            # Same rule as TailUI.selectableModel, which hides other ids from both UIs;
            # ids stored by an older version stay accepted so saving never locks up.
            stored = self.settings["services"].get(provider, {}).get("models", [])
            hidden = [
                x
                for x in models
                if provider == "claude"
                and x not in stored
                and not re.fullmatch(r"claude-[a-z]+-\d{1,3}(?:-\d{1,3})?", x)
            ]
            if hidden:
                raise ValueError(
                    f"Use a versioned Claude model id such as claude-sonnet-4-6, not {hidden[0]}."
                )
            if not isinstance(allowed_projects, list) or any(
                p not in ids | {"sem-projeto"} for p in allowed_projects
            ):
                raise ValueError("Project not registered.")
            if not isinstance(spec.get("permissions", {}), dict):
                raise ValueError("Invalid permissions.")
            perms = {
                k: (provider != "local" or spec.get("permissions", {}).get(k) is True)
                for k in PERMISSIONS
            }
            if perms["write"] and not perms["read"]:
                raise ValueError("To allow changes, also enable read access.")
            if provider == "local" and perms["upload"] and not out["uploads_enabled"]:
                raise ValueError(
                    "Enable global uploads before allowing attachments on the service."
                )
            selected = spec.get("integrations", [])
            available = {x["id"] for x in self.integrations().get(provider, [])}
            if not isinstance(selected, list) or any(x not in available for x in selected):
                raise ValueError("Integration not found. Refresh the inventory.")
            if selected and not perms["internet"]:
                raise ValueError("Connectors require internet access in this version.")
            enabled = spec.get("enabled") is True
            allowed_projects = ["sem-projeto", *[p["id"] for p in projects]]
            if enabled and not models:
                raise ValueError("Select models for the enabled service.")
            out["services"][provider] = {
                "added": spec.get("added") is True or enabled or bool(models),
                # Providers are native-only; isolation is chosen per conversation.
                "mode": "native",
                "integrations": selected,
                "enabled": enabled,
                "models": list(dict.fromkeys(models)),
                "projects": allowed_projects,
                "permissions": perms,
            }
            if provider == "claude" and "global_hooks" in spec:
                if type(spec["global_hooks"]) is not bool:
                    raise ValueError("Global hooks must be an explicit boolean.")
                out["services"][provider]["global_hooks"] = spec["global_hooks"]
        logins = data.get("logins", [])
        if (
            not isinstance(logins, list)
            or len(logins) > 50
            or any(
                not isinstance(x, str) or not re.fullmatch("[A-Za-z0-9_.+@-]{1,160}", x)
                for x in logins
            )
        ):
            raise ValueError("Invalid Tailscale identities.")
        defaults = data.get("mcp_defaults") or {}
        if not isinstance(defaults, dict) or set(defaults) - {"backend", "model", "effort"}:
            raise ValueError("Invalid MCP defaults.")
        if defaults:
            backend = defaults.get("backend")
            model = defaults.get("model")
            effort = defaults.get("effort", "")
            if not isinstance(backend, str) or backend not in out["services"]:
                raise ValueError("Choose a valid MCP provider.")
            spec = out["services"][backend]
            if not spec["enabled"] or model not in spec["models"]:
                raise ValueError("The default MCP model must be enabled.")
            if effort not in (
                "",
                "configured",
                "none",
                "minimal",
                "low",
                "medium",
                "high",
                "xhigh",
                "max",
                "ultra",
            ):
                raise ValueError("Invalid default MCP effort.")
            supported = self.provider_models.get(backend, {}).get(model)
            if supported and effort and effort not in supported:
                raise ValueError("Effort not available for the default model.")
            defaults = {"backend": backend, "model": model, "effort": effort}
        out["mcp_defaults"] = defaults
        out["logins"] = list(dict.fromkeys(logins))
        return out

    def save(self, data):
        settings = self.validate(data)
        if (self.state / "tailnet.json").exists() and any(
            settings.get(k) != self.settings.get(k) for k in ("port", "tailnet_port", "vpn_bind")
        ):
            raise ValueError("Remove the Tailscale route before changing the ports or the IP.")
        self.state_repository.save_settings(settings)
        self.settings = settings
        self.audit("settings_saved")

    @contextmanager
    def configuration_change(self):
        """Restore configuration files on a rejected or interrupted update."""
        previous = self.state_repository.snapshot()
        settings = self.settings
        try:
            yield
        except BaseException:
            self.settings = settings
            self.state_repository.restore(previous)
            raise

    def _previous_runtime(self):
        return self.state_repository.read_runtime()

    def _write_runtime(self, config):
        self.state_repository.write_runtime(config)

    def browser_url(self, settings):
        host = (self.inventory or {}).get("network", {}).get("hostname")
        if host and settings.get("logins") and (self.state / "tailnet.json").exists():
            return f"http://{host}:{settings['tailnet_port']}/"
        return None

    async def build_runtime_config(self, settings, allow_empty=False):
        """Build the process-owned configuration without starting or restarting it."""
        if self.inventory is None:
            await self.refresh()
        settings = self.validate(settings)
        cfg = runtime_config.base_config(
            settings,
            self.state,
            self.admin_port,
            self.browser_url(settings),
            getattr(self, "provider_revisions", {}),
        )
        enabled = 0
        for provider, spec in settings["services"].items():
            if not spec["enabled"]:
                continue
            enabled += 1
            checked = await self.check(provider)
            info = next(s for s in self.inventory["services"] if s["id"] == provider)
            build = runtime_config.BUILDERS.get(provider, runtime_config.build_cli_provider)
            build(cfg, provider, spec, checked, info, self.state)
        if not enabled and not allow_empty:
            raise ValueError("Enable at least one service.")
        runtime_config.check_mcp_defaults(cfg)
        runtime_config.mark_unrestricted(cfg, self.integrations)
        runtime_config.build_clients(cfg, settings, self.state, self._previous_runtime())
        runtime_config.build_origins(cfg, settings, self.inventory)
        return cfg

    async def apply_settings(self, data):
        settings = self.validate(data)
        # The new configuration may repair integrations removed from the CLI.
        current = {
            "port": self.settings.get("port", 8095),
            "vpn_bind": self.settings.get("vpn_bind", "127.0.0.1"),
        }
        if self.running() and any(
            settings.get(key) != current.get(key) for key in ("port", "vpn_bind")
        ):
            raise ValueError("To change the address or port, restart the harness.")
        previous_proc, previous_applied, previous_error = (
            self.proc,
            self.applied,
            self.startup_error,
        )
        with self.configuration_change():
            try:
                if self.running():
                    runtime = await self.build_runtime_config(settings, allow_empty=True)
                    self.save(settings)
                    self._write_runtime(runtime)
                else:
                    self.save(settings)
                    if any(spec.get("enabled") for spec in settings["services"].values()):
                        await self.start()
            except BaseException:
                if self.proc is not previous_proc and self.running():
                    await self.stop(force=True)
                self.proc, self.applied, self.startup_error = (
                    previous_proc,
                    previous_applied,
                    previous_error,
                )
                raise

    async def check(self, provider):
        if provider not in ("codex", "claude", "gemini", "local", "deepseek"):
            raise ValueError("Unknown service.")
        if self.inventory is None:
            await self.refresh()
        info = next(s for s in self.inventory["services"] if s["id"] == provider)
        if not info["found"]:
            raise ValueError("CLI not found. Install it and sign in with the official tool.")
        if provider == "deepseek":
            self.auth[provider] = False
            result = await deepseek.check(self.state)
            self.provider_models[provider] = result["models"]
            self.auth[provider] = True
            return result
        if provider == "local":
            self.provider_models[provider] = {m: ["configured"] for m in info.get("models", [])}
            self.auth[provider] = bool(info["found"])
            return {
                "authenticated": bool(info["found"]),
                "models": self.provider_models[provider],
                "model_source": "Local servers detected (llama.cpp / Ollama)",
            }
        if provider == "gemini":
            self.auth[provider] = False
            result = await gemini.check(info["binary"])
            self.provider_models[provider] = result.get("models", {})
            self.auth[provider] = result.get("authenticated") is True
            self.audit("provider_check:gemini")
            return result
        if provider == "codex":
            code, _ = await discovery.command(info["binary"], "login", "status")
            authenticated = code == 0
            if authenticated:
                listing = await metadata(info["binary"], "model/list")
                self.provider_models[provider] = {
                    m["id"]: [e["reasoningEffort"] for e in m.get("supportedReasoningEfforts", [])]
                    or ["low"]
                    for m in listing.get("data", [])
                }
        else:
            options = (
                {"env": cli_login_environment()}
                if (self.state / "claude-cli-login").exists()
                else {}
            )
            code, raw = await discovery.command(
                info["binary"], "auth", "status", "--json", **options
            )
            try:
                authenticated = code == 0 and json.loads(raw).get("loggedIn") is True
            except ValueError:
                authenticated = False
            self.provider_models[provider] = {}
            if authenticated:
                self.provider_models[provider] = claude.model_catalog(
                    await claude.metadata(
                        {"binary": info["binary"], "use_cli_login": bool(options)}
                    )
                )
        self.auth[provider] = authenticated
        self.audit("provider_check:" + provider)
        return {
            "authenticated": authenticated,
            "models": self.provider_models.get(provider, {}),
            "model_source": "CLI model/list"
            if provider == "codex"
            else "Claude Code catalog and legacy official releases; access subject to account",
        }

    async def start(self):
        if self.running():
            return
        await self.refresh()
        self.settings = self.validate(self.settings)
        cfg = await self.build_runtime_config(self.settings)
        port = self.settings["port"]
        bind = self.settings.get("vpn_bind", "127.0.0.1")
        with socket.socket() as check:
            check.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                check.bind((bind, port))
            except OSError:
                raise ValueError("Port in use. Choose another; no existing service was stopped.")
        path = self.state / "runtime.json"
        self._write_runtime(cfg)
        log = open(self.state / "harness.log", "ab", opener=private_file)
        self.proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "agent_service.app",
            cwd=ROOT,
            env={**os.environ, "TAIL_HARNESS_AGENT_CONFIG": str(path)},
            stdout=log,
            stderr=log,
        )
        log.close()
        async with httpx.AsyncClient(trust_env=False, timeout=1) as client:
            for _ in range(40):
                if self.proc.returncode is not None:
                    raise ValueError("The service exited while starting. Check the local log.")
                try:
                    # The browser entry may redirect to Tailscale; readiness is local.
                    if (await client.get(f"http://{bind}:{port}/ui.css")).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.25)
            else:
                await self.stop(force=True)
                raise ValueError("The service did not become ready in time.")
        self.applied = time.time()
        (self.state / "autostart").touch(mode=0o600)
        self.startup_error = None
        self.audit("harness_started")

    async def stop(self, force=False):
        if not force and self.busy():
            raise ValueError("There are tasks queued or running. Cancel or wait before stopping.")
        if self.running():
            self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), 15)
            except asyncio.TimeoutError:
                self.proc.kill()
                await self.proc.wait()
        if not force:
            (self.state / "autostart").unlink(missing_ok=True)
        self.audit("harness_stopped")

    async def tailnet(self, enabled):
        if not self.inventory:
            await self.refresh()
        binary = self.inventory["binaries"]["tailscale"]
        port = self.settings["tailnet_port"]
        if not binary or not self.inventory["network"]["online"]:
            raise ValueError("Connect Tailscale first.")
        receipt = self.state / "tailnet.json"
        if enabled:
            if self.settings.get("vpn_bind", "127.0.0.1") != "127.0.0.1":
                raise ValueError(
                    "For Tailscale Serve, use the local address 127.0.0.1; for another VPN, use the configured IP directly."
                )
            if not self.running() or not self.settings["logins"]:
                raise ValueError(
                    "Start the harness and register allowed identities before sharing."
                )
            code, out = await discovery.command(binary, "serve", "status", "--json")
            if code:
                raise ValueError("Could not check the existing routes.")
            # Never overwrite a pre-existing route; only a route recorded as ours can be updated.
            status = json.loads(out or "{}")
            if str(port) in status.get("TCP", {}) and not receipt.exists():
                raise ValueError(
                    "That Tailscale port already belongs to another route. Choose another."
                )
            code, _ = await discovery.command(
                binary,
                "serve",
                "--bg",
                "--http=" + str(port),
                "http://127.0.0.1:" + str(self.settings["port"]),
            )
            if code:
                raise ValueError(
                    "Tailscale refused the configuration. Check the operator permissions in the terminal; we do not run sudo."
                )
            receipt.write_text(json.dumps({"port": port}))
            self.audit("tailnet_enabled")
        else:
            if not receipt.exists():
                raise ValueError("No route for this project to remove.")
            port = json.loads(receipt.read_text())["port"]
            code, _ = await discovery.command(binary, "serve", "--http=" + str(port), "off")
            if code:
                raise ValueError("Could not remove the route.")
            receipt.unlink()
            self.audit("tailnet_disabled")
        runtime = self._previous_runtime()
        if runtime:
            runtime["browser_url"] = self.browser_url(self.settings)
            self._write_runtime(runtime)

    def status(self):
        host = (self.inventory or {}).get("network", {}).get("hostname")
        port = self.settings["port"]
        return {
            "running": self.running(),
            "busy": self.busy(),
            "applied_at": self.applied,
            "local_url": f"http://{self.settings.get('vpn_bind', '127.0.0.1')}:{port}/",
            "remote_url": f"http://{host}:{self.settings['tailnet_port']}/" if host else None,
            "shared": (self.state / "tailnet.json").exists(),
            "version": VERSION_FILE.read_text().strip(),
            "startup_error": self.startup_error,
        }
