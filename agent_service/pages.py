"""Space pages: Markdown notes that belong to one client inside one project.

A page is a title and a Markdown body. Pages are private to the client that created them
and live in the project they were created in; see ``docs/pages.md``. The files live in
``JsonFileRepository``; this module decides what a page may hold.
"""

import json
import logging
import re
import threading
import uuid
from collections.abc import Mapping
from pathlib import Path

from .errors import APIError
from .json_depth import too_deep
from .persistence.json_file_repository import (
    JsonFileRepository,
    owner_folder_name,
    revision_of,
    timestamp,
)

logger = logging.getLogger(__name__)

PAGE_ID = re.compile(r"^[0-9a-f]{32}$")
# A project id becomes a folder name, so it must be one plain path component.
PROJECT_ID = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$")
MAX_PAGES = 500
TITLE_LENGTH = (1, 120)
MAX_BODY_BYTES = 200 * 1024
# Escaping can double the body (quotes, backslashes, line breaks) in the stored JSON.
MAX_FILE_BYTES = 2 * MAX_BODY_BYTES + 4096
REQUEST_LIMIT = MAX_FILE_BYTES
EDITABLE = frozenset({"title", "body"})
# Echoed by a read, so a client may send them back; they never change what is stored.
READ_ONLY = frozenset({"id", "created_at", "updated_at", "revision"})
SCOPE = frozenset({"project_id"})
TITLE_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
# Markdown keeps tabs and line breaks; every other control character is refused.
BODY_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# Serializes read-compare-write sequences; the service runs as a single process.
write_lock = threading.Lock()


def invalid(field: str) -> APIError:
    return APIError("page_invalid", 422, field=field)


def not_found() -> APIError:
    return APIError("page_not_found", 404)


def unsafe_storage() -> APIError:
    return APIError("page_storage_unsafe", 500)


def project_of(source: Mapping) -> str:
    """The ``project_id`` of a request body or query; ``page_invalid`` if missing or unsafe."""
    project_id = source.get("project_id")
    if not isinstance(project_id, str) or not PROJECT_ID.fullmatch(project_id):
        raise invalid("project_id")
    return project_id


def repository(config: dict, owner: str, project_id: str) -> JsonFileRepository:
    folder = Path(config["state_dir"]) / "pages" / owner_folder_name(owner) / project_id
    return JsonFileRepository(
        folder, id_pattern=PAGE_ID, max_bytes=MAX_FILE_BYTES, unsafe=unsafe_storage
    )


# ----------------------------------------------------------------------------- validation


def clean_title(value: object) -> str:
    if not isinstance(value, str) or TITLE_CONTROL.search(value):
        raise invalid("title")
    value = value.strip()
    if not TITLE_LENGTH[0] <= len(value) <= TITLE_LENGTH[1]:
        raise invalid("title")
    return value


def clean_body(value: object) -> str:
    if not isinstance(value, str) or BODY_CONTROL.search(value):
        raise invalid("body")
    if len(value.encode()) > MAX_BODY_BYTES:
        raise invalid("body")
    return value


def reject_unknown_fields(body: dict) -> None:
    unknown = sorted(set(body) - EDITABLE - READ_ONLY - SCOPE)
    if unknown:
        raise invalid(unknown[0])


def normalize(body: dict) -> dict:
    """The editable fields of a request or file body; ``page_invalid`` if they are not valid."""
    return {"title": clean_title(body.get("title")), "body": clean_body(body.get("body"))}


def serialize(record: dict) -> str:
    text = json.dumps(record, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    if len(text.encode()) > MAX_FILE_BYTES:
        raise invalid("body")
    return text


def load(page_id: str, text: str) -> dict:
    """A stored page, held to the request limits; ``ValueError`` when the file is unusable."""
    try:
        data = json.loads(text)
        if too_deep(data):
            raise ValueError("invalid_page")
        if not isinstance(data, dict) or data.get("id") != page_id:
            raise ValueError("invalid_page")
        record = {"id": page_id, **normalize(data)}
    except (ValueError, RecursionError, APIError):
        raise ValueError("invalid_page") from None
    for key in ("created_at", "updated_at"):
        record[key] = data[key] if isinstance(data.get(key), str) else ""
    return record


def describe(record: dict, text: str) -> dict:
    return {**record, "revision": revision_of(text)}


def summarize(record: dict, text: str) -> dict:
    return {
        "id": record["id"],
        "title": record["title"],
        "updated_at": record["updated_at"],
        "revision": revision_of(text),
        "size": len(record["body"].encode()),
    }


def require_revision(body: dict) -> str:
    revision = body.get("revision")
    if not isinstance(revision, str) or not revision:
        raise invalid("revision")
    return revision


def require_known(page_id: str) -> None:
    if not PAGE_ID.fullmatch(page_id):
        raise not_found()


def read_current(store: JsonFileRepository, page_id: str, revision: str) -> str:
    """The stored text, after checking that it is still the revision the caller saw."""
    text = store.read(page_id)
    if text is None:
        raise not_found()
    if revision_of(text) != revision:
        raise APIError("page_changed", 409)
    return text


# ----------------------------------------------------------------------------- operations


def list_pages(config: dict, owner: str, project_id: str) -> list[dict]:
    entries, skipped = repository(config, owner, project_id).read_all(MAX_PAGES)
    pages = []
    for page_id, text in entries:
        try:
            pages.append((load(page_id, text), text))
        except ValueError:
            skipped.append(page_id)
    if skipped:
        logger.warning("Skipped unusable page files: %s", ", ".join(sorted(skipped)))
    pages.sort(key=lambda item: (item[0]["updated_at"], item[0]["id"]), reverse=True)
    return [summarize(record, text) for record, text in pages]


def read_page(config: dict, owner: str, project_id: str, page_id: str) -> dict:
    require_known(page_id)
    text = repository(config, owner, project_id).read(page_id)
    if text is None:
        raise not_found()
    try:
        return describe(load(page_id, text), text)
    except ValueError:
        raise not_found() from None


def create_page(config: dict, owner: str, project_id: str, body: dict) -> dict:
    reject_unknown_fields(body)
    fields = normalize(body)
    now = timestamp()
    page_id = uuid.uuid4().hex
    record = {"id": page_id, **fields, "created_at": now, "updated_at": now}
    text = serialize(record)
    store = repository(config, owner, project_id)
    with write_lock:
        if len(store.ids()) >= MAX_PAGES:
            raise APIError("page_limit", 409)
        store.create(page_id, text)
    return describe(record, text)


def replace_page(config: dict, owner: str, project_id: str, page_id: str, body: dict) -> dict:
    require_known(page_id)
    if body.get("id", page_id) != page_id:
        raise invalid("id")
    reject_unknown_fields(body)
    revision = require_revision(body)
    fields = normalize(body)
    now = timestamp()
    store = repository(config, owner, project_id)
    with write_lock:
        current = read_current(store, page_id, revision)
        try:
            created_at = load(page_id, current)["created_at"]
        except ValueError:
            created_at = now
        record = {"id": page_id, **fields, "created_at": created_at or now, "updated_at": now}
        text = serialize(record)
        store.replace(page_id, text)
    return describe(record, text)


def delete_page(config: dict, owner: str, project_id: str, page_id: str, body: dict) -> dict:
    require_known(page_id)
    revision = require_revision(body)
    store = repository(config, owner, project_id)
    with write_lock:
        read_current(store, page_id, revision)
        store.delete(page_id)
    return {"deleted": True}
