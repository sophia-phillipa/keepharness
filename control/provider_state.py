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
from adapters.shared.process import redact_paths
from adapters.shared.provider_state import (
    ProviderCommandError,
    ProviderStateAdapter,
    ProviderStateConflictError,
    StateSnapshot,
    _ProviderStateError,
    fingerprint,
)
from agent_service.errors import APIError

from .persistence import ControlStateRepository

PROVIDERS = ("codex", "claude")
COALESCE_SECONDS = 5.0
MESSAGE_LIMIT = 300
NO_PROJECT = "sem-projeto"
SEEN_FILE = "provider-state-seen.json"
RUNS_FILE = "provider-state-runs.json"
FILE_VERSION = 1
NOTICE_CAP = 50

logger = logging.getLogger(__name__)
_runs_lock = threading.Lock()  # runs share one thread pool and one runs file


def _owner_environment(state: Path) -> dict[str, str]:
    """Pin the owner's locations; the 0.15 run homes never belong to this facade.

    Compare lexical paths only: resolving these locations must not read CLI state.
    Keep custom owner directories, but discard inherited harness-owned locations.
    """
    harness = Path(os.path.abspath(Path(state) / "providers"))

    def owned(value: str) -> bool:
        return Path(os.path.abspath(value)).is_relative_to(harness)

    home = os.environ.get("HOME")
    if not home or owned(home):
        if os.name == "posix":
            import pwd

            home = pwd.getpwuid(os.getuid()).pw_dir
        else:
            home = os.environ.get("USERPROFILE") or str(Path.home())
    result = {"HOME": home}
    for name, folder in (("CODEX_HOME", ".codex"), ("CLAUDE_CONFIG_DIR", ".claude")):
        configured = os.environ.get(name)
        result[name] = (
            configured if configured and not owned(configured) else str(Path(home) / folder)
        )
    return result


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
        environment = _owner_environment(control_state)
        adapter = (
            CodexStateAdapter(environment=environment)
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
    ) -> None:
        self.state = Path(state)
        self.projects = projects
        self.adapters = adapters  # built on first use
        self.environment = _owner_environment(self.state)
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
                return Path(project["root"])
        raise APIError("project_unknown", 404)

    def _adapter(self, provider: str) -> ProviderStateAdapter:
        if self.adapters is None:
            self.adapters = {
                "codex": CodexStateAdapter(environment=self.environment),
                "claude": ClaudeStateAdapter(self.state, environment=self.environment),
            }
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
            }
            return self._save()
        if written is None and entry["stat"] == stat:
            return
        item_id = written[0] if written else None
        found = diff_items(entry["items"], items, entry["writes"], item_id)
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
        return _watch(adapter, root), adapter.read_state(
            root
        )  # stat first: a late edit shows next time

    async def _read_fresh(self, provider: str, project_id: str, root: Path | None) -> StateSnapshot:
        stat, snapshot = await asyncio.to_thread(self._read_with_stat, provider, root)
        self.cache[provider, project_id] = (self.clock(), snapshot)
        self._record(provider, project_id, stat, snapshot)
        return snapshot

    async def read(self, provider: str, project_id: str) -> dict | JSONResponse:
        root = self.resolve(provider, project_id)
        try:
            async with self.locks.setdefault(provider, asyncio.Lock()):
                cached = self.cache.get((provider, project_id))
                if cached and self.clock() - cached[0] < COALESCE_SECONDS:
                    snapshot = cached[1]
                else:
                    snapshot = await self._read_fresh(provider, project_id, root)
                changes = self._pending(provider, project_id)
        except _ProviderStateError as exc:
            return error_response(exc)
        return {"snapshot": snapshot_json(snapshot), "external_changes": changes}

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
            adapter = self._adapter(provider)
            try:
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
            self._record(provider, project_id, stat, snapshot, (item_id, enabled, scope))
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
