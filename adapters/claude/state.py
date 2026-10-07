"""Claude Code state reader (the read side of the provider-state seam, issue #40 package 1).

Reads the effective plugin, skill and MCP state from the files Claude Code itself uses, under
``$CLAUDE_CONFIG_DIR`` (default ``~/.claude``) and the project. Layers, strongest first: managed
settings, ``<project>/.claude/settings.local.json``, ``<project>/.claude/settings.json``, the user
``settings.json``. Writes are the next package; ``set_enabled`` is a stub until then.

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

import hashlib
import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

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

TESTED_VERSIONS = ">=2.1.292,<2.2"
_TESTED_FROM, _TESTED_BELOW = (2, 1, 292), (2, 2)
MANAGED_SETTINGS_DIR = Path(
    "/etc/claude-code"
)  # Linux; https://code.claude.com/docs/en/managed-settings
SKILL_OVERRIDE_VALUES = frozenset({"on", "name-only", "user-invocable-only", "off"})
MANAGED_REASON = "Set by managed settings"
CHOOSE_PROJECT_REASON = "Choose a project"
VERSION_TIMEOUT_SECONDS = 10
_MCP_PROJECT_KEYS = ("mcpServers", "disabledMcpServers", "enabledMcpServers")


@dataclass(frozen=True)
class _Layer:
    scope: Scope
    shown: str
    data: dict


def _read_json_object(path: Path) -> tuple[dict | None, str, str]:
    """``(object, sha256 of the bytes, problem)``; problem is "", "missing", "invalid" or "unreadable"."""
    try:
        raw = path.read_bytes()
    except (FileNotFoundError, NotADirectoryError):
        return None, "missing", "missing"
    except OSError:
        return None, "unreadable", "unreadable"
    digest = hashlib.sha256(raw).hexdigest()
    try:
        data = json.loads(raw)
    except ValueError:  # the error text could quote the file, so it is dropped
        return None, digest, "invalid"
    return (data, digest, "") if isinstance(data, dict) else (None, digest, "invalid")


def _project_entry(document: dict | None, project_root: Path) -> dict:
    projects = document.get("projects") if document else None
    if isinstance(projects, dict):
        for key in dict.fromkeys((str(project_root), str(project_root.resolve()))):
            if isinstance(projects.get(key), dict):
                return projects[key]
    return {}


def _names(value) -> set[str]:
    return {name for name in value if isinstance(name, str)} if isinstance(value, list) else set()


def _server_names(value) -> list[str]:
    return [name for name in value if isinstance(name, str)] if isinstance(value, dict) else []


def _version_tuple(text: str) -> tuple[int, ...] | None:
    found = re.search(r"\d+\.\d+\.\d+", text)
    return tuple(int(part) for part in found.group().split(".")) if found else None


class _Reading:
    """One ``read_state`` pass: the warnings, the files that were tried and what feeds the fingerprint."""

    def __init__(self, project_root: Path | None) -> None:
        self.project_root = project_root
        self.warnings: list[str] = []
        self.parts: list[tuple[str, str]] = []
        self.attempted = 0
        self.parsed = 0

    def warn(self, message: str) -> None:
        self.warnings.append(message)
        logger.debug("claude state: %s", message)

    def shown(self, path: Path) -> str:
        for base, prefix in ((self.project_root, ""), (Path.home(), "~/")):
            if base is not None:
                try:
                    return prefix + path.relative_to(base).as_posix()
                except ValueError:
                    continue
        return str(path)

    def load(self, path: Path, *, counts: bool = True, record: bool = True) -> dict | None:
        data, digest, problem = _read_json_object(path)
        if record:
            self.parts.append((self.shown(path), digest))
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

    def __init__(self, state_dir: Path, managed_dir: Path = MANAGED_SETTINGS_DIR) -> None:
        self.state_dir = Path(state_dir)  # the write package keeps its backups here
        self.managed_dir = Path(managed_dir)

    # --- locations (read at call time: the environment may change under a long-lived adapter) ---

    @staticmethod
    def _config_dir() -> Path:
        configured = os.environ.get("CLAUDE_CONFIG_DIR")
        return Path(configured) if configured else Path.home() / ".claude"

    @staticmethod
    def _claude_json() -> Path:
        configured = os.environ.get("CLAUDE_CONFIG_DIR")
        return Path(configured, ".claude.json") if configured else Path.home() / ".claude.json"

    # --- reading ---

    def read_state(self, project_root: Path | None) -> StateSnapshot:
        root = Path(project_root) if project_root else None
        reading = _Reading(root)
        layers = self._settings_layers(reading)
        plugins = self._plugin_items(layers, reading)
        items = [
            *plugins.values(),
            *self._skill_items(layers, plugins, reading),
            *self._mcp_items(reading),
        ]
        if reading.attempted and not reading.parsed:
            raise ProviderStateSchemaError("no Claude Code state file could be read")
        version = self._version(reading)
        return StateSnapshot(
            provider=self.provider,
            engine=self.engine,
            project_root=str(root) if root else None,
            items=tuple(items),
            fingerprint=reading.digest(),
            cli_version=version,
            warnings=tuple(reading.warnings),
        )

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
                    source=layer.shown if layer else reading.shown(path),
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
                writable=root is not None,
                reason="" if root is not None else CHOOSE_PROJECT_REASON,
            )
            for name, (scope, where) in defined.items()
        ]

    def _version(self, reading: _Reading) -> str:
        try:
            done = subprocess.run(
                ["claude", "--version"],
                capture_output=True,
                text=True,
                timeout=VERSION_TIMEOUT_SECONDS,
                env=child_environment(),
                check=False,
            )
            version = _version_tuple(done.stdout) if done.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired):
            version = None
        if version is None:
            reading.warn("the Claude Code version could not be read")
            return ""
        text = ".".join(map(str, version))
        if not _TESTED_FROM <= version < _TESTED_BELOW:
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
        return tuple(paths)

    def is_project_trusted(self, project_root: Path) -> bool:
        document, _, _ = _read_json_object(self._claude_json())
        return _project_entry(document, Path(project_root)).get("hasTrustDialogAccepted") is True

    # --- not in this package ---

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

    def trust_project(self, project_root: Path) -> None:
        raise ProviderStateUnsupportedError("writing trust lands with issue #44")

    def approved_project_servers(self, project_root: Path) -> frozenset[str]:
        raise ProviderStateUnsupportedError("project MCP approval lands with issue #44")

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
