"""Claude Code state reader (the read side of the provider-state seam, issue #40 package 1).

Reads the effective plugin, skill and MCP state from the files Claude Code itself uses, under
``$CLAUDE_CONFIG_DIR`` (default ``~/.claude``) and the project. Layers, strongest first: managed
settings, ``<project>/.claude/settings.local.json``, ``<project>/.claude/settings.json``, the user
``settings.json``.

Writes (``set_enabled``) go where Claude Code itself writes: plugins through ``claude plugin
enable|disable``, skills as ``skillOverrides.<name>`` in the chosen scope's settings file and MCP
servers as ``projects["<path>"].disabledMcpServers`` in ``.claude.json``, both through
``write_json_atomic``. Direct edits need a Claude Code version inside ``tested_versions``.

``.claude.json`` holds the sign-in session. Only ``mcpServers`` (top level and under
``projects["<path>"]``), the project's ``disabledMcpServers`` / ``enabledMcpServers`` and
``hasTrustDialogAccepted`` are looked at; nothing from it is logged, returned or put in a message.
MCP ``env``, headers and tokens never leave this module: a ``StateItem`` has no field for them.

``StateSnapshot.fingerprint`` is a sha256 over the bytes of every settings file, managed file and
``.mcp.json`` that was read, ``installed_plugins.json``, the skill folder listings, and only the
MCP keys of ``.claude.json`` as canonical JSON. Claude Code rewrites the session fields of that file
all the time, so hashing them would report false conflicts; the write-side sha256 still covers the
whole file.

Blocking: ``read_state`` runs ``claude --version`` (10 s limit) and reads files; async callers use
``asyncio.to_thread``.
"""

import functools
import hashlib
import json
import logging
import os
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from jsonschema import Draft7Validator

from adapters.shared.process import child_environment, provider_message, redact_paths
from adapters.shared.provider_state import (
    MISSING_FILE,
    CredentialRule,
    LoginStatus,
    ProviderCommandError,
    ProviderMcpDisabledError,
    ProviderStateConflictError,
    ProviderStateSchemaError,
    ProviderStateTimeoutError,
    ProviderStateUnsupportedError,
    ProviderStateVersionError,
    RunSetup,
    Scope,
    SecretStr,
    StateItem,
    StateSnapshot,
    TrustWriteRollback,
    claude_json_backup_dir,
    run_state_command,
    state_write_active,
    write_json_atomic,
)

logger = logging.getLogger(__name__)

TESTED_VERSIONS = ">=2.1.292,<2.2"
_TESTED_FROM, _TESTED_BELOW = (2, 1, 292), (2, 2)
MANAGED_SETTINGS_DIR = Path(
    "/etc/claude-code"
)  # Linux; https://code.claude.com/docs/en/managed-settings
SKILL_OVERRIDE_VALUES = frozenset({"on", "name-only", "user-invocable-only", "off"})
MANAGED_REASON = "Set by managed settings"
CHOOSE_PROJECT_REASON = "Choose a project"
OPEN_PROJECT_REASON = "Open this project in Claude Code once"
VERSION_TIMEOUT_SECONDS = 10
PLUGIN_TIMEOUT_SECONDS = 10
SETTINGS_SCHEMA = Path(__file__).parent / "schemas" / "claude-code-settings.schema.json"
_MCP_PROJECT_KEYS = (
    "mcpServers",
    "disabledMcpServers",
    "enabledMcpServers",
    "hasTrustDialogAccepted",
)


class _Unchanged(Exception):
    """Raised inside a ``change`` callback: the file already holds the requested value, write nothing."""


@dataclass(frozen=True)
class _Layer:
    scope: Scope
    shown: str
    data: dict


def _read_json_object(path: Path) -> tuple[dict | None, str | None, str]:
    """``(object, sha256 of the bytes, problem)``; problem is "", "missing", "invalid" or "unreadable".

    The digest is ``None`` for a missing file, so it can never be mistaken for ``MISSING_FILE``.
    """
    try:
        raw = path.read_bytes()
    except (FileNotFoundError, NotADirectoryError):
        return None, None, "missing"
    except OSError:
        return None, "unreadable", "unreadable"
    digest = hashlib.sha256(raw).hexdigest()
    try:
        data = json.loads(raw)
    except ValueError:  # the error text could quote the file, so it is dropped
        return None, digest, "invalid"
    return (data, digest, "") if isinstance(data, dict) else (None, digest, "invalid")


def _project_keys(document: dict | None, project_root: Path) -> list[str]:
    """The keys of ``projects`` that hold this project (as given, then resolved), strongest first."""
    projects = document.get("projects") if document else None
    if not isinstance(projects, dict):
        return []
    candidates = dict.fromkeys((str(project_root), str(project_root.resolve())))
    return [key for key in candidates if isinstance(projects.get(key), dict)]


def _project_key(document: dict | None, project_root: Path) -> str | None:
    """The key of ``projects`` used for this project: the as-given path wins over the resolved one."""
    keys = _project_keys(document, project_root)
    return keys[0] if keys else None


def _project_entry(document: dict | None, project_root: Path) -> dict:
    key = _project_key(document, project_root)
    return document["projects"][key] if key is not None else {}


def _tested(version: tuple[int, ...]) -> bool:
    return _TESTED_FROM <= version < _TESTED_BELOW


def _names(value) -> set[str]:
    return {name for name in value if isinstance(name, str)} if isinstance(value, list) else set()


def _server_names(value) -> list[str]:
    return [name for name in value if isinstance(name, str)] if isinstance(value, dict) else []


def _version_tuple(text: str) -> tuple[int, ...] | None:
    found = re.search(r"\d+\.\d+\.\d+", text)
    return tuple(int(part) for part in found.group().split(".")) if found else None


@functools.cache
def _settings_validator() -> Draft7Validator:
    return Draft7Validator(json.loads(SETTINGS_SCHEMA.read_text(encoding="utf-8")))


def _settings_errors(raw: bytes) -> list[str]:
    """Schema errors of a settings file as ``<path>: <keyword>``; never the offending value."""
    try:
        document = json.loads(raw)
    except ValueError:
        return ["not valid JSON"]
    return sorted(
        {
            f"/{'/'.join(map(str, error.absolute_path))}: {error.validator}"
            for error in _settings_validator().iter_errors(document)
        }
    )


def _disabled_servers_errors(project_root: Path) -> Callable[[bytes], list[str]]:
    def errors(raw: bytes) -> list[str]:
        try:
            document = json.loads(raw)
        except ValueError:
            return ["not valid JSON"]
        value = _project_entry(document, project_root).get("disabledMcpServers")
        if value is None or (isinstance(value, list) and all(isinstance(n, str) for n in value)):
            return []
        return ["disabledMcpServers is not a list of strings"]

    return errors


class _Reading:
    """One ``read_state`` pass: the warnings, the files that were tried and what feeds the fingerprint."""

    def __init__(self, project_root: Path | None, home: Path | None = None) -> None:
        self.project_root = project_root
        self.home = home if home is not None else Path.home()
        self.warnings: list[str] = []
        self.parts: list[tuple[str, str]] = []
        # sha256 of the bytes read per path; None for a missing file
        self.digests: dict[Path, str | None] = {}
        self.attempted = 0
        self.parsed = 0

    def warn(self, message: str) -> None:
        self.warnings.append(message)
        logger.debug("claude state: %s", message)

    def shown(self, path: Path) -> str:
        for base, prefix in ((self.project_root, ""), (self.home, "~/")):
            if base is not None:
                try:
                    return prefix + path.relative_to(base).as_posix()
                except ValueError:
                    continue
        return str(path)

    def load(self, path: Path, *, counts: bool = True, record: bool = True) -> dict | None:
        data, digest, problem = _read_json_object(path)
        self.digests[path] = digest
        if record:
            self.parts.append((self.shown(path), digest or "missing"))
        if problem in ("invalid", "unreadable"):
            reason = "is not valid JSON" if problem == "invalid" else "could not be read"
            self.warn(f"{self.shown(path)} {reason}; its items are not shown")
        if counts and problem != "missing":
            self.attempted += 1
            self.parsed += data is not None
        return data

    def digest(self) -> str:
        digest = hashlib.sha256()
        for label, value in self.parts:
            digest.update(f"{label}\0{value}\n".encode())
        return digest.hexdigest()


class ClaudeStateAdapter:
    provider = "claude"
    engine = "claude"
    tested_versions = TESTED_VERSIONS

    def __init__(
        self, state_dir: Path, managed_dir: Path = MANAGED_SETTINGS_DIR, *, environment=None
    ) -> None:
        self.state_dir = Path(state_dir)  # the write package keeps its backups here
        self.managed_dir = Path(managed_dir)
        self.environment = dict(environment) if environment is not None else None

    # Standalone adapters retain call-time lookup; the admin service pins owner locations.

    def _environment(self):
        return {**os.environ, **self.environment} if self.environment is not None else None

    def _config_dir(self) -> Path:
        source = os.environ if self.environment is None else self.environment
        configured = source.get("CLAUDE_CONFIG_DIR")
        return (
            Path(configured) if configured else Path(source.get("HOME") or Path.home()) / ".claude"
        )

    def _claude_json(self) -> Path:
        if self.environment is not None:
            return Path(self.environment["HOME"]) / ".claude.json"
        configured = os.environ.get("CLAUDE_CONFIG_DIR")
        return Path(configured, ".claude.json") if configured else Path.home() / ".claude.json"

    # --- reading ---

    def read_state(self, project_root: Path | None) -> StateSnapshot:
        return self._read(project_root)[0]

    def _read(self, project_root: Path | None) -> tuple[StateSnapshot, _Reading]:
        """The snapshot and the pass that made it (its ``digests`` are what a write must still find)."""
        root = Path(project_root) if project_root else None
        reading = _Reading(
            root, Path(self.environment["HOME"]) if self.environment is not None else None
        )
        version = self._version(reading)  # slow: it must not sit between reading and writing
        layers = self._settings_layers(reading)
        plugins = self._plugin_items(layers, reading)
        items = [
            *plugins.values(),
            *self._skill_items(layers, plugins, reading),
            *self._mcp_items(reading),
        ]
        if reading.attempted and not reading.parsed:
            raise ProviderStateSchemaError("no Claude Code state file could be read")
        snapshot = StateSnapshot(
            provider=self.provider,
            engine=self.engine,
            project_root=str(root) if root else None,
            items=tuple(items),
            fingerprint=reading.digest(),
            cli_version=version,
            warnings=tuple(reading.warnings),
        )
        return snapshot, reading

    def _settings_layers(self, reading: _Reading) -> list[_Layer]:
        """Readable settings files, strongest layer first."""
        root = reading.project_root
        drop_ins = sorted((self.managed_dir / "managed-settings.d").glob("*.json"))
        sources: list[tuple[Scope, Path]] = [
            *(("managed", path) for path in reversed(drop_ins)),
            ("managed", self.managed_dir / "managed-settings.json"),
        ]
        if root is not None:
            sources.append(("local", root / ".claude" / "settings.local.json"))
            sources.append(("project", root / ".claude" / "settings.json"))
        sources.append(("user", self._config_dir() / "settings.json"))
        layers = []
        for scope, path in sources:
            data = reading.load(path)
            if data is not None:
                layers.append(_Layer(scope, reading.shown(path), data))
        return layers

    @staticmethod
    def _decided(layers, key: str, accepts, reading: _Reading) -> dict:
        """``{name: (layer, value)}`` of the strongest layer that sets each entry of mapping ``key``."""
        decided = {}
        for layer in layers:
            mapping = layer.data.get(key)
            if mapping is None:
                continue
            if not isinstance(mapping, dict):
                reading.warn(f"{key} in {layer.shown} is not an object; ignored")
                continue
            for name, value in mapping.items():
                if accepts(value):
                    decided.setdefault(name, (layer, value))
                else:
                    reading.warn(
                        f"{key}[{name}] in {layer.shown} has an unsupported value; ignored"
                    )
        return decided

    def _plugin_items(self, layers, reading: _Reading) -> dict[str, StateItem]:
        decided = self._decided(layers, "enabledPlugins", lambda v: isinstance(v, bool), reading)
        return {
            plugin_id: StateItem(
                id=f"plugin:{plugin_id}",
                kind="plugin",
                name=plugin_id,
                scope=layer.scope,
                enabled=value,
                source=layer.shown,
                writable=layer.scope != "managed",
                reason=MANAGED_REASON if layer.scope == "managed" else "",
            )
            for plugin_id, (layer, value) in decided.items()
        }

    @staticmethod
    def _skill_names(folder: Path, reading: _Reading) -> list[str]:
        """Skill folders (with a SKILL.md) directly in ``folder`` that stay inside it."""
        try:
            root = folder.resolve(strict=True)
            entries = sorted(folder.iterdir())
        except (FileNotFoundError, NotADirectoryError):
            reading.parts.append((reading.shown(folder), "missing"))
            return []
        except OSError:
            reading.warn(f"{reading.shown(folder)} could not be read")
            return []
        names = []
        for entry in entries:
            if entry.name.startswith("."):
                continue
            try:
                inside = entry.resolve().is_relative_to(root)
            except (OSError, RuntimeError):  # a link loop
                inside = False
            if not inside:
                reading.warn(f"skill {entry.name} skipped: its link leaves {reading.shown(folder)}")
            elif (entry / "SKILL.md").is_file():
                names.append(entry.name)
        reading.parts.append((reading.shown(folder), ",".join(names)))
        return names

    def _skill_items(
        self, layers, plugins: dict[str, StateItem], reading: _Reading
    ) -> list[StateItem]:
        overrides = self._decided(
            layers,
            "skillOverrides",
            lambda v: isinstance(v, str) and v in SKILL_OVERRIDE_VALUES,
            reading,
        )
        folders: list[tuple[Scope, Path]] = [("user", self._config_dir() / "skills")]
        if reading.project_root is not None:
            folders.append(("project", reading.project_root / ".claude" / "skills"))
        found: dict[str, tuple[Scope, Path]] = {}
        for scope, folder in folders:  # the user's own skill shadows a project one of the same name
            for name in self._skill_names(folder, reading):
                if name in found:
                    reading.warn(
                        f"skill {name} in {reading.shown(folder)} is shadowed by the user skill"
                    )
                else:
                    found[name] = (scope, folder / name)
        items = []
        for name, (scope, path) in found.items():
            layer, value = overrides.get(name, (None, "on"))
            managed = layer is not None and layer.scope == "managed"
            notes = [MANAGED_REASON] if managed else []
            if value not in ("on", "off"):
                notes.append(f"Skill override: {value}")
            items.append(
                StateItem(
                    id=f"skill:{name}",
                    kind="skill",
                    name=name,
                    scope=layer.scope if layer else scope,
                    enabled=value != "off",
                    source=layer.shown if layer else reading.shown(path / "SKILL.md"),
                    writable=not managed,
                    reason="; ".join(notes),
                )
            )
        return items + self._plugin_skill_items(plugins, reading)

    def _plugin_skill_items(
        self, plugins: dict[str, StateItem], reading: _Reading
    ) -> list[StateItem]:
        # UNVERIFIED layout of installed_plugins.json: {"plugins": {"<id>": [{"installPath": ...}]}}
        path = self._config_dir() / "plugins" / "installed_plugins.json"
        data = reading.load(path, counts=False)
        if data is None:
            return []
        installed = data.get("plugins")
        if not isinstance(installed, dict):
            reading.warn(
                f"{reading.shown(path)} has a layout that is not known; plugin skills are not listed"
            )
            return []
        config_root = self._config_dir().resolve()
        items, listed = [], set()
        for plugin_id, entries in installed.items():
            for entry in entries if isinstance(entries, list) else []:
                install = entry.get("installPath") if isinstance(entry, dict) else None
                if not isinstance(install, str) or not Path(install).resolve().is_relative_to(
                    config_root
                ):
                    reading.warn(
                        f"plugin {plugin_id} skipped: its install path is unknown or outside {reading.shown(self._config_dir())}"
                    )
                    continue
                plugin = plugins.get(plugin_id)
                for skill in self._skill_names(Path(install) / "skills", reading):
                    skill_id = f"skill:{plugin_id.split('@')[0]}:{skill}"
                    if skill_id in listed:
                        continue
                    listed.add(skill_id)
                    items.append(
                        StateItem(
                            id=skill_id,
                            kind="skill",
                            name=skill_id[len("skill:") :],
                            scope=plugin.scope if plugin else "user",
                            enabled=plugin.enabled if plugin else False,
                            source=plugin.source if plugin else reading.shown(path),
                            writable=False,
                            reason=f"Part of plugin {plugin_id}",
                        )
                    )
        return items

    def _mcp_items(self, reading: _Reading) -> list[StateItem]:
        claude_json = self._claude_json()
        document = reading.load(claude_json, record=False)
        root = reading.project_root
        entry = _project_entry(document, root) if root is not None else {}
        known = root is not None and _project_key(document, root) is not None
        no_entry = OPEN_PROJECT_REASON
        if reading.digests[claude_json] is None:
            no_entry = f"{claude_json.name} does not exist; {no_entry[0].lower()}{no_entry[1:]}"
        if root is not None and len(_project_keys(document, root)) > 1:
            reading.warn(
                f"{reading.shown(claude_json)} has entries for both the given and the resolved "
                "path of this project; the given one is used"
            )
        top = document.get("mcpServers") if document else None
        # The CLI rewrites the session fields of this file constantly: only MCP keys feed the fingerprint.
        subset = {"mcpServers": top, "project": {key: entry.get(key) for key in _MCP_PROJECT_KEYS}}
        reading.parts.append(
            (
                reading.shown(claude_json),
                hashlib.sha256(json.dumps(subset, sort_keys=True).encode()).hexdigest(),
            )
        )
        project_servers = {}
        if root is not None:
            mcp_json = reading.load(root / ".mcp.json")
            project_servers = _server_names((mcp_json or {}).get("mcpServers"))
        # Weakest first, so the strongest definition overwrites: user < project < local.
        defined: dict[str, tuple[Scope, str]] = {}
        for scope, names, where in (
            ("user", _server_names(top), reading.shown(claude_json)),
            ("project", project_servers, ".mcp.json"),
            ("local", _server_names(entry.get("mcpServers")), reading.shown(claude_json)),
        ):
            defined.update({name: (scope, where) for name in names})
        disabled = _names(entry.get("disabledMcpServers"))
        return [
            StateItem(
                id=f"mcp:{name}",
                kind="mcp",
                name=name,
                scope=scope,
                enabled=root is None or name not in disabled,
                source=where,
                writable=known,
                reason="" if known else (no_entry if root is not None else CHOOSE_PROJECT_REASON),
            )
            for name, (scope, where) in defined.items()
        ]

    def _version(self, reading: _Reading) -> str:
        try:
            done = run_state_command(
                ["claude", "--version"],
                timeout=VERSION_TIMEOUT_SECONDS,
                env=child_environment(self._environment()),
            )
            version = _version_tuple(done.stdout) if done.returncode == 0 else None
        except ProviderStateTimeoutError:
            if state_write_active():
                raise
            version = None
        except (OSError, subprocess.TimeoutExpired):
            version = None
        if version is None:
            reading.warn("the Claude Code version could not be read")
            return ""
        text = ".".join(map(str, version))
        if not _tested(version):
            reading.warn(f"Claude Code {text} is outside the tested range {TESTED_VERSIONS}")
        return text

    # --- change detection and trust ---

    def watch_paths(self, project_root: Path | None) -> tuple[Path, ...]:
        config_dir = self._config_dir()
        paths = [
            config_dir / "settings.json",
            self._claude_json(),
            config_dir / "plugins" / "installed_plugins.json",  # UNVERIFIED path
            config_dir / "skills",
        ]
        if project_root is not None:
            folder = Path(project_root)
            paths += [
                folder / ".claude" / "settings.json",
                folder / ".claude" / "settings.local.json",
                folder / ".mcp.json",
                folder / ".claude" / "skills",
            ]
        paths += [
            self.managed_dir / "managed-settings.json",
            self.managed_dir / "managed-settings.d",
        ]
        return tuple(paths)

    def is_project_trusted(self, project_root: Path) -> bool:
        from adapters.shared.provider_state import project_trusted

        return project_trusted(project_root, claude=self, environment=self.environment)

    def _is_project_trusted(self, project_root: Path) -> bool:
        document, _, _ = _read_json_object(self._claude_json())
        return _project_entry(document, Path(project_root)).get("hasTrustDialogAccepted") is True

    # --- writing ---

    def set_enabled(
        self,
        item_id: str,
        scope: Scope,
        enabled: bool,
        expected_fingerprint: str,
        *,
        project_root: Path | None = None,
    ) -> StateSnapshot:
        """Turn a plugin, skill or MCP server on or off in ``scope``; the fresh snapshot.

        Plugins go through ``claude plugin enable|disable``. A skill is ``skillOverrides.<name>`` in
        the settings file of ``scope`` (``on`` or ``off``; ``on`` is written explicitly, because
        removing the key would let a weaker layer's ``off`` win again, and it only ever replaces
        ``off`` or a missing key: ``name-only`` and the like stay). An MCP server is an entry of
        ``projects["<project_root>"].disabledMcpServers`` in ``.claude.json`` whatever ``scope`` is;
        that file is only edited, never created, and a project with no entry there is refused (open it
        in Claude Code once). A request for the state a switch is already in writes nothing.
        Direct edits (skills, MCP) are refused outside ``tested_versions``.

        The write is checked against the sha256 of the bytes the fingerprint was made from. For
        ``.claude.json`` that is the whole file, session fields included, so Claude Code rewriting
        its own session fields between that read and the write is a ``ProviderStateConflictError``
        too; the window is a few milliseconds (the slow version check runs before any file is
        read) and the caller can read again and retry.

        When the value was written but a stronger layer still decides another one, the returned
        snapshot says so in a warning (deciding scope and file, never content); it is no error.

        Synchronous and blocking (files, ``claude --version``, the plugin command, 10 s limits):
        async callers use ``asyncio.to_thread``.
        """
        root = Path(project_root) if project_root else None
        kind, _, name = item_id.partition(":")
        if kind not in ("plugin", "skill", "mcp") or not name or name.startswith("-"):
            raise ProviderStateUnsupportedError(f"{item_id} has no writer")
        if scope in ("managed", "profile"):
            raise ProviderStateUnsupportedError(f"the {scope} layer cannot be written")
        snapshot, reading = self._read(root)
        if snapshot.fingerprint != expected_fingerprint:
            raise ProviderStateConflictError("the Claude Code state changed since it was read")
        item = next((found for found in snapshot.items if found.id == item_id), None)
        if item is not None and not item.writable:
            raise ProviderStateUnsupportedError(item.reason or f"{item_id} is read-only")
        if kind == "plugin":
            self._set_plugin(name, scope, enabled, root)
        else:
            if item is None:
                raise ProviderStateUnsupportedError(f"{item_id} is not known")
            version = _version_tuple(snapshot.cli_version)
            if version is None or not _tested(version):
                raise ProviderStateVersionError(
                    f"Claude Code {snapshot.cli_version or 'of unknown version'} is outside "
                    f"{TESTED_VERSIONS}; {kind} switches are not written"
                )
            if kind == "skill":
                self._set_skill(name, scope, enabled, item.enabled, root, reading.digests)
            else:
                self._set_mcp(name, enabled, root, reading.digests)
        return self._explain_override(self.read_state(root), item_id, enabled)

    @staticmethod
    def _explain_override(snapshot: StateSnapshot, item_id: str, enabled: bool) -> StateSnapshot:
        """Add a warning when the write stuck but a stronger layer still decides another value."""
        item = next((found for found in snapshot.items if found.id == item_id), None)
        if item is None or item.enabled == enabled:
            return snapshot
        state = "enabled" if item.enabled else "disabled"
        note = f"{item.name} stays {state}: the {item.scope} setting in {item.source} decides"
        return replace(snapshot, warnings=(*snapshot.warnings, note))

    def _settings_path(self, scope: Scope, root: Path | None) -> Path:
        if scope == "user":
            return self._config_dir() / "settings.json"
        if scope not in ("project", "local") or root is None:
            raise ProviderStateUnsupportedError(f"the {scope} layer needs a project to write")
        return root / ".claude" / ("settings.json" if scope == "project" else "settings.local.json")

    @staticmethod
    def _confirm(found, expected) -> None:
        if found != expected:
            raise ProviderStateConflictError("the change did not stick; Claude Code state moved")

    def _set_plugin(self, plugin_id: str, scope: Scope, enabled: bool, root: Path | None) -> None:
        path = self._settings_path(scope, root)
        verb = "enable" if enabled else "disable"
        try:
            done = subprocess.run(
                ["claude", "plugin", verb, plugin_id, "-s", scope, "--json"],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=PLUGIN_TIMEOUT_SECONDS,
                env=child_environment(self._environment()),
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise ProviderCommandError(f"claude plugin {verb} timed out") from None
        except OSError:
            raise ProviderCommandError(f"claude plugin {verb} could not run") from None
        if done.returncode != 0:
            try:
                reported = json.loads(done.stdout)["message"]
            except (ValueError, KeyError, TypeError):
                reported = done.stdout or done.stderr
            raise ProviderCommandError(
                f"claude plugin {verb} failed: " + provider_message(redact_paths(str(reported))),
                exit_code=done.returncode,
            )
        document, _, _ = _read_json_object(path)
        plugins = (document or {}).get("enabledPlugins")
        self._confirm(plugins.get(plugin_id) if isinstance(plugins, dict) else None, enabled)

    def _set_skill(
        self,
        name: str,
        scope: Scope,
        enabled: bool,
        effective: bool,
        root: Path | None,
        digests: dict,
    ) -> None:
        path = self._settings_path(scope, root)
        value = "on" if enabled else "off"

        def change(document: dict) -> dict:
            overrides = document.setdefault("skillOverrides", {})
            if not isinstance(overrides, dict):
                raise ProviderStateSchemaError(f"skillOverrides in {path.name} is not an object")
            current = overrides.get(name)
            if enabled and (current not in (None, "off") or (current is None and effective)):
                raise _Unchanged  # already on here or through another layer; restricted stays
            if not enabled and current == "off":
                raise _Unchanged
            overrides[name] = value
            return document

        # The digest of the bytes the fingerprint covered: a change since then is a conflict.
        # An unreadable file is never treated as missing: create mode would hide it.
        digest = digests[path]
        expected = MISSING_FILE if digest is None else digest
        try:
            write_json_atomic(path, change, expected, validate=_settings_errors)
        except _Unchanged:
            return
        document, _, _ = _read_json_object(path)
        overrides = (document or {}).get("skillOverrides")
        self._confirm(overrides.get(name) if isinstance(overrides, dict) else None, value)

    def _set_mcp(self, name: str, enabled: bool, root: Path, digests: dict) -> None:
        path = self._claude_json()
        digest = digests[path]  # of the whole file as read; see set_enabled
        if digest is None:
            raise ProviderStateUnsupportedError(".claude.json does not exist; it is not created")

        def change(document: dict) -> dict:
            key = _project_key(document, root)
            if key is None:  # never invented: Claude Code creates its own entry
                raise ProviderStateUnsupportedError(OPEN_PROJECT_REASON)
            entry = document["projects"][key]
            listed = entry.get("disabledMcpServers", [])
            if not isinstance(listed, list):
                raise ProviderStateSchemaError(".claude.json does not have the expected layout")
            if (name in listed) == (not enabled):
                raise _Unchanged  # already so: no rewrite, no backup, no empty key
            kept = [server for server in listed if server != name]
            entry["disabledMcpServers"] = kept if enabled else [*kept, name]
            return document

        try:
            write_json_atomic(
                path,
                change,
                digest,
                backup_dir=claude_json_backup_dir(self.state_dir),
                validate=_disabled_servers_errors(root),
            )
        except _Unchanged:
            return
        document, _, _ = _read_json_object(path)
        disabled = _project_entry(document, root).get("disabledMcpServers")
        self._confirm(name in disabled if isinstance(disabled, list) else None, not enabled)

    def _check_write_version(self, root: Path) -> None:
        version = self._version(_Reading(root))
        if not _tested(_version_tuple(version) or ()):
            raise ProviderStateVersionError(
                "Claude Code is outside the tested range; state is not written"
            )

    def trust_project(self, project_root: Path, *, trusted: bool = True, rollback=None) -> None:
        root = Path(project_root).resolve()
        self._check_write_version(root)
        path = self._claude_json()
        _, digest, problem = _read_json_object(path)
        if problem and problem != "missing":
            raise ProviderStateSchemaError("Claude trust state is unreadable")

        undo = None
        if rollback is not None:
            undo = TrustWriteRollback.capture(path, digest if digest is not None else MISSING_FILE)
            rollback.append(undo)

        def change(document):
            projects = document.setdefault("projects", {})
            if not isinstance(projects, dict):
                raise ProviderStateSchemaError("Claude projects is not an object")
            entry = projects.setdefault(str(root), {})
            if not isinstance(entry, dict):
                raise ProviderStateSchemaError("Claude project is not an object")
            entry["hasTrustDialogAccepted"] = trusted
            return document

        written = write_json_atomic(
            path,
            change,
            digest if digest is not None else MISSING_FILE,
            backup_dir=claude_json_backup_dir(self.state_dir),
            validate=lambda raw: (
                []
                if isinstance(json.loads(raw).get("projects"), dict)
                else ["projects must be an object"]
            ),
        )
        if undo is not None:
            undo.written = written
        self._confirm(self._is_project_trusted(root), trusted)

    def project_servers(self, project_root: Path) -> dict:
        document, _, problem = _read_json_object(Path(project_root) / ".mcp.json")
        if problem not in ("", "missing"):
            raise ProviderStateSchemaError("Project MCP state is unreadable")
        servers = (document or {}).get("mcpServers", {})
        if not isinstance(servers, dict):
            raise ProviderStateSchemaError("Project MCP servers is not an object")
        return servers

    def enabled_project_servers(self, project_root: Path) -> frozenset[str]:
        """The owner's native MCP opt-out still applies to explicitly injected definitions."""
        document, _, problem = _read_json_object(self._claude_json())
        if problem not in ("", "missing"):
            raise ProviderStateSchemaError("Claude MCP switch state is unreadable")
        disabled = _project_entry(document, Path(project_root)).get("disabledMcpServers", [])
        if not isinstance(disabled, list) or any(not isinstance(name, str) for name in disabled):
            raise ProviderStateSchemaError("Claude MCP disabled list is invalid")
        return frozenset(self.project_servers(project_root).keys() - set(disabled))

    def approved_project_servers(
        self, project_root: Path, *, trusted: bool | None = None
    ) -> frozenset[str]:
        if trusted is None:
            trusted = self.is_project_trusted(project_root)
        if not trusted:
            return frozenset()
        servers = set(self.project_servers(project_root))
        reading = _Reading(Path(project_root))
        layers = self._settings_layers(reading)
        if reading.warnings:
            raise ProviderStateSchemaError("Project MCP approval settings are unreadable")
        approved, denied = set(), set()
        for layer in layers:
            for key in ("enabledMcpjsonServers", "disabledMcpjsonServers"):
                value = layer.data.get(key, [])
                if not isinstance(value, list) or any(not isinstance(name, str) for name in value):
                    raise ProviderStateSchemaError("Project MCP approval list is invalid")
            approved.update(_names(layer.data.get("enabledMcpjsonServers")))
            denied.update(_names(layer.data.get("disabledMcpjsonServers")))
            if layer.data.get("enableAllProjectMcpServers") is True:
                approved.update(servers)
        return frozenset((approved & servers) - denied)

    def set_project_server_approval(self, project_root: Path, server: str, approved: bool) -> None:
        root = Path(project_root)
        self._check_write_version(root)
        if server not in self.project_servers(root):
            raise ProviderStateUnsupportedError("The project MCP server is unknown")
        if server not in self.enabled_project_servers(root):
            raise ProviderMcpDisabledError(
                "This MCP server is disabled by owner; enable it before changing approval."
            )
        path = self._settings_path("local", root)
        _, digest, problem = _read_json_object(path)
        if problem not in ("", "missing"):
            raise ProviderStateSchemaError("Project approval settings are unreadable")
        path.parent.mkdir(mode=0o700, exist_ok=True)

        def change(document):
            for key, include in (
                ("enabledMcpjsonServers", approved),
                ("disabledMcpjsonServers", not approved),
            ):
                names = document.get(key, [])
                if not isinstance(names, list) or any(not isinstance(name, str) for name in names):
                    raise ProviderStateSchemaError("Project MCP approval list is invalid")
                kept = [name for name in names if name != server]
                document[key] = [*kept, server] if include else kept
            return document

        write_json_atomic(
            path, change, digest if digest is not None else MISSING_FILE, validate=_settings_errors
        )

    # --- later run-home migration and sign-in packages ---

    def run_environment(self, project_root: Path, trusted: bool, permission_flags) -> RunSetup:
        raise ProviderStateUnsupportedError("the run setup lands with the runs issue (plan item 6)")

    def credential_isolation(self) -> CredentialRule | None:
        return None

    def login_command(self, headless: bool) -> list[str]:
        raise ProviderStateUnsupportedError("sign-in lands with the sign-in issue (plan item 7)")

    def login_status(self) -> LoginStatus:
        raise ProviderStateUnsupportedError("sign-in lands with the sign-in issue (plan item 7)")

    def set_api_key(self, secret: SecretStr) -> None:
        raise ProviderStateUnsupportedError(
            "Claude uses the CLI's own sign-in, not an API key file"
        )
