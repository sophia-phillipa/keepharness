"""Durable UI preferences: an allow-listed JSON file per owner under the harness state folder.

The browser used to keep these in ``localStorage``, which belongs to one origin (scheme, host and
port) and is lost with the port, the Electron profile or the runtime file. Only the local owner has
a store. Nothing here is conversation content, a draft, a secret or a file path.

Reads are tolerant, writes are strict. A stored key that is unknown or invalid is not served and the
rest loads (a write keeps its raw value, so a downgrade never erases it); a file that cannot be parsed is moved aside as a timestamped ``.bak`` and the defaults
load. A write names only allow-listed keys with valid values, or nothing is written.
"""

import json
import logging
import math
import os
import re
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from .errors import APIError
from .harness_agents import LOCAL_CLIENT
from .pages import PROJECT_ID
from .persistence.json_file_repository import JsonFileRepository, owner_folder_name

logger = logging.getLogger(__name__)

VERSION = 1
FOLDER = "ui-state"
ENTRY = "preferences"
STRING_CHARS = 200  # every string, map key and identifier
VALUE_BYTES = 16 * 1024  # one key, serialized: a single key always fits one request
BODY_BYTES = 64 * 1024  # one PATCH
MAX_FILE_BYTES = 512 * 1024  # larger than every key at its cap together
# Unbounded client maps are capped here; the client prunes to its most recent entries.
ITEM_CAPS = {
    "project_list_preferences": 200,
    "project_expanded": 200,
    "conversation_activity": 100,
    "conversation_scroll": 100,
    "workspace_sections": 16,
}
IDENTIFIER = re.compile(r"[A-Za-z0-9._:-]{1,64}")
WORD = re.compile(r"[a-z0-9_-]*")
_lock = threading.Lock()


class Invalid(ValueError):
    """A value outside its key's schema."""


# A check takes ``(value, strict)`` and returns the normalized value. Strict (writes) raises on
# anything unexpected; lenient (reads of a stored file) drops it and keeps the rest.
Check = Callable[[object, bool], object]


def flag(value: object, strict: bool = True) -> bool:
    if not isinstance(value, bool):
        raise Invalid
    return value


def text(*, pattern: re.Pattern | None = None, choices: tuple[str, ...] | None = None) -> Check:
    def check(value: object, strict: bool = True) -> str:
        if not isinstance(value, str) or len(value) > STRING_CHARS:
            raise Invalid
        if choices is not None and value not in choices:
            raise Invalid
        if pattern is not None and not pattern.fullmatch(value):
            raise Invalid
        return value

    return check


def number(low: float, high: float) -> Check:
    def check(value: object, strict: bool = True) -> float:
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise Invalid
        # Range first: math.isfinite() overflows on a huge int.
        if not low <= value <= high or (isinstance(value, float) and not math.isfinite(value)):
            raise Invalid
        return value

    return check


def optional(inner: Check) -> Check:
    return lambda value, strict=True: None if value is None else inner(value, strict)


def record(fields: dict[str, Check]) -> Check:
    def check(value: object, strict: bool = True) -> dict:
        if not isinstance(value, dict):
            raise Invalid
        result = {}
        for name, item in value.items():
            try:
                if name not in fields:
                    raise Invalid
                result[name] = fields[name](item, strict)
            except Invalid:
                if strict:
                    raise
        return result

    return check


def mapping(key: Check, item: Check, cap: int) -> Check:
    def check(value: object, strict: bool = True) -> dict:
        if not isinstance(value, dict) or (strict and len(value) > cap):
            raise Invalid
        result = {}
        for name, entry in value.items():
            try:
                result[key(name, strict)] = item(entry, strict)
            except Invalid:
                if strict:
                    raise
        return dict(list(result.items())[-cap:])

    return check


def pairs(key: Check, item: Check, cap: int) -> Check:
    """A list of ``[key, value]`` pairs, oldest first, like a JavaScript ``Map`` entry list."""

    def check(value: object, strict: bool = True) -> list:
        if not isinstance(value, list) or (strict and len(value) > cap):
            raise Invalid
        result = []
        for entry in value:
            try:
                if not isinstance(entry, list) or len(entry) != 2:
                    raise Invalid
                result.append([key(entry[0], strict), item(entry[1], strict)])
            except Invalid:
                if strict:
                    raise
        return result[-cap:]

    return check


_identifier = text(pattern=IDENTIFIER)
_token = text(pattern=re.compile(r"(?:%s)?" % IDENTIFIER.pattern))  # "" is a writer sentinel
_project_id = text(pattern=PROJECT_ID)
_width = number(0, 20000)
# Model ids and efforts come from the provider catalogs (``gpt-5.6-sol``, ``claude-opus-4-6[1m]``,
# ``qwen36-35b-a3b-ud-q3-k-m``); "" is a writer sentinel.
_catalog_id = text(pattern=re.compile(r"[A-Za-z0-9._:/\[\]-]{0,128}"))
# Each key is one preference the UI persists; ``dossier/releases/v0.16.0.md`` lists its writer.
SCHEMA: dict[str, Check] = {
    "theme": text(pattern=re.compile(r"[a-z0-9-]{1,40}")),
    "sidebar_collapsed": flag,
    "panel_order": text(choices=("conversations-left", "conversations-right")),
    "panel_widths": record({"sidebar": _width, "activity_panel": _width}),
    "reading_size": text(choices=("15", "17", "19")),
    "chat_selection": record({"model": _catalog_id, "effort": _catalog_id}),
    "project_list_preferences": mapping(
        _project_id,
        record({"favorite": flag, "hidden": flag, "hide_icon": flag}),
        ITEM_CAPS["project_list_preferences"],
    ),
    "project_expanded": mapping(_project_id, flag, ITEM_CAPS["project_expanded"]),
    "right_panel_view": text(choices=("files", "activity")),
    "activity_open": flag,
    "visual_markers": flag,
    "conversation_activity": mapping(
        _identifier,
        record({"token": _token, "state": text(pattern=WORD), "unread": flag}),
        ITEM_CAPS["conversation_activity"],
    ),
    "conversation_scroll": pairs(_identifier, number(-1, 10_000_000), ITEM_CAPS["conversation_scroll"]),
    "tour_seen": text(pattern=re.compile(r"[0-9A-Za-z.+-]{0,32}")),
    "run_console_height": number(0, 20000),
    "workspace_sections": mapping(
        text(pattern=re.compile(r"[a-z0-9-]{1,40}")),
        record({"open": flag, "height": optional(number(0, 20000))}),
        ITEM_CAPS["workspace_sections"],
    ),
    "last_section": text(pattern=WORD),
}


def limits() -> dict:
    """The caps the client shares; a contract test pins them against the schema."""
    return {
        "body_bytes": BODY_BYTES,
        "value_bytes": VALUE_BYTES,
        "string_chars": STRING_CHARS,
        "max_items": dict(ITEM_CAPS),
    }


def validate(key: str, value: object, *, strict: bool) -> object:
    result = SCHEMA[key](value, strict)
    if len(json.dumps(result, ensure_ascii=False).encode()) > VALUE_BYTES:
        raise Invalid
    return result


def unsafe() -> APIError:
    return APIError("ui_state_read_only", 409)


def repository(config: dict, owner: str) -> JsonFileRepository:
    folder = Path(config["state_dir"]) / FOLDER / owner_folder_name(owner)
    return JsonFileRepository(
        folder, id_pattern=re.compile(ENTRY), max_bytes=MAX_FILE_BYTES, unsafe=unsafe
    )


def writable(folder: Path) -> bool:
    """Whether a store can be created or replaced there: its nearest existing folder is ours."""
    if folder.is_symlink():
        return False
    for candidate in (folder, *folder.parents):
        if candidate.exists():
            return candidate.is_dir() and os.access(candidate, os.W_OK | os.X_OK)
    return False


def quarantine(store: JsonFileRepository) -> bool:
    """Move an unusable file aside as ``preferences.json.<time>.bak``; False when that fails."""
    path = store.folder / (ENTRY + ".json")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    try:
        if store.folder.is_symlink() or not path.parent.is_dir():
            return False
        os.replace(path, path.with_name(f"{path.name}.{stamp}.bak"))
    except FileNotFoundError:
        return True  # another reader already moved it aside
    except OSError:
        return False
    logger.warning("UI state file was unusable and was moved aside as .bak")
    return True


def load(store: JsonFileRepository, passthrough: dict | None = None) -> tuple[dict, bool]:
    """The valid stored values and whether the store is read-only.

    A stored key this build does not know (written by a newer one), rejects or truncates (a stricter
    or smaller schema) goes to ``passthrough`` with its raw value so a write keeps it unchanged.
    """
    read_only = not writable(store.folder)
    try:
        raw = store.read(ENTRY)
        if raw is None:
            return {}, read_only
        document = json.loads(raw)
        stored = document.get("values") if isinstance(document, dict) else None
        if not isinstance(stored, dict):
            raise ValueError("ui_state_shape")
    except (APIError, ValueError, RecursionError):
        return {}, read_only or not quarantine(store)
    values = {}
    for key, value in stored.items():
        try:
            if key not in SCHEMA:
                raise Invalid
            values[key] = validate(key, value, strict=False)
            if values[key] == value:
                continue
        except Invalid:
            pass
        if passthrough is not None:
            passthrough[key] = value
    return values, read_only


def view(values: dict, read_only: bool) -> dict:
    return {"version": VERSION, "values": values, "read_only": read_only, "limits": limits()}


def read(config: dict, owner: str) -> dict:
    return view(*load(repository(config, owner)))


def update(config: dict, owner: str, changes: dict) -> dict:
    """Merge ``changes`` key by key; ``None`` clears a key. Invalid input writes nothing."""
    checked = {}
    for key, value in changes.items():
        if key not in SCHEMA:
            raise APIError("ui_state_unknown_key", 422, field=str(key)[:STRING_CHARS])
        try:
            checked[key] = None if value is None else validate(key, value, strict=True)
        except Invalid:
            raise APIError("ui_state_invalid_value", 422, field=key) from None
    store = repository(config, owner)
    with _lock:
        passthrough: dict = {}
        values, read_only = load(store, passthrough)
        if read_only:
            raise unsafe()
        before = {**values, **passthrough}
        for key, value in checked.items():
            passthrough.pop(key, None)
            if value is None:
                values.pop(key, None)
            else:
                values[key] = value
        merged = {**values, **passthrough}  # a kept raw value wins over its tolerant reading
        if merged == before:
            return view(values, False)
        document = json.dumps({"version": VERSION, "values": merged}, ensure_ascii=False)
        try:
            store.replace(ENTRY, document)
        except OSError:
            raise unsafe() from None
    return view(values, False)


def require_owner(identity: tuple) -> None:
    """Preferences belong to the local owner; any other client gets no store."""
    if identity[0] != LOCAL_CLIENT:
        raise APIError("ui_state_local_only", 403)
