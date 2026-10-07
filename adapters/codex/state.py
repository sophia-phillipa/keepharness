"""Codex provider state, read side: the effective plugins, skills, MCP servers and apps.

Everything is read through ``codex app-server`` (``config/read`` with layers, ``skills/list``,
``plugin/list``, ``app/list``), so no TOML library is involved and nothing is ever written here.
Writes are a later package; ``set_enabled`` is a stub until then.

* Layers come strongest first (project, profile, user, system). The effective value of an item is
  the first layer that sets ``<kind>.<id>.enabled``, and that layer is the deciding one; when none
  does, the value is the CLI's own (``plugin/list``, ``app/list``) or true, and the user layer,
  where a write would go, is shown. A project layer that the CLI marks as disabled (untrusted
  project) is skipped. Only user-layer rows are writable: the CLI refuses to write project config
  (``configLayerReadonly``), and profile, system and managed layers have no writer.
* Only ``enabled`` flags, ids and display names leave a layer. MCP ``env``, ``http_headers`` and
  any other value are never copied, so no secret can reach a snapshot, a warning or a log line.
* ``StateSnapshot.fingerprint`` is a sha256 over the layer ``version`` strings (``config/read``
  already hashes each file's bytes) plus the ids and flags the three lists returned, not over
  the file bytes themselves: this adapter must not read ``config.toml`` on its own.
* The adapter is synchronous and runs the async RPC client with ``asyncio.run``; async callers
  must use ``asyncio.to_thread``. Each CLI call is bounded by ``CALL_SECONDS``.
* Degraded app-server (cannot start, a method missing or failing): what could be read still shows,
  every row is read-only with a reason and a warning names the method. Only when nothing could be
  read is ``ProviderStateSchemaError`` raised.
"""

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from adapters.codex.rpc import RPCError, connection
from adapters.shared.process import child_environment
from adapters.shared.provider_state import (
    CredentialRule,
    LoginStatus,
    ProviderStateSchemaError,
    ProviderStateUnsupportedError,
    RunSetup,
    Scope,
    SecretStr,
    StateItem,
    StateSnapshot,
)

logger = logging.getLogger(__name__)

PROVIDER = ENGINE = "codex"
TESTED_VERSIONS = ">=0.157,<0.158"
_TESTED = (0, 157)
CALL_SECONDS = 10

_PROJECT_REASON = (
    "Codex cannot write project config (configLayerReadonly); edit the project's "
    ".codex/config.toml by hand."
)
_MANAGED_REASON = "System and managed config is read-only."
_SKILL_SCOPES = {
    "user": ("user", ""),
    "repo": ("project", ""),  # switched in the user config, where the CLI writes it
    "system": ("managed", "Bundled with Codex."),
    "admin": ("managed", "Installed by an administrator."),
}
_ORDER = {"plugin": 0, "skill": 1, "mcp": 2, "app": 3}


@dataclass(frozen=True)
class _Layer:
    scope: Scope
    source: str
    version: str
    config: dict
    reason: str  # why it is not writable; empty for the user layer


def _codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def _layer(entry: dict) -> _Layer:
    name = entry.get("name") if isinstance(entry.get("name"), dict) else {}
    config = entry.get("config") if isinstance(entry.get("config"), dict) else {}
    version, kind = str(entry.get("version", "")), name.get("type")
    if kind == "user" and not name.get("profile"):
        return _Layer("user", str(name.get("file", "")), version, config, "")
    if kind == "user":
        reason = f"Set by the profile '{name['profile']}'; Codex writes only the base user config."
        return _Layer("profile", str(name.get("file", "")), version, config, reason)
    if kind == "project":
        source = f"{name.get('dotCodexFolder', '')}/config.toml"
        return _Layer("project", source, version, config, _PROJECT_REASON)
    return _Layer("managed", str(name.get("file") or kind or ""), version, config, _MANAGED_REASON)


def _decide(layers: list[_Layer], key: str) -> dict[str, tuple[_Layer, bool | None]]:
    """``id -> (deciding layer, enabled or None)`` for every ``<key>.<id>`` table in any layer."""
    decided: dict[str, tuple[_Layer, bool | None]] = {}
    for layer in layers:  # strongest first
        tables = layer.config.get(key)
        for item_id, table in tables.items() if isinstance(tables, dict) else ():
            if not isinstance(table, dict) or item_id == "_default":
                continue
            flag = table.get("enabled") if isinstance(table.get("enabled"), bool) else None
            current = decided.get(item_id)
            if current is None or (current[1] is None and flag is not None):
                decided[item_id] = (layer, flag)
    return decided


def _listed(result: dict | None, *path: str) -> list[dict]:
    value = result
    for step in path:
        value = value.get(step) if isinstance(value, dict) else None
    return [entry for entry in value or [] if isinstance(entry, dict)]


def _plugin_list(result: dict | None) -> dict[str, tuple[str, bool | None]]:
    found = {}
    for market in _listed(result, "marketplaces"):
        for plugin in _listed(market, "plugins"):
            plugin_id = plugin.get("id") or f"{plugin.get('name')}@{market.get('name')}"
            if plugin.get("installed") is not False and isinstance(plugin_id, str):
                flag = plugin.get("enabled") if isinstance(plugin.get("enabled"), bool) else None
                found[plugin_id] = (str(plugin.get("name") or plugin_id.split("@")[0]), flag)
    return found


def _app_list(result: dict | None) -> dict[str, tuple[str, bool | None]]:
    found = {}
    for app in _listed(result, "data"):
        if isinstance(app.get("id"), str):
            flag = app.get("isEnabled") if isinstance(app.get("isEnabled"), bool) else None
            found[app["id"]] = (str(app.get("name") or app["id"]), flag)
    return found


def _skill_list(result: dict | None) -> dict[str, dict]:
    found = {}
    for group in _listed(result, "data"):
        for skill in _listed(group, "skills"):
            if isinstance(skill.get("path"), str) and isinstance(skill.get("enabled"), bool):
                found[skill["path"]] = skill
    return found


def _cli_version(binary: str) -> str:
    try:
        done = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            text=True,
            timeout=CALL_SECONDS,
            env=child_environment(),
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    found = re.search(r"\d+\.\d+\.\d+\S*", done.stdout)
    return found.group(0) if found else ""


def _tested(version: str) -> bool:
    found = re.match(r"(\d+)\.(\d+)", version)
    return bool(found) and (int(found.group(1)), int(found.group(2))) == _TESTED


async def _ask(binary: str, requests: list[tuple[str, str, dict]]) -> tuple[dict, dict]:
    """One app-server session: ``({key: result}, {key: why})`` for the requests in order."""
    results: dict[str, dict] = {}
    failures: dict[str, str] = {}
    command = [binary, "app-server", "--listen", "stdio://"]
    try:
        async with connection(command, config={"idle_timeout_seconds": CALL_SECONDS}) as rpc:
            for key, method, params in requests:
                try:
                    results[key] = await rpc.call(method, params)
                except RPCError:
                    failures[key] = f"{method} was refused"
    except Exception as exc:  # start, framing or timeout: the rest of the session is unreadable
        logger.debug("codex app-server session ended: %s", type(exc).__name__)
        for key, method, _ in requests:
            if key not in results and key not in failures:
                failures[key] = f"{method} is unavailable"
    return results, failures


class CodexStateAdapter:
    """The Codex view of the CLI's real state. Synchronous: async callers use ``to_thread``."""

    def _binary(self) -> str:
        binary = shutil.which("codex")
        if binary is None:
            raise ProviderStateSchemaError("the codex CLI is not on PATH")
        return binary

    def read_state(self, project_root: Path | None) -> StateSnapshot:
        binary = self._binary()
        cwd = str(project_root) if project_root else str(Path.home())
        config_params = {"includeLayers": True, **({"cwd": cwd} if project_root else {})}
        results, failures = asyncio.run(
            _ask(
                binary,
                [
                    ("config", "config/read", config_params),
                    ("skills", "skills/list", {"cwds": [cwd], "forceReload": False}),
                    ("plugins", "plugin/list", {}),
                    ("apps", "app/list", {}),
                ],
            )
        )
        if not results:
            raise ProviderStateSchemaError(
                "Codex state is not readable: " + "; ".join(failures.values())
            )
        warnings = [f"Codex: {why}; its rows are read-only." for why in failures.values()]

        if "config" in results and not isinstance(results["config"].get("layers"), list):
            failures["config"] = "config/read answered in an unexpected shape"
            warnings.append(f"Codex: {failures['config']}; its rows are read-only.")
        raw_layers = _listed(results.get("config"), "layers")
        layers = []
        for entry in raw_layers:
            if entry.get("disabledReason"):
                warnings.append("Project config skipped: the project is not trusted by Codex.")
            else:
                layers.append(_layer(entry))
        locked = (
            "Codex app-server did not answer every request; switches are read-only until it does."
            if failures
            else ""
        )
        user = next((layer for layer in layers if layer.scope == "user"), None)

        def row(kind, item_id, name, enabled, scope, source, reason):
            return StateItem(
                id=f"{kind}:{item_id}",
                kind=kind,
                name=name,
                scope=scope,
                enabled=enabled,
                source=source,
                writable=not (reason or locked),
                reason=reason or locked,
            )

        def layered_rows(kind, key, listed):
            decided = _decide(layers, key)
            fallback = user or _Layer("user", "", "", {}, "")
            for item_id in decided.keys() | listed.keys():
                layer, flag = decided.get(item_id, (fallback, None))
                name, listed_flag = listed.get(
                    item_id, (item_id.split("@")[0] if kind == "plugin" else item_id, None)
                )
                chosen = flag if flag is not None else listed_flag
                yield row(
                    kind,
                    item_id,
                    name,
                    chosen is not False,
                    layer.scope,
                    layer.source,
                    layer.reason,
                )

        items = []
        items += layered_rows("plugin", "plugins", _plugin_list(results.get("plugins")))
        items += layered_rows("app", "apps", _app_list(results.get("apps")))
        items += layered_rows("mcp", "mcp_servers", {})
        for path, skill in _skill_list(results.get("skills")).items():
            scope, reason = _SKILL_SCOPES.get(
                skill.get("scope"), ("managed", "Unrecognised skill scope.")
            )
            source = user.source if user and not reason else path
            name = str(skill.get("name") or path)
            items.append(row("skill", path, name, skill["enabled"], scope, source, reason))
        items.sort(key=lambda item: (_ORDER[item.kind], item.id))

        digest = hashlib.sha256()
        for entry in raw_layers:
            digest.update(
                f"layer\0{json.dumps(entry.get('name'), sort_keys=True)}\0{entry.get('version')}\n".encode()
            )
        flags = sorted((item.id, item.enabled) for item in items if item.kind != "mcp")
        digest.update(json.dumps(flags).encode())

        version = _cli_version(binary)
        if not version:
            warnings.append("The Codex version could not be read.")
        elif not _tested(version):
            warnings.append(
                f"Codex {version} is outside the tested range {TESTED_VERSIONS}; "
                "reads may be incomplete."
            )
        return StateSnapshot(
            provider=PROVIDER,
            engine=ENGINE,
            project_root=str(project_root) if project_root else None,
            items=tuple(items),
            fingerprint=digest.hexdigest(),
            cli_version=version or "unknown",
            warnings=tuple(dict.fromkeys(warnings)),
        )

    def set_enabled(
        self,
        item_id: str,
        scope: Scope,
        enabled: bool,
        expected_fingerprint: str,
        *,
        project_root: Path | None = None,
    ) -> StateSnapshot:
        raise ProviderStateUnsupportedError("writes land in the next package")

    def watch_paths(self, project_root: Path | None) -> tuple[Path, ...]:
        home = _codex_home()
        paths = (home / "config.toml", home / "skills")
        if project_root is None:
            return paths
        project = Path(project_root)
        return (*paths, project / ".codex" / "config.toml", project / ".agents" / "skills")

    def is_project_trusted(self, project_root: Path) -> bool:
        binary = shutil.which("codex")
        if binary is None:
            return False
        params = {"includeLayers": True, "cwd": str(project_root)}
        results, _ = asyncio.run(_ask(binary, [("config", "config/read", params)]))
        result = results.get("config") or {}
        sources = [
            result.get("config"),
            *(layer.get("config") for layer in _listed(result, "layers")),
        ]
        keys = {str(project_root), str(Path(project_root).resolve())}
        for source in sources:
            projects = source.get("projects") if isinstance(source, dict) else None
            for key in keys:
                table = projects.get(key) if isinstance(projects, dict) else None
                if isinstance(table, dict) and table.get("trust_level") == "trusted":
                    return True
        return False

    def trust_project(self, project_root: Path) -> None:
        raise ProviderStateUnsupportedError("trusting a project from KeepHarness lands with #44")

    def approved_project_servers(self, project_root: Path) -> frozenset[str]:
        return frozenset()  # Codex has no .mcp.json

    def run_environment(
        self, project_root: Path, trusted: bool, permission_flags: Sequence[str]
    ) -> RunSetup:
        raise ProviderStateUnsupportedError("the run setup on the real home lands with #45")

    def credential_isolation(self) -> CredentialRule | None:
        return None

    def login_command(self, headless: bool) -> list[str]:
        raise ProviderStateUnsupportedError("the login on the real home lands with #45")

    def login_status(self) -> LoginStatus:
        raise ProviderStateUnsupportedError("the login status on the real home lands with #45")

    def set_api_key(self, secret: SecretStr) -> None:
        raise ProviderStateUnsupportedError("Codex signs in with OAuth; it has no API key file")
