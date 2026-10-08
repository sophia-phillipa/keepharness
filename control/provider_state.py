"""Provider state for the admin panel: one service over the Codex and Claude Code adapters.

The adapters block (CLI sessions, file writes), so every call runs in a thread under one
``asyncio.Lock`` per provider. Bodies carry snapshot fields and adapter-authored messages
only (at most ``MESSAGE_LIMIT`` characters); logs carry error codes, never messages.

Changes made outside KeepHarness (issue #43): ``<state>/provider-state-seen.json`` keeps, per
provider and project, the stat fingerprint and the last-seen ``{enabled, source, name}`` of every
item, the last write KeepHarness made to each item and the pending notices. A read compares the
cheap stat fingerprint first and only diffs the items when it moved. The harness stats the same
paths at run start and records only ``{stat, detected_at}`` in ``provider-state-runs.json``.
Neither file holds credentials: names, ids, flags and source labels only.
"""

import asyncio
import dataclasses
import hashlib
import json
import logging
import os
import threading
import time
from collections.abc import Callable, Mapping
from functools import partial
from pathlib import Path

from starlette.responses import JSONResponse

from adapters.claude.state import ClaudeStateAdapter
from adapters.codex.state import CodexStateAdapter
from adapters.deepseek.state import DeepSeekStateAdapter
from adapters.shared.process import redact_paths
from adapters.shared.provider_state import (
    ProviderCommandError,
    ProviderStateAdapter,
    ProviderStateConflictError,
    ProviderStateSchemaError,
    ProviderTrustRollbackError,
    StateSnapshot,
    _ProviderStateError,
    fingerprint,
    state_write_deadline,
)
from agent_service.errors import APIError

from .persistence import ControlStateRepository

SECURITY_WRITE_SECONDS = 15.0
PROVIDERS = ("codex", "claude", "deepseek")
COALESCE_SECONDS = 5.0
MESSAGE_LIMIT = 300
NO_PROJECT = "sem-projeto"
SEEN_FILE = "provider-state-seen.json"
RUNS_FILE = "provider-state-runs.json"
FILE_VERSION = 1
NOTICE_CAP = 50

logger = logging.getLogger(__name__)
_runs_lock = threading.Lock()  # runs share one thread pool and one runs file


def _owner_environment(
    state: Path, provider: str | None = None, *, source: Mapping[str, str] | None = None
) -> dict[str, str]:
    """Pin the owner's locations; the 0.15 run homes never belong to this facade.

    Resolve directory aliases for containment only, without reading CLI file contents.
    Keep accepted custom path spellings, discard harness-owned locations, and report
    unavailable owner directories only when their provider's state is requested.
    """
    source = os.environ if source is None else source
    try:
        harness = (Path(state) / "providers").resolve()
    except (OSError, RuntimeError):
        raise ProviderStateSchemaError("The provider state directory cannot be resolved.") from None

    def owner_path(value: str) -> bool:
        try:
            path = Path(value).resolve()
            if path.is_relative_to(harness):
                return False
            if path.exists() and not os.access(path, os.R_OK | os.X_OK):
                raise PermissionError
            return True
        except (OSError, RuntimeError):
            raise ProviderStateSchemaError(
                "The owner CLI directory is unavailable for provider state."
            ) from None

    home = source.get("HOME")
    if not home or not owner_path(home):
        if os.name == "posix":
            import pwd

            home = pwd.getpwuid(os.getuid()).pw_dir
        else:
            home = source.get("USERPROFILE") or str(Path.home())
    if not owner_path(home):
        raise ProviderStateSchemaError("The owner home is unavailable for provider state.")
    result = {"HOME": home}
    locations = {"codex": ("CODEX_HOME", ".codex"), "claude": ("CLAUDE_CONFIG_DIR", ".claude")}
    for name, folder in (locations[provider],) if provider is not None else locations.values():
        configured = source.get(name)
        location = configured if configured and owner_path(configured) else str(Path(home) / folder)
        if not owner_path(location):
            raise ProviderStateSchemaError(
                "The owner CLI directory is unavailable for provider state."
            )
        result[name] = location
    return result


async def _finish_security_write(function, *args, **kwargs):
    """Finish the writer or complete rollback before propagating any cancellation."""
    with state_write_deadline(SECURITY_WRITE_SECONDS):
        task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    cancelled = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as exc:
            cancelled = exc
        except Exception:
            break
    if cancelled is not None:
        if not task.cancelled():
            task.exception()  # Retrieve a failed writer's result before compensation.
        raise cancelled
    return task.result()


def _restore_security_writes(rollback):
    """Run every compensation as one unit, including after an earlier undo fails."""
    failed = False
    for undo in reversed(rollback):
        try:
            with state_write_deadline(SECURITY_WRITE_SECONDS):
                undo.restore()
        except Exception:
            failed = True
    if failed:
        logger.warning("Provider trust rollback incomplete: provider_trust_rollback_incomplete")
    return failed


def snapshot_json(snapshot: StateSnapshot) -> dict:
    return dataclasses.asdict(snapshot)  # tuples become JSON arrays when serialized


def error_response(exc: _ProviderStateError, **extra) -> JSONResponse:
    """The control envelope ``{"error": code}`` plus the adapter's message, capped."""
    logger.warning("Provider state request failed: %s", exc.code)
    field = "provider_message" if isinstance(exc, ProviderCommandError) else "message"
    body = {"error": exc.code, **extra}
    if not isinstance(exc, ProviderStateConflictError):
        text = redact_paths(str(exc)) if field == "provider_message" else str(exc)
        body[field] = text[:MESSAGE_LIMIT]
    return JSONResponse(body, exc.status)


def _now(wall: Callable[[], float]) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(wall()))


def _key(provider: str, project_id: str) -> str:
    return f"{provider}|{project_id}"


def _read_entries(path: Path) -> dict[str, dict]:
    """The ``entries`` of a state file; anything unreadable or of another version is empty."""
    try:
        document = json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        document = None
    entries = document.get("entries") if isinstance(document, dict) else None
    if not (document and document.get("version") == FILE_VERSION and isinstance(entries, dict)):
        logger.warning("Provider state file ignored: provider_state_seen_unreadable")
        return {}
    return {key: entry for key, entry in entries.items() if isinstance(entry, dict)}


def _write_entries(path: Path, entries: dict[str, dict]) -> None:
    ControlStateRepository._replace(path, json.dumps({"version": FILE_VERSION, "entries": entries}))


def _sound(entry: dict) -> bool:
    """A seen entry the diff can rely on."""
    items, writes, notices = entry.get("items"), entry.get("writes"), entry.get("notices")
    return (
        isinstance(entry.get("stat"), str)
        and isinstance(items, dict)
        and all(isinstance(v, dict) and isinstance(v.get("enabled"), bool) for v in items.values())
        and isinstance(writes, dict)
        and all(isinstance(v, dict) for v in writes.values())
        and isinstance(notices, list)
        and all(
            isinstance(n, dict)
            and isinstance(n.get("id"), str)
            and isinstance(n.get("item_id"), str)
            for n in notices
        )
    )


def _items_of(snapshot: StateSnapshot) -> dict[str, dict]:
    return {
        item.id: {
            "enabled": item.enabled,
            "source": item.source.rsplit("/", 1)[-1],
            "name": item.name,
            "scope": item.scope,  # the layer that decides the value
        }
        for item in snapshot.items
    }


def diff_items(
    old: Mapping[str, dict],
    new: Mapping[str, dict],
    writes: Mapping[str, dict],
    exclude: str | None = None,
) -> list[dict]:
    """What changed between two item maps, as notices without ``id`` and ``detected_at``.

    ``reverted`` is a change that puts an item back to the value it had before KeepHarness's last
    write to it (the write's ``after`` is what we saw, the write's ``before`` is what we see now).
    """
    found = []
    for item_id in sorted(old.keys() | new.keys()):
        before, after = old.get(item_id), new.get(item_id)
        if item_id == exclude or (before and after and before["enabled"] == after["enabled"]):
            continue
        shown = after or before
        notice = {
            "item_id": item_id,
            "name": shown.get("name", ""),
            "change": "added" if before is None else "removed" if after is None else "changed",
            "before": before and before["enabled"],
            "after": after and after["enabled"],
            "source": shown.get("source", ""),
        }
        written = writes.get(item_id) or {}
        if (
            notice["change"] == "changed"
            and written.get("after") == notice["before"]
            and written.get("before") == notice["after"]
        ):
            notice["change"] = "reverted"
        found.append(notice)
    return found


def _merge(
    notices: list[dict], found: list[dict], provider: str, project_id: str, detected_at: str
) -> list[dict]:
    """Pending notices plus new changes: one per item, the first ``before`` kept, the cap applied."""
    pending = {notice["item_id"]: notice for notice in notices}
    for change in found:
        earlier = pending.pop(change["item_id"], None)
        before = earlier["before"] if earlier else change["before"]
        if change["after"] == before:
            continue  # the item came back to what the owner last saw
        kind = (
            "added"
            if before is None
            else "removed"
            if change["after"] is None
            else change["change"]
            if change["change"] == "reverted"
            else "changed"
        )
        seed = (
            f"{provider}|{project_id}|{change['item_id']}|{before}|{change['after']}|{detected_at}"
        )
        pending[change["item_id"]] = {
            "id": "n_" + hashlib.sha256(seed.encode()).hexdigest()[:16],
            **change,
            "change": kind,
            "before": before,
            "detected_at": detected_at,
        }
    return list(pending.values())[-NOTICE_CAP:]


def _note_write(entry: dict, item_id: str, before: bool | None, after: bool, at: str) -> None:
    """KeepHarness wrote ``item_id``: remember it for ``reverted`` and drop its stale notice."""
    entry["writes"].pop(item_id, None)
    if before is not None and before != after:
        entry["writes"][item_id] = {"before": before, "after": after, "at": at}
    entry["notices"] = [n for n in entry["notices"] if n["item_id"] != item_id]


def _watch(adapter: ProviderStateAdapter, root: Path | None) -> str:
    return fingerprint(adapter.watch_paths(root))


def _source_identity(adapter: ProviderStateAdapter, root: Path | None) -> str:
    """Locations only: changing source homes requires a baseline, editing their files does not."""
    locations = "\0".join(str(path) for path in adapter.watch_paths(root))
    return hashlib.sha256(locations.encode()).hexdigest()


def _watch_or_blank(adapter: ProviderStateAdapter, root: Path | None) -> str:
    """The stat after a write that landed; unreadable is "unknown", so the next read diffs."""
    try:
        return _watch(adapter, root)
    except OSError:
        return ""


def run_start_check(
    control_state: Path, provider: str, project_id: str, project_root: Path | None
) -> None:
    """Harness side (issue #43): stat the watched paths and note, once, when they moved.

    Reads no CLI file beyond its stat. Without a seen entry for the key there is nothing to
    compare with, so nothing is written. Never raises: a run must not fail over this.
    """
    try:
        environment = _owner_environment(control_state, provider) if provider != "deepseek" else None
        adapter = (
            DeepSeekStateAdapter(control_state)
            if provider == "deepseek"
            else CodexStateAdapter(environment=environment)
            if provider == "codex"
            else ClaudeStateAdapter(control_state, environment=environment)
        )
        stat = _watch(adapter, project_root)
        key = _key(provider, project_id)
        seen = _read_entries(Path(control_state) / SEEN_FILE).get(key)
        if seen is None or seen.get("source_identity") != _source_identity(adapter, project_root):
            return
        with _runs_lock:  # read-modify-write of the one runs file
            runs = _read_entries(Path(control_state) / RUNS_FILE)
            if stat in (seen.get("stat"), runs.get(key, {}).get("stat")):
                return
            runs[key] = {"stat": stat, "detected_at": _now(time.time)}
            _write_entries(Path(control_state) / RUNS_FILE, runs)
    except Exception:
        logger.debug("provider_state_run_check_failed")


class ProviderStateService:
    def __init__(
        self,
        state: Path,
        projects: Callable[[], list[dict]],
        adapters: Mapping[str, ProviderStateAdapter] | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
        track_notices: bool = True,
    ) -> None:
        self.state = Path(state)
        self.track_notices = track_notices
        self.projects = projects
        self.adapters = dict(adapters or {})  # each provider is built on its first use
        self.environment = {
            name: os.environ[name]
            for name in ("HOME", "USERPROFILE", "CODEX_HOME", "CLAUDE_CONFIG_DIR")
            if name in os.environ
        }
        self.clock = clock
        self.wall = wall
        self.locks: dict[str, asyncio.Lock] = {}
        self.cache: dict[tuple[str, str], tuple[float, StateSnapshot]] = {}
        self._seen: dict[str, dict] | None = None  # loaded on first use, then kept in memory
        self._live: set[str] | None = None  # the project ids the seen map was last pruned against

    def resolve(self, provider: str, project_id: str) -> Path | None:
        """The project's folder (``None`` for "No project"); 404 for an unknown provider or project."""
        if provider not in PROVIDERS:
            raise APIError("provider_unknown", 404)
        if project_id == NO_PROJECT:
            return None
        projects = self.projects()
        self._prune(projects)
        for project in projects:
            if project.get("id") == project_id:
                return Path(project["root"]).resolve()
        raise APIError("project_unknown", 404)

    def _adapter(self, provider: str) -> ProviderStateAdapter:
        if provider == "deepseek" and provider not in self.adapters:
            self.adapters[provider] = DeepSeekStateAdapter(self.state)
        if provider not in self.adapters:
            environment = _owner_environment(self.state, provider, source=self.environment)
            self.adapters[provider] = (
                CodexStateAdapter(environment=environment)
                if provider == "codex"
                else ClaudeStateAdapter(self.state, environment=environment)
            )
        return self.adapters[provider]

    # --- the seen map: every method below is synchronous, so a load-modify-save never interleaves

    def _prune(self, projects: list[dict]) -> None:
        """Drop the seen keys of removed projects, only when the project list changed since the last prune."""
        live = {NO_PROJECT, *(project.get("id") for project in projects)}
        if self._seen is None or live == self._live:
            return
        self._live = live
        for key in [key for key in self._seen if key.split("|", 1)[1] not in live]:
            del self._seen[key]  # a removed project: a project added later starts from a baseline

    def _entries(self) -> dict[str, dict]:
        if self._seen is None:
            loaded = _read_entries(self.state / SEEN_FILE)
            self._seen = {key: entry for key, entry in loaded.items() if _sound(entry)}
            self._prune(self.projects())
        return self._seen

    def _save(self) -> None:
        try:
            self._prune(self.projects())
            _write_entries(self.state / SEEN_FILE, self._entries())
        except OSError:
            logger.warning("Provider state seen map not saved: provider_state_seen_unwritable")

    def _pending(self, provider: str, project_id: str) -> list[dict]:
        entry = self._entries().get(_key(provider, project_id))
        return [dict(notice) for notice in entry["notices"]] if entry else []

    def _detected_at(self, key: str, old_stat: str, new_stat: str) -> str:
        """When the harness saw the files move at a run start, if that is this very change."""
        run = _read_entries(self.state / RUNS_FILE).get(key, {})
        if run.get("stat") == new_stat != old_stat and isinstance(run.get("detected_at"), str):
            return run["detected_at"]
        return _now(self.wall)

    def _record(
        self,
        provider: str,
        project_id: str,
        stat: str,
        snapshot: StateSnapshot,
        written: tuple[str, bool, str] | None = None,
        receipt: dict | None = None,
    ) -> None:
        """Compare ``snapshot`` with the seen map and keep what is new.

        ``written`` is KeepHarness's own write, ``(item_id, enabled, scope)``: that item makes no
        notice and is remembered so a later change back shows as ``reverted``; a user-scope write
        also moves the item in the provider's other keys, which see the same file. A read with an
        unchanged stat fingerprint is not diffed.
        """
        key, items = _key(provider, project_id), _items_of(snapshot)
        entry = self._entries().get(key)
        root = Path(snapshot.project_root) if snapshot.project_root else None
        source_identity = _source_identity(self._adapter(provider), root)
        if entry is None or entry.get("source_identity") != source_identity:
            # Legacy basename-only entries cannot distinguish owner and harness homes.
            self._entries()[key] = {
                "source_identity": source_identity,
                "stat": stat,
                "items": items,
                "writes": {},
                "notices": [],
                "receipt_id": (receipt or {}).get("id"),
            }
            return self._save()
        receipt = receipt or {}
        if (
            written is None
            and entry["stat"] == stat
            and receipt.get("id") == entry.get("receipt_id")
        ):
            return
        item_id = written[0] if written else None
        previous = dict(entry["items"])
        if receipt.get("source_identity") == source_identity and receipt.get("id") != entry.get(
            "receipt_id"
        ):
            entry["receipt_id"] = receipt.get("id")
            for ident, transition in receipt.get("transitions", {}).items():
                before, after = transition.get("before"), transition.get("after")
                if items.get(ident) == after:
                    entry["notices"] = [
                        notice
                        for notice in entry["notices"]
                        if not (
                            notice["item_id"] == ident
                            and notice.get("before") == (before and before["enabled"])
                            and notice.get("after") == (after and after["enabled"])
                        )
                    ]
                if previous.get(ident) == transition.get("before") and items.get(
                    ident
                ) == transition.get("after"):
                    if ident in items:
                        previous[ident] = items[ident]
                    else:
                        previous.pop(ident, None)
        found = diff_items(previous, items, entry["writes"], item_id)
        if found:
            detected_at = self._detected_at(key, entry["stat"], stat)
            entry["notices"] = _merge(entry["notices"], found, provider, project_id, detected_at)
        for notice in found:
            entry["writes"].pop(notice["item_id"], None)
        if written:
            after = items.get(item_id, {}).get("enabled", written[1])
            at = _now(self.wall)
            _note_write(entry, item_id, entry["items"].get(item_id, {}).get("enabled"), after, at)
            for other, seen in self._entries().items():
                held = seen["items"].get(item_id, {})
                # only a key whose value the user layer decides moves with a user-scope write
                if (
                    written[2] == "user"
                    and other.startswith(f"{provider}|")
                    and other != key
                    and held.get("scope") == "user"
                ):
                    _note_write(seen, item_id, held["enabled"], after, at)
                    held["enabled"] = after
        entry["items"], entry["stat"] = items, stat
        self._save()

    # --- adapter calls (threads)

    def _read_with_stat(self, provider: str, root: Path | None) -> tuple[str, StateSnapshot]:
        adapter = self._adapter(provider)
        try:
            return _watch(adapter, root), adapter.read_state(root)
        except OSError:
            raise ProviderStateSchemaError("The provider state is unreadable.") from None

    async def _read_fresh(self, provider: str, project_id: str, root: Path | None) -> StateSnapshot:
        stat, snapshot = await asyncio.to_thread(self._read_with_stat, provider, root)
        self.cache[provider, project_id] = (self.clock(), snapshot)
        if self.track_notices:
            receipt = await self._prepare_receipt(provider, project_id, root, snapshot)
            self._record(provider, project_id, stat, snapshot, receipt=receipt)
        return snapshot

    async def read(self, provider: str, project_id: str) -> dict | JSONResponse:
        root = self.resolve(provider, project_id)
        try:
            async with self.locks.setdefault(provider, asyncio.Lock()):
                cached = self.cache.get((provider, project_id))
                if (
                    cached
                    and self.clock() - cached[0] < COALESCE_SECONDS
                    and cached[1].project_root == (str(root) if root else None)
                ):
                    snapshot = cached[1]
                else:
                    snapshot = await self._read_fresh(provider, project_id, root)
                changes = self._pending(provider, project_id) if self.track_notices else []
                metadata = (
                    {}
                    if provider == "deepseek"
                    else await asyncio.to_thread(self._security_metadata, root)
                )
        except _ProviderStateError as exc:
            return error_response(exc)
        return {"snapshot": snapshot_json(snapshot), "external_changes": changes, **metadata}

    def _security_metadata(self, root: Path | None) -> dict:
        if root is None:
            return {}
        codex_trust = self._adapter("codex").project_trust_details(root)
        trusted = codex_trust["trusted"] or self._adapter("claude")._is_project_trusted(root)
        claude = self._adapter("claude")
        approved = claude.approved_project_servers(root, trusted=trusted)
        enabled = claude.enabled_project_servers(root)
        return {
            "trust": {**codex_trust, "trusted": trusted, "required": not trusted},
            "mcp_approvals": [
                {"server": name, "approved": name in approved, "enabled": name in enabled}
                for name in sorted(claude.project_servers(root))
            ],
        }

    def _receipt_path(self, provider: str, project_id: str) -> Path:
        ident = hashlib.sha256(_key(provider, project_id).encode()).hexdigest()
        return self.state / "provider-state-writes" / (ident + ".json")

    def _validated_receipt(self, provider, root, snapshot, receipt):
        """Attribute only unchanged project layers crossing the requested trust boundary."""
        if receipt.get("source_identity") != _source_identity(self._adapter(provider), root):
            return {}
        layers = receipt.get("project_layers")
        trusted = receipt.get("trusted", True)
        if (
            not layers
            or provider != "codex"
            or next(iter(layers.values()))["enabled"] == trusted
            or any(
                not isinstance(layer.get("version"), str) or not layer["version"]
                for layer in layers.values()
            )
        ):
            return receipt
        verified, current = self._adapter(provider).read_trust_state(root)
        if (
            verified.fingerprint != snapshot.fingerprint
            or not current
            or (trusted and not all(layer["enabled"] for layer in current.values()))
            or (not trusted and next(iter(current.values()))["enabled"])
            or {key: layer["version"] for key, layer in layers.items()}
            != {key: layer["version"] for key, layer in current.items()}
        ):
            return receipt
        before = receipt["before"]
        transitions = dict(receipt.get("transitions", {}))
        expected = {
            ident: enabled
            for layer in current.values()
            for ident, enabled in layer.get("items", {}).items()
        }
        after = _items_of(snapshot)
        if not trusted:
            # Match the pre-write fallback as well as unchanged project versions;
            # a concurrent user-layer edit must still produce an external notice.
            for key, layer in layers.items():
                if not layer["enabled"] or current[key]["enabled"]:
                    continue
                for ident in layer.get("items", {}):
                    expected_item = layer.get("fallbacks", {}).get(ident)
                    if ident in before and after.get(ident) == expected_item:
                        held = transitions.get(ident)
                        original = (
                            held["before"]
                            if held and held["after"] == before[ident]
                            else before[ident]
                        )
                        transitions[ident] = {"before": original, "after": expected_item}
        for ident, item in after.items():
            if (
                item["scope"] == "project"
                and ident in expected
                and expected[ident] == item["enabled"]
                and before.get(ident) != item
            ):
                held = transitions.get(ident)
                original = (
                    held["before"]
                    if held and held["after"] == before.get(ident)
                    else before.get(ident)
                )
                transitions[ident] = {"before": original, "after": item}
        # Exact transitions no longer need re-derivation on a subsequent no-op retry.
        return {
            key: value
            for key, value in {**receipt, "transitions": transitions}.items()
            if key not in ("before", "project_layers")
        }

    async def _prepare_receipt(self, provider, project_id, root, snapshot):
        receipt = _read_entries(self._receipt_path(provider, project_id)).get("receipt", {})
        entry = self._entries().get(_key(provider, project_id), {})
        if receipt.get("id") != entry.get("receipt_id"):
            receipt = await asyncio.to_thread(
                self._validated_receipt, provider, root, snapshot, receipt
            )
        return receipt

    def _security_intent(self, provider, project_id, root, before, layers, trusted=True):
        # No-op retries must preserve the earlier, possibly unconsumed own-write receipt.
        if (
            not layers
            or next(iter(layers.values()))["enabled"] == trusted
            or any(
                not isinstance(layer.get("version"), str) or not layer["version"]
                for layer in layers.values()
            )
        ):
            return
        path = self._receipt_path(provider, project_id)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        identity = _source_identity(self._adapter(provider), root)
        previous = _read_entries(path).get("receipt", {})
        seen = _read_entries(self.state / SEEN_FILE).get(_key(provider, project_id), {})
        old = _items_of(before)
        pending = previous.get("source_identity") == identity and previous.get("id") != seen.get(
            "receipt_id"
        )
        transitions = {
            ident: held
            for ident, held in previous.get("transitions", {}).items()
            if pending and old.get(ident) == held.get("after")
        }
        _write_entries(
            path,
            {
                "receipt": {
                    "id": str(time.time_ns()),
                    "source_identity": identity,
                    "fingerprint": before.fingerprint,
                    "before": _items_of(before),
                    "project_layers": layers,
                    "trusted": trusted,
                    "transitions": transitions,
                }
            },
        )

    def _security_receipt(self, provider, project_id, root, after):
        path = self._receipt_path(provider, project_id)
        receipt = _read_entries(path).get("receipt", {})
        checked = self._validated_receipt(provider, root, after, receipt)
        if checked != receipt:
            _write_entries(path, {"receipt": checked})

    async def security_write(
        self,
        provider: str,
        project_id: str,
        *,
        server: str | None = None,
        approved: bool = False,
        trusted: bool = True,
        expected_project_root: str | None = None,
    ) -> dict | JSONResponse:
        root = self.resolve(provider, project_id)
        if provider == "deepseek" or root is None or (server is not None and provider != "claude"):
            raise APIError("invalid_request", 400)
        # A trust action writes both CLIs. Always acquire locks in this fixed order.
        async with self.locks.setdefault("codex", asyncio.Lock()):
            async with self.locks.setdefault("claude", asyncio.Lock()):
                try:
                    current_root = self.resolve(provider, project_id)
                    if current_root != root or (
                        expected_project_root is not None
                        and expected_project_root != str(current_root)
                    ):
                        raise ProviderStateConflictError(
                            "The project folder changed; review its trust prompt again."
                        )
                    names = ("codex", "claude") if server is None else ("claude",)
                    rollback, intents, updates = [], [], {}
                    try:
                        for name in names:
                            adapter = self._adapter(name)
                            _, before = await asyncio.to_thread(self._read_with_stat, name, root)
                            if server is None and name == "codex":
                                captured, layers = await asyncio.to_thread(
                                    adapter.read_trust_state, root
                                )
                                if captured.fingerprint != before.fingerprint:
                                    raise ProviderStateConflictError(
                                        "The project state changed before changing trust."
                                    )
                                await _finish_security_write(
                                    adapter.trust_project,
                                    root,
                                    trusted=trusted,
                                    expected_fingerprint=before.fingerprint,
                                    rollback=rollback,
                                )
                                intents.append((name, project_id, root, before, layers, trusted))
                            elif server is None:
                                await _finish_security_write(
                                    adapter.trust_project, root, trusted=trusted, rollback=rollback
                                )
                            else:
                                await asyncio.to_thread(
                                    adapter.set_project_server_approval, root, server, approved
                                )
                        # Confirmation reads belong to the transaction too. No own-write
                        # receipt or notice map changes until both providers confirm.
                        for name in names:
                            updates[name] = await asyncio.to_thread(
                                self._read_with_stat, name, root
                            )
                        metadata = await asyncio.to_thread(self._security_metadata, root)
                    except BaseException as exc:
                        failed = await _finish_security_write(_restore_security_writes, rollback)
                        if isinstance(exc, asyncio.CancelledError):
                            raise
                        if failed:
                            raise ProviderTrustRollbackError(
                                "Trust change failed and rollback could not finish because CLI state changed "
                                "or became unavailable. Review both CLIs; no concurrent edits were overwritten."
                            ) from None
                        if isinstance(exc, _ProviderStateError):
                            raise
                        if not isinstance(exc, Exception):
                            raise
                        raise ProviderStateSchemaError(
                            "The CLI state could not be updated; trust changes were rolled back."
                            if server is None
                            else "The MCP approval could not be updated."
                        ) from None
                    finally:
                        for key in [key for key in self.cache if key[0] in names]:
                            del self.cache[key]
                    for intent in intents:
                        await asyncio.to_thread(self._security_intent, *intent)
                    for name, (stat, fresh) in updates.items():
                        await asyncio.to_thread(
                            self._security_receipt, name, project_id, root, fresh
                        )
                        self.cache[name, project_id] = (self.clock(), fresh)
                        if self.track_notices:
                            receipt = await self._prepare_receipt(name, project_id, root, fresh)
                            self._record(name, project_id, stat, fresh, receipt=receipt)
                    return {
                        "snapshot": snapshot_json(self.cache[provider, project_id][1]),
                        **metadata,
                    }
                except _ProviderStateError as exc:
                    return error_response(exc)

    async def write(
        self,
        provider: str,
        project_id: str,
        item_id: str,
        scope: str,
        enabled: bool,
        fingerprint: str,
    ) -> dict | JSONResponse:
        """``{"snapshot"}`` on success, the error response (409 with a fresh snapshot) otherwise."""
        root = self.resolve(provider, project_id)
        async with self.locks.setdefault(provider, asyncio.Lock()):
            try:
                adapter = self._adapter(provider)
                snapshot = await asyncio.to_thread(
                    partial(
                        adapter.set_enabled,
                        item_id,
                        scope,
                        enabled,
                        fingerprint,
                        project_root=root,
                    )
                )
                stat = await asyncio.to_thread(_watch_or_blank, adapter, root)
            except ProviderStateConflictError as exc:
                fresh = await self._read_fresh(provider, project_id, root)
                changes = self._pending(provider, project_id)
                return error_response(exc, snapshot=snapshot_json(fresh), external_changes=changes)
            except _ProviderStateError as exc:
                return error_response(exc)
            finally:
                # a write can change what any project of this provider sees, even a failed one
                for key in [key for key in self.cache if key[0] == provider]:
                    del self.cache[key]
            self.cache[provider, project_id] = (self.clock(), snapshot)
            receipt = await self._prepare_receipt(provider, project_id, root, snapshot)
            self._record(provider, project_id, stat, snapshot, (item_id, enabled, scope), receipt)
        return {"snapshot": snapshot_json(snapshot)}

    async def ack(self, provider: str, project_id: str, notice_ids: list[str]) -> dict:
        """Mark notices seen; ids that are not pending are ignored."""
        self.resolve(provider, project_id)
        async with self.locks.setdefault(provider, asyncio.Lock()):
            entry = self._entries().get(_key(provider, project_id))
            if entry and any(notice["id"] in notice_ids for notice in entry["notices"]):
                entry["notices"] = [n for n in entry["notices"] if n["id"] not in notice_ids]
                self._save()
        return {}
