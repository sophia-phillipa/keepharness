"""Codex provider state, read side: the effective plugins, skills, MCP servers and apps.

Everything is read through ``codex app-server`` (``config/read`` with layers, ``skills/list``,
``plugin/list``, ``app/list``), so no TOML library is involved and no file is touched here.
``set_enabled`` writes through the same app-server, in the user layer only (the one layer the CLI
writes): plugins, MCP servers and apps with ``config/batchWrite`` (``expectedVersion`` is the user
layer ``version`` that ``config/read`` returned), skills with ``skills/config/write`` on a path
that ``skills/list`` just returned. ``skills/config/write`` has no ``expectedVersion``, so the
user layer version is read again right before it and compared; the milliseconds between that read
and the CLI's own write cannot be closed. A write is confirmed by reading the state again.

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
  must use ``asyncio.to_thread``. A session is bounded by ``CALL_SECONDS`` for the start plus one
  for each request, however many notifications the server sends.
* Degraded app-server (cannot start, a method missing or failing): what could be read still shows,
  and only rows whose writes depend on the missing result are read-only. A warning names the
  method. Only when nothing could be read is ``ProviderStateSchemaError`` raised.
"""

import asyncio
import copy
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

from adapters.codex.rpc import RPCError, connection, provider_message
from adapters.shared.orchestration_state import (
    codex_instructions,
    hook_items,
    safe_details,
    safe_text,
)
from adapters.shared.process import child_environment
from adapters.shared.provider_state import (
    CredentialRule,
    LoginStatus,
    ProviderCommandError,
    ProviderStateConflictError,
    ProviderStateSchemaError,
    ProviderStateTimeoutError,
    ProviderStateUnsupportedError,
    RunSetup,
    Scope,
    SecretStr,
    StateItem,
    StateSnapshot,
    run_state_command,
    state_write_active,
    state_write_remaining,
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
_UNKNOWN_METHOD = "Invalid request: unknown variant"  # what 0.157.1 answers for a method it lacks
_ORDER = {"plugin": 0, "skill": 1, "mcp": 2, "app": 3}
_KEYS = {"plugin": "plugins", "mcp": "mcp_servers", "app": "apps"}
_READ_DEPENDENCIES = {
    "plugin": ("config", "plugins"),
    "skill": ("config", "skills", "plugins"),
    "mcp": ("config",),
    "app": ("config", "apps"),
}
_ADDRESSABLE = re.compile(r"[\w@+-]+")  # a keyPath splits on dots, so ids with dots cannot be named


@dataclass(repr=False)
class _TrustRollback:
    adapter: object
    root: Path
    key_path: str
    value: object
    before_version: str
    expected_config: dict
    written: str | None = None

    def restore(self):
        checkpoint = {}
        _, current = self.adapter._read(self.root, trust_checkpoint=checkpoint)
        if current == self.before_version:
            return
        if self.written is None and checkpoint.get("config") == self.expected_config:
            self.written = current
        if not self.written or current != self.written:
            raise ProviderStateConflictError("Codex changed during rollback.")
        asyncio.run(
            _write(
                self.adapter._binary(),
                "config/batchWrite",
                {
                    "expectedVersion": self.written,
                    "edits": [
                        {"keyPath": self.key_path, "value": self.value, "mergeStrategy": "upsert"}
                    ],
                },
                None,
                environment=self.adapter._environment(),
            )
        )
        restored = {}
        self.adapter._read(self.root, trust_checkpoint=restored)
        if (
            restored.get("exists") != self.key_path.endswith(".trust_level")
            or restored.get("value") != self.value
        ):
            raise ProviderStateConflictError("Codex did not confirm the restored trust state.")


@dataclass(frozen=True)
class _Layer:
    scope: Scope
    source: str
    version: str
    config: dict = field(repr=False)  # holds MCP env and headers in clear: never printed
    reason: str  # why it is not writable; empty for the user layer


def _codex_home(environment=None) -> Path:
    source = os.environ if environment is None else environment
    return Path(source.get("CODEX_HOME") or Path(source.get("HOME") or Path.home()) / ".codex")


def _name(entry: dict) -> dict:
    return entry.get("name") if isinstance(entry.get("name"), dict) else {}


def _layers(entries: list[dict]) -> list[_Layer]:
    """The layers that apply, strongest first; a project layer the CLI disabled is dropped.

    The 0.157.1 app-server cannot be started with a profile, so its user layer always has
    ``profile: null``. The schema allows a profile name, though: when one user-file layer is all
    there is, it is the owner's base ``config.toml``, however it is labelled.
    """
    lone = sum(_name(entry).get("type") == "user" for entry in entries) == 1
    return [_layer(entry, lone) for entry in entries if not entry.get("disabledReason")]


def _layer(entry: dict, lone_user_file: bool) -> _Layer:
    name = _name(entry)
    config = entry.get("config") if isinstance(entry.get("config"), dict) else {}
    raw_version = entry.get("version")
    version = raw_version if isinstance(raw_version, str) and raw_version else ""
    kind = name.get("type")
    if kind == "user" and (lone_user_file or not name.get("profile")):
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


def _skill_list(result: dict | None, home: Path, plugins: dict | None) -> dict[str, dict]:
    roots = [home / "skills"]
    for market in _listed(plugins, "marketplaces"):
        for plugin in _listed(market, "plugins"):
            source = plugin.get("source")
            if (
                isinstance(source, dict)
                and source.get("type") == "local"
                and isinstance(source.get("path"), str)
                and Path(source["path"]).is_absolute()
            ):
                roots.append(Path(source["path"]) / "skills")
    found = {}
    for group in _listed(result, "data"):
        cwd = Path(group["cwd"]) if isinstance(group.get("cwd"), str) else None
        project_roots = []
        if cwd is not None:
            try:
                cwd = cwd.resolve()
            except (OSError, RuntimeError):
                pass
            project_roots = [directory / ".agents" / "skills" for directory in (cwd, *cwd.parents)]
        locations = set()
        for root in roots + project_roots:
            try:
                locations.add(root.resolve())
            except (OSError, RuntimeError):
                pass
        # Use the longest canonical root known from home, project ancestry or plugin/list.
        # Only its relative components count as hidden; unknown collections stay unchanged.
        locations = sorted(locations, key=lambda root: len(root.parts), reverse=True)
        for skill in _listed(group, "skills"):
            if isinstance(skill.get("path"), str) and isinstance(skill.get("enabled"), bool):
                try:
                    path = Path(skill["path"]).resolve()
                except (OSError, RuntimeError):
                    found[skill["path"]] = skill
                    continue
                root = next((root for root in locations if path.is_relative_to(root)), None)
                if root is not None and any(
                    part.startswith(".") for part in path.relative_to(root).parts
                ):
                    continue
                found[skill["path"]] = skill
    return found


def _cli_version(binary: str, environment=None) -> str:
    try:
        done = run_state_command(
            [binary, "--version"],
            timeout=CALL_SECONDS,
            env=child_environment(environment),
        )
    except ProviderStateTimeoutError:
        if state_write_active():
            raise
        return ""
    except (OSError, subprocess.SubprocessError):
        return ""
    found = re.search(r"\d+\.\d+\.\d+\S*", done.stdout)
    return found.group(0) if found else ""


def _tested(version: str) -> bool:
    found = re.match(r"(\d+)\.(\d+)", version)
    return bool(found) and (int(found.group(1)), int(found.group(2))) == _TESTED


def _session_seconds(calls: int) -> float:
    """The whole app-server session: ``CALL_SECONDS`` for the start and for each request.

    The idle watchdog alone is reset by every notification, so a chatty server could outlast it.
    """
    return CALL_SECONDS * (calls + 1)


async def _ask(
    binary: str, requests: list[tuple[str, str, dict]], environment=None
) -> tuple[dict, dict]:
    """One app-server session: ``({key: result}, {key: why})`` for the requests in order."""
    results: dict[str, dict] = {}
    failures: dict[str, str] = {}
    command = [binary, "app-server", "--listen", "stdio://"]
    try:
        async with asyncio.timeout(state_write_remaining(_session_seconds(len(requests)))):
            async with connection(
                command,
                env=environment,
                config={"idle_timeout_seconds": CALL_SECONDS},
                kill_on_error=state_write_active(),
            ) as rpc:
                for key, method, params in requests:
                    try:
                        results[key] = await rpc.call(method, params)
                    except RPCError:
                        failures[key] = f"{method} was refused"
    except Exception as exc:  # start, framing or timeout: the rest of the session is unreadable
        if state_write_active() and (
            isinstance(exc, (TimeoutError, ProviderStateTimeoutError))
            or str(exc) == "provider_idle_timeout"
        ):
            raise ProviderStateTimeoutError("Codex state read timed out.") from None
        logger.debug("codex app-server session ended: %s", type(exc).__name__)
        for key, method, _ in requests:
            if key not in results and key not in failures:
                failures[key] = f"{method} is unavailable"
    return results, failures


async def _write(
    binary: str, method: str, params: dict, user_version: str | None, environment=None
) -> dict:
    """One write in its own app-server session.

    ``user_version`` is given for ``skills/config/write`` only (it has no ``expectedVersion``): the
    user layer is read again first and the write is refused when it is no longer the one the
    caller saw.
    """
    command = [binary, "app-server", "--listen", "stdio://"]
    try:
        async with asyncio.timeout(
            state_write_remaining(_session_seconds(1 if user_version is None else 2))
        ):
            async with connection(
                command,
                env=environment,
                config={"idle_timeout_seconds": CALL_SECONDS},
                kill_on_error=state_write_active(),
            ) as rpc:
                if user_version is not None:
                    try:
                        result = await rpc.call("config/read", {"includeLayers": True})
                    except RPCError as exc:
                        raise ProviderCommandError(
                            "Codex could not re-read its config before the skill write: "
                            + provider_message(exc.error)
                        ) from None
                    layers = _layers(_listed(result, "layers"))
                    if next((x.version for x in layers if x.scope == "user"), "") != user_version:
                        raise ProviderStateConflictError(
                            "The Codex config changed since it was read."
                        )
                return await rpc.call(method, params)
    except (TimeoutError, ProviderStateTimeoutError):
        if state_write_active():
            raise ProviderStateTimeoutError(
                "Codex state write timed out; the writer was stopped."
            ) from None
        raise ProviderCommandError(f"Codex did not complete {method}.") from None
    except (ProviderStateConflictError, ProviderCommandError):
        raise
    except RPCError as exc:
        raise _write_failure(method, exc.error) from None
    except Exception as exc:  # start, framing or timeout
        if state_write_active() and str(exc) == "provider_idle_timeout":
            raise ProviderStateTimeoutError(
                "Codex state write timed out; the writer was stopped."
            ) from None
        logger.debug("codex %s failed: %s", method, type(exc).__name__)
        raise ProviderCommandError(f"Codex did not complete {method}.") from None


def _write_failure(method: str, error: dict) -> Exception:
    data = error.get("data") if isinstance(error.get("data"), dict) else {}
    code = data.get("config_write_error_code")
    if code == "configVersionConflict":
        return ProviderStateConflictError("The Codex config changed since it was read.")
    if code == "configLayerReadonly":
        return ProviderStateUnsupportedError(f"Codex refused {method}: {provider_message(error)}")
    if error.get("code") == -32600 and str(error.get("message", "")).startswith(_UNKNOWN_METHOD):
        return ProviderStateUnsupportedError(f"This Codex does not support {method}.")
    return ProviderCommandError(provider_message(error))


class CodexStateAdapter:
    """The Codex view of the CLI's real state. Synchronous: async callers use ``to_thread``."""

    environment = None

    def __init__(self, *, environment=None):
        self.environment = dict(environment) if environment is not None else None

    def _environment(self):
        return {**os.environ, **self.environment} if self.environment is not None else None

    def _binary(self) -> str:
        binary = shutil.which("codex")
        if binary is None:
            raise ProviderStateSchemaError("the codex CLI is not on PATH")
        return binary

    def read_state(self, project_root: Path | None) -> StateSnapshot:
        return self._read(project_root)[0]

    def read_trust_state(self, project_root: Path) -> tuple[StateSnapshot, dict]:
        """Return one snapshot and the project-layer versions from that same CLI response."""
        layers = {}
        snapshot, _ = self._read(project_root, trust_layers=layers)
        return snapshot, layers

    def _read(
        self, project_root: Path | None, *, trust_layers=None, trust_checkpoint=None
    ) -> tuple[StateSnapshot, str]:
        """The snapshot and the user layer version (``""`` when the user layer was not read)."""
        binary = self._binary()
        environment = self._environment()
        cwd = (
            str(project_root)
            if project_root
            else str((environment or os.environ).get("HOME") or Path.home())
        )
        config_params = {"includeLayers": True, **({"cwd": cwd} if project_root else {})}
        results, failures = asyncio.run(
            _ask(
                binary,
                [
                    ("config", "config/read", config_params),
                    ("skills", "skills/list", {"cwds": [cwd], "forceReload": False}),
                    ("plugins", "plugin/list", {}),
                    ("apps", "app/list", {}),
                    ("hooks", "hooks/list", {"cwds": [cwd]}),
                ],
                environment=environment,
            )
        )
        if not results or (
            set(results) == {"hooks"}
            and not any(_listed(group, "hooks") for group in _listed(results.get("hooks"), "data"))
        ):
            raise ProviderStateSchemaError(
                "Codex state is not readable: " + "; ".join(failures.values())
            )
        warnings = [f"Codex: {why}; its rows are read-only." for why in failures.values()]

        if "config" in results and not isinstance(results["config"].get("layers"), list):
            failures["config"] = "config/read answered in an unexpected shape"
            warnings.append(f"Codex: {failures['config']}; its rows are read-only.")
        raw_layers = _listed(results.get("config"), "layers")
        if trust_layers is not None:
            trust_layers.update(
                {
                    str(_name(layer).get("dotCodexFolder")): {
                        "version": layer.get("version"),
                        "enabled": not layer.get("disabledReason"),
                    }
                    for layer in raw_layers
                    if _name(layer).get("type") == "project"
                }
            )
        layers = _layers(raw_layers)
        if trust_layers is not None:
            # Only these flags are decided by a config layer. Skills and list fallbacks
            # have separate sources, so config versions cannot attribute their changes.
            for kind, key in _KEYS.items():
                for ident, (layer, flag) in _decide(layers, key).items():
                    folder = str(Path(layer.source).parent)
                    if layer.scope == "project" and (flag is not None or kind == "mcp"):
                        trust_layers[folder].setdefault("items", {})[f"{kind}:{ident}"] = (
                            flag is not False
                        )
        if any(entry.get("disabledReason") for entry in raw_layers):
            warnings.append("Project config skipped: the project is not trusted by Codex.")
        user = next((layer for layer in layers if layer.scope == "user"), None)
        if trust_checkpoint is not None and user is not None:
            projects = user.config.get("projects", {})
            key = str(Path(project_root).resolve())
            entry = projects.get(key) if isinstance(projects, dict) else None
            trust_checkpoint.update(
                config=copy.deepcopy(user.config),
                exists=entry is not None,
                value=entry.get("trust_level") if isinstance(entry, dict) else None,
            )

        def row(kind, item_id, name, enabled, scope, source, reason):
            unavailable = [failures[key] for key in _READ_DEPENDENCIES[kind] if key in failures]
            locked = (
                f"Codex app-server did not answer the requests required for {kind} switches: "
                + "; ".join(unavailable)
                + f"; {kind} switches are read-only until it does."
                if unavailable
                else ""
            )
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

        def layered_rows(kind, key, listed, effective_layers=layers):
            decided = _decide(effective_layers, key)
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
        if trust_layers is not None:
            revoked_source = next(iter(trust_layers), None)
            without_project = [
                layer
                for layer in layers
                if layer.scope != "project" or str(Path(layer.source).parent) != revoked_source
            ]
            fallback_items = [
                *layered_rows(
                    "plugin", "plugins", _plugin_list(results.get("plugins")), without_project
                ),
                *layered_rows("app", "apps", _app_list(results.get("apps")), without_project),
                *layered_rows("mcp", "mcp_servers", {}, without_project),
            ]
            for layer in trust_layers.values():
                layer["fallbacks"] = {
                    item.id: {
                        "enabled": item.enabled,
                        "source": item.source.rsplit("/", 1)[-1],
                        "name": item.name,
                        "scope": item.scope,
                    }
                    for item in fallback_items
                    if item.id in layer.get("items", {})
                }
        for path, skill in _skill_list(
            results.get("skills"), _codex_home(self.environment), results.get("plugins")
        ).items():
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

        instructions = codex_instructions(
            (environment or os.environ).get("HOME") or Path.home(),
            _codex_home(self.environment),
            project_root,
            (results.get("config") or {})
            .get("config", {})
            .get("project_doc_fallback_filenames", []),
        )
        for entry in raw_layers:
            layer = _layer(entry, True)
            source = Path(layer.source)
            if source.is_absolute() and source.suffix == ".toml":
                rules = source.parent / "rules"
                instructions.paths.append(rules)
                for path in sorted(rules.glob("*.rules"))[:200]:
                    instructions.document(path, layer.scope)
        blocked_rule_roots = [
            Path(_name(entry)["dotCodexFolder"]) / "rules"
            for entry in raw_layers
            if entry.get("disabledReason")
            and _name(entry).get("type") == "project"
            and isinstance(_name(entry).get("dotCodexFolder"), str)
        ]
        project_trusted = bool(
            project_root
            and self._trust_details(results.get("config") or {}, project_root)["trusted"]
        )
        for index, item in enumerate(instructions.items):
            source = Path(item.source)
            if item.source.startswith("~/"):
                source = instructions.home / item.source[2:]
            elif not source.is_absolute() and project_root:
                source = Path(project_root) / source
            if item.scope == "project" and (
                not project_trusted
                or any(source.is_relative_to(folder) for folder in blocked_rule_roots)
            ):
                instructions.items[index] = replace(
                    item,
                    enabled=False,
                    details={
                        **item.details,
                        "project_trust": "pending project trust",
                        "status": "pending project trust"
                        if item.enabled
                        else item.details["status"],
                    },
                )
        items.extend(instructions.items)
        warnings.extend(instructions.warnings)
        digest.update(instructions.digest().encode())
        for group in _listed(results.get("hooks"), "data"):
            for error in _listed(group, "errors"):
                warnings.append(
                    "Codex hooks: "
                    + safe_text(error.get("path", ""))
                    + ": "
                    + safe_text(error.get("message", "Hook discovery failed."))
                )
            if isinstance(group.get("warnings"), list):
                warnings.extend(
                    "Codex hooks: " + safe_text(warning)
                    for warning in group["warnings"][:100]
                    if isinstance(warning, str)
                )
            for hook in _listed(group, "hooks"):
                details = safe_details(hook)
                trust = hook.get("trustStatus")
                details["status"] = (
                    "pending review"
                    if trust not in ("trusted", "managed")
                    else "enabled"
                    if hook.get("enabled")
                    else "disabled"
                )
                scope = (
                    "managed"
                    if hook.get("isManaged")
                    else "project"
                    if hook.get("source") == "project"
                    else "user"
                )
                details["event"] = details.get("eventName", "")
                items.append(
                    StateItem(
                        "hook:"
                        + hashlib.sha256(str(hook.get("key", hook)).encode()).hexdigest()[:20],
                        "hook",
                        safe_text(hook.get("eventName", "Hook")),
                        scope,
                        hook.get("enabled") is True and trust in ("trusted", "managed"),
                        safe_text(hook.get("sourcePath", "")),
                        False,
                        "Read-only; review hooks with Codex /hooks.",
                        details=details,
                    )
                )
        native_sources = {
            hook.get("sourcePath")
            for group in _listed(results.get("hooks"), "data")
            for hook in _listed(group, "hooks")
        }
        instructions.paths.extend(
            Path(source) for source in native_sources if isinstance(source, str)
        )
        disabled_project = any(
            entry.get("disabledReason") and _name(entry).get("type") == "project"
            for entry in raw_layers
        )
        hook_sources = [("user", _codex_home(self.environment))]
        if project_root:
            hook_sources.append(("project", Path(project_root) / ".codex"))
        for scope, folder in hook_sources:
            path = folder / "hooks.json"
            if str(path) in native_sources:
                continue
            raw = instructions.read(path)
            if raw:
                try:
                    document = json.loads(raw)
                except ValueError:
                    warnings.append("A hooks.json source could not be parsed.")
                    continue
                if isinstance(document, dict):
                    status = (
                        "pending project trust"
                        if scope == "project" and disabled_project
                        else "pending review"
                    )
                    items.extend(
                        hook_items(document, str(path), scope, enabled=False, status=status)
                    )
        for entry in raw_layers:
            layer = _layer(entry, True)
            if layer.source not in native_sources:
                status = (
                    "pending project trust" if entry.get("disabledReason") else "pending review"
                )
                items.extend(
                    hook_items(
                        layer.config, layer.source, layer.scope, enabled=False, status=status
                    )
                )
        self._orchestration_paths = tuple(instructions.paths)
        digest.update(instructions.digest().encode())
        digest.update(
            json.dumps(
                [item.details for item in items if item.kind == "hook"], sort_keys=True
            ).encode()
        )
        version = _cli_version(binary, environment)
        if not version:
            warnings.append("The Codex version could not be read.")
        elif not _tested(version):
            warnings.append(
                f"Codex {version} is outside the tested range {TESTED_VERSIONS}; "
                "reads may be incomplete."
            )
        snapshot = StateSnapshot(
            provider=PROVIDER,
            engine=ENGINE,
            project_root=str(project_root) if project_root else None,
            items=tuple(items),
            fingerprint=digest.hexdigest(),
            cli_version=version or "unknown",
            warnings=tuple(dict.fromkeys(warnings)),
        )
        return snapshot, user.version if user else ""

    def set_enabled(
        self,
        item_id: str,
        scope: Scope,
        enabled: bool,
        expected_fingerprint: str,
        *,
        project_root: Path | None = None,
    ) -> StateSnapshot:
        """Switch one plugin, skill, MCP server or app in the user config; return the fresh state.

        Synchronous (about three app-server sessions, each bounded as described above): async
        callers use ``asyncio.to_thread``. Allowed outside the tested versions, because the CLI
        validates its own config.
        """
        try:
            binary = self._binary()
            snapshot, user_version = self._read(project_root)
        except ProviderStateSchemaError as exc:
            raise ProviderStateUnsupportedError(f"Codex switches are unavailable: {exc}") from None
        if snapshot.fingerprint != expected_fingerprint:
            raise ProviderStateConflictError("The Codex state changed since it was read.")
        item = next((entry for entry in snapshot.items if entry.id == item_id), None)
        if item is None:
            raise ProviderStateUnsupportedError(f"Codex has no switchable item {item_id}.")
        if not item.writable or not user_version:
            raise ProviderStateUnsupportedError(item.reason or "The Codex config was not read.")
        if scope not in ("user", item.scope):
            raise ProviderStateUnsupportedError(
                f"Codex writes the user config, not the {scope} scope."
            )
        ident = item_id.partition(":")[2]
        if item.kind == "skill":  # the id came from skills/list a moment ago
            method, params, guard = (
                "skills/config/write",
                {"path": ident, "enabled": enabled},
                user_version,
            )
        elif _ADDRESSABLE.fullmatch(ident):
            edit = {
                "keyPath": f"{_KEYS[item.kind]}.{ident}.enabled",
                "value": enabled,
                "mergeStrategy": "upsert",
            }
            method, guard = "config/batchWrite", None
            params = {"edits": [edit], "expectedVersion": user_version}
        else:
            raise ProviderStateUnsupportedError(
                f"Codex cannot address {ident!r} by key path; edit config.toml by hand."
            )
        asyncio.run(_write(binary, method, params, guard, environment=self._environment()))
        fresh = self.read_state(project_root)
        confirmed = next((entry for entry in fresh.items if entry.id == item_id), None)
        if confirmed is None or confirmed.enabled is not enabled:
            raise ProviderStateConflictError("Codex does not show the requested value.")
        return fresh

    def watch_paths(self, project_root: Path | None) -> tuple[Path, ...]:
        home = _codex_home(self.environment)
        instructions = codex_instructions(
            (self.environment or os.environ).get("HOME") or Path.home(),
            home,
            project_root,
            read_content=False,
        )
        paths = (
            home / "config.toml",
            home / "skills",
            home / "hooks.json",
            *instructions.paths,
            *getattr(self, "_orchestration_paths", ()),
        )
        if project_root is None:
            return tuple(dict.fromkeys(paths))
        project = Path(project_root)
        return tuple(
            dict.fromkeys(
                (
                    *paths,
                    project / ".codex" / "config.toml",
                    project / ".agents" / "skills",
                    project / ".codex/hooks.json",
                )
            )
        )

    def source_identity_paths(self, project_root):
        return (_codex_home(self.environment), project_root)

    def is_project_trusted(self, project_root: Path) -> bool:
        from adapters.shared.provider_state import project_trusted

        return project_trusted(project_root, codex=self, environment=self.environment)

    def _is_project_trusted(self, project_root: Path) -> bool:
        return self.project_trust_details(project_root)["trusted"]

    def project_trust_details(self, project_root: Path, *, require_explicit: bool = False) -> dict:
        binary = shutil.which("codex")
        if binary is None:
            if require_explicit:
                raise ProviderStateSchemaError("Codex could not confirm the project trust write.")
            return {"trusted": False}
        root = Path(project_root).resolve()
        params = {"includeLayers": True, "cwd": str(root)}
        results, _ = asyncio.run(
            _ask(binary, [("config", "config/read", params)], environment=self._environment())
        )
        result = results.get("config") or {}
        return self._trust_details(result, project_root, require_explicit=require_explicit)

    @staticmethod
    def _trust_details(result: dict, project_root: Path, *, require_explicit: bool = False) -> dict:
        """Use the same native config answer for inventory and trust confirmation."""
        root = Path(project_root).resolve()
        layers = [
            layer for layer in _listed(result, "layers") if _name(layer).get("type") == "project"
        ]
        inherited = next(
            (
                str(Path(_name(layer)["dotCodexFolder"]).parent)
                for layer in layers
                if not layer.get("disabledReason")
                and _name(layer).get("dotCodexFolder")
                and Path(_name(layer)["dotCodexFolder"]).parent != root
            ),
            None,
        )
        details = {"inherited_from": inherited} if inherited else {}
        # The effective native key is authoritative even when the only config
        # layer belongs to a trusted ancestor rather than this project.
        config = result.get("config")
        projects = config.get("projects") if isinstance(config, dict) else None
        for key in (str(root), str(project_root)):
            table = projects.get(key) if isinstance(projects, dict) else None
            if isinstance(table, dict) and table.get("trust_level") in ("trusted", "untrusted"):
                return {"trusted": table["trust_level"] == "trusted", **details}
        if require_explicit:
            raise ProviderStateSchemaError(
                "Codex did not return an explicit project trust confirmation."
            )
        return {"trusted": bool(layers and not layers[0].get("disabledReason")), **details}

    def trust_project(
        self,
        project_root: Path,
        *,
        trusted: bool = True,
        on_written=None,
        expected_fingerprint=None,
        rollback=None,
    ) -> None:
        root = Path(project_root).resolve()
        binary = self._binary()
        checkpoint = {}
        snapshot, version = self._read(root, trust_checkpoint=checkpoint)
        if expected_fingerprint is not None and snapshot.fingerprint != expected_fingerprint:
            raise ProviderStateConflictError("The Codex state changed before accepting trust.")
        if not version:
            raise ProviderStateUnsupportedError("The Codex user config was not read.")
        # Native key paths escape quotes/backslashes only.
        quoted_root = str(root).replace("\\", "\\\\").replace('"', '\\"')
        key_path = f'projects."{quoted_root}".trust_level'
        undo = None
        if rollback is not None:
            expected_config = copy.deepcopy(checkpoint["config"])
            expected_config.setdefault("projects", {}).setdefault(str(root), {})["trust_level"] = (
                "trusted" if trusted else "untrusted"
            )
            undo = _TrustRollback(
                self,
                root,
                key_path if checkpoint.get("exists") else f'projects."{quoted_root}"',
                checkpoint.get("value"),
                version,
                expected_config,
            )
            rollback.append(undo)
        params = {
            "expectedVersion": version,
            "edits": [
                {
                    "keyPath": key_path,
                    "value": "trusted" if trusted else "untrusted",
                    "mergeStrategy": "upsert",
                }
            ],
        }
        result = asyncio.run(
            _write(binary, "config/batchWrite", params, None, environment=self._environment())
        )
        if undo is not None:
            written_version = result.get("version", "")
            if not written_version:
                raise ProviderStateConflictError(
                    "Codex did not confirm its written rollback version."
                )
            undo.written = written_version
        if on_written is not None:
            on_written()
        if self.project_trust_details(root, require_explicit=True)["trusted"] != trusted:
            raise ProviderStateConflictError("Codex does not show the requested trust.")

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
