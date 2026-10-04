"""Scheduled tasks: recurring prompts that run unattended as new conversations.

A schedule is a prompt, the route it runs on (provider, model, effort), an access mode, whether
it may use the internet and a cadence. It belongs to the client that created it. This module
decides what a schedule may hold, when it is due and how a run is recorded;
``services/scheduler.py`` submits the due ones and ``docs/scheduled-tasks.md`` describes the
whole feature. The files live in ``JsonFileRepository``.
"""

import itertools
import json
import logging
import os
import re
import threading
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from datetime import time as wall_time
from pathlib import Path
from types import MappingProxyType

from . import harness_agents, pages
from .errors import APIError
from .persistence.harness_agent_repository import AGENT_ID
from .persistence.json_file_repository import (
    JsonFileRepository,
    owner_folder_name,
    revision_of,
    timestamp,
)
from .resources import ENGINES, ResourceError, reserved_markers

logger = logging.getLogger(__name__)

SCHEDULE_ID = re.compile(r"^[0-9a-f]{32}$")
OWNER_FOLDER = re.compile(r"^[0-9a-f]{16}$")
MAX_SCHEDULES = 50
TITLE_LENGTH = (1, 120)
PROMPT_LENGTH = (1, 20000)
MAX_FILE_BYTES = 131072
# Nobody watches an unattended run, so it never gets automatic or full access.
ACCESS_MODES = ("ask", "read_only")
FAILURE_LIMIT = 3
HOURS = (1, 168)
MAX_PAGE_IDS = 5
# "submitted": the run is queued or running; the scheduler then reads its outcome back (D15).
# "failed" without a job is a refused submit.
FAILED_OUTCOMES = ("failed", "cancelled", "interrupted")
LAST_RUN_STATES = ("submitted", "completed", *FAILED_OUTCOMES)
EDITABLE = (
    "title",
    "prompt",
    "project_id",
    "backend",
    "model",
    "effort",
    "access_mode",
    "allow_internet",
    "cadence",
    "enabled",
    "agent",
    "page_ids",
)
ROUTE = ("project_id", "backend", "model", "effort")
# Echoed by a listing, so a client may send them back; they never change what is stored.
READ_ONLY = frozenset(
    {
        "id",
        "created_at",
        "updated_at",
        "revision",
        "next_run",
        "last_run",
        "failures",
        "paused_reason",
    }
)
PUBLIC = (
    "id",
    *EDITABLE,
    "created_at",
    "updated_at",
    "next_run",
    "last_run",
    "failures",
    "paused_reason",
)
CADENCE_KEYS = {
    "daily": {"kind", "time"},
    "weekly": {"kind", "weekday", "time"},
    "interval": {"kind", "hours"},
}
TIME = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
# A boolean is an ``int`` in Python but never a valid weekday or hour count.
CADENCE_VALUE_OK = MappingProxyType(
    {
        "time": lambda value: isinstance(value, str) and bool(TIME.fullmatch(value)),
        "weekday": lambda value: type(value) is int and 0 <= value <= 6,
        "hours": lambda value: type(value) is int and HOURS[0] <= value <= HOURS[1],
    }
)
LINE_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
# A prompt keeps tabs and line breaks; every other control character is refused.
TEXT_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# Serializes read-compare-write sequences; the service runs as a single process.
write_lock = threading.Lock()


def clock() -> float:
    return time.time()


def invalid(field: str) -> APIError:
    return APIError("schedule_invalid", 422, field=field)


def not_found() -> APIError:
    return APIError("schedule_not_found", 404)


def unsafe_storage() -> APIError:
    return APIError("schedule_storage_unsafe", 500)


def folder_repository(config: dict, folder: str) -> JsonFileRepository:
    return JsonFileRepository(
        Path(config["state_dir"]) / "schedules" / folder,
        id_pattern=SCHEDULE_ID,
        max_bytes=MAX_FILE_BYTES,
        unsafe=unsafe_storage,
    )


def repository(config: dict, owner: str) -> JsonFileRepository:
    return folder_repository(config, owner_folder_name(owner))


def owner_folders(config: dict) -> list[str]:
    """Names of the owner folders; a linked ``schedules`` folder is refused, a missing one is empty."""
    try:
        descriptor = os.open(
            Path(config["state_dir"]) / "schedules",
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        )
    except FileNotFoundError:
        return []
    except OSError:
        raise unsafe_storage() from None
    try:
        return sorted(name for name in os.listdir(descriptor) if OWNER_FOLDER.fullmatch(name))
    finally:
        os.close(descriptor)


# ----------------------------------------------------------------------------- cadence


def normalize_cadence(value: object) -> dict:
    """A copy of a valid cadence; ``schedule_invalid`` (field ``cadence``) if it is not one."""
    if not isinstance(value, dict):
        raise invalid("cadence")
    kind = value.get("kind")
    if not isinstance(kind, str) or kind not in CADENCE_KEYS or set(value) != CADENCE_KEYS[kind]:
        raise invalid("cadence")
    if not all(CADENCE_VALUE_OK[key](value[key]) for key in value if key != "kind"):
        raise invalid("cadence")
    return dict(value)


def next_run(cadence: dict, now: float) -> int:
    """The first run strictly after ``now`` (a UNIX timestamp), in the server's local time zone.

    Daily and weekly runs keep their wall-clock time across daylight saving changes: the day
    is chosen on the calendar and the time is attached to it, never a fixed 24 hours. A time
    that does not exist on a given day runs at the nearest instant the system maps it to.
    """
    if cadence["kind"] == "interval":
        return int(now) + cadence["hours"] * 3600
    at = wall_time(int(cadence["time"][:2]), int(cadence["time"][3:]))
    today = datetime.fromtimestamp(now).date()
    days = (today + timedelta(days=offset) for offset in itertools.count())
    matching = (
        day for day in days if cadence["kind"] == "daily" or day.weekday() == cadence["weekday"]
    )
    moments = (datetime.combine(day, at).timestamp() for day in matching)
    return int(next(moment for moment in moments if moment > now))


# ----------------------------------------------------------------------------- validation


def clean_text(value: object, field: str, limits: tuple[int, int], control: re.Pattern) -> str:
    if not isinstance(value, str) or control.search(value):
        raise invalid(field)
    value = value.strip()
    if not limits[0] <= len(value) <= limits[1]:
        raise invalid(field)
    return value


def clean_name(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise invalid(field)
    return value


def clean_agent(value: object) -> str | None:
    """The name of the Harness agent a run adopts; resolved to its current revision at run time."""
    if value in (None, ""):
        return None
    if not isinstance(value, str) or not AGENT_ID.fullmatch(value):
        raise invalid("agent")
    return value


def clean_page_ids(value: object) -> list[str]:
    """Space pages of the schedule's project whose current text every run carries (D41)."""
    if value is None:
        return []
    if (
        not isinstance(value, list)
        or len(value) > MAX_PAGE_IDS
        or len(set(map(str, value))) != len(value)
        or not all(isinstance(item, str) and pages.PAGE_ID.fullmatch(item) for item in value)
    ):
        raise invalid("page_ids")
    return list(value)


def body_fields(body: dict) -> dict:
    return {key: body[key] for key in EDITABLE if key in body}


def reject_unknown_fields(body: dict) -> None:
    unknown = sorted(set(body) - set(EDITABLE) - READ_ONLY)
    if unknown:
        raise invalid(unknown[0])


def normalize(body: dict) -> dict:
    """The editable fields of a request or file body; ``schedule_invalid`` names the first bad one."""
    fields = {
        "title": clean_text(body.get("title"), "title", TITLE_LENGTH, LINE_CONTROL),
        "prompt": clean_text(body.get("prompt"), "prompt", PROMPT_LENGTH, TEXT_CONTROL),
        "project_id": clean_name(body.get("project_id"), "project_id"),
        "backend": clean_name(body.get("backend"), "backend"),
        "model": clean_name(body.get("model"), "model"),
        "effort": clean_name(body.get("effort"), "effort"),
        "access_mode": body.get("access_mode"),
        # Unattended runs have no internet unless the task opts in (D03); older files lack it.
        "allow_internet": body.get("allow_internet", False),
        "cadence": normalize_cadence(body.get("cadence")),
        "enabled": body.get("enabled"),
        "agent": clean_agent(body.get("agent")),
        "page_ids": clean_page_ids(body.get("page_ids")),
    }
    if fields["backend"] not in ENGINES:
        raise invalid("backend")
    if not isinstance(fields["access_mode"], str) or fields["access_mode"] not in ACCESS_MODES:
        raise invalid("access_mode")
    if type(fields["allow_internet"]) is not bool:
        raise invalid("allow_internet")
    if type(fields["enabled"]) is not bool:
        raise invalid("enabled")
    return fields


def require_available(config: dict, fields: dict) -> None:
    """The route must be offered for the project right now, as for Harness agents."""
    problem = harness_agents.availability_problem(
        harness_agents.offered(config, fields["project_id"]), fields
    )
    if problem:
        raise invalid(problem[0])


def require_agent(config: dict, fields: dict) -> None:
    """The prompt names no ``@@`` agent that was not picked, and the picked agent exists (D41)."""
    picked = {"@@" + fields["agent"]} if fields["agent"] else set()
    try:
        typed = reserved_markers(fields["prompt"], picked)
    except ResourceError:
        raise APIError("schedule_agent_unselected", 422, field="prompt") from None
    if typed - picked:
        raise APIError("schedule_agent_unselected", 422, field="prompt")
    if picked and harness_agents.repository(config).read(fields["agent"]) is None:
        raise invalid("agent")


def serialize(record: dict) -> str:
    text = json.dumps(record, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    if len(text.encode()) > MAX_FILE_BYTES:
        raise invalid("prompt")
    return text


def clean_last_run(value: object) -> dict | None:
    if not isinstance(value, dict):
        return None
    job_id, at, state = value.get("job_id"), value.get("at"), value.get("state")
    if state not in LAST_RUN_STATES or type(at) is not int:
        return None
    if job_id is not None and not isinstance(job_id, str):
        return None
    run = {"job_id": job_id, "at": at, "state": state}
    if state in FAILED_OUTCOMES and isinstance(value.get("error"), str):
        run["error"] = value["error"]
    if value.get("needs_you") is True:
        run["needs_you"] = True
    return run


def text_or_empty(value: object) -> str:
    return value if isinstance(value, str) else ""


def run_state(data: dict) -> dict:
    """The fields the harness keeps itself; a damaged one falls back to its starting value."""
    due_at, failures = data.get("next_run"), data.get("failures")
    return {
        "created_at": text_or_empty(data.get("created_at")),
        "updated_at": text_or_empty(data.get("updated_at")),
        "next_run": due_at if type(due_at) is int and due_at >= 0 else None,
        "last_run": clean_last_run(data.get("last_run")),
        "failures": failures if type(failures) is int and failures >= 0 else 0,
        "paused_reason": text_or_empty(data.get("paused_reason")),
    }


def load(schedule_id: str, text: str, folder: str) -> dict:
    """A stored schedule, held to the request limits; ``ValueError`` when the file is unusable.

    The file must belong in ``folder``: a copy placed under another owner's folder is refused.
    """
    try:
        data = json.loads(text)
        if not isinstance(data, dict) or data.get("id") != schedule_id:
            raise ValueError("invalid_schedule")
        owner = data.get("owner")
        if not isinstance(owner, str) or owner_folder_name(owner) != folder:
            raise ValueError("invalid_schedule")
        return {"id": schedule_id, "owner": owner, **normalize(data), **run_state(data)}
    except (ValueError, RecursionError, APIError):
        raise ValueError("invalid_schedule") from None


def public(record: dict, text: str) -> dict:
    return {**{key: record[key] for key in PUBLIC}, "revision": revision_of(text)}


def require_known(schedule_id: str) -> None:
    if not SCHEDULE_ID.fullmatch(schedule_id):
        raise not_found()


def require_revision(body: dict) -> str:
    revision = body.get("revision")
    if not isinstance(revision, str) or not revision:
        raise invalid("revision")
    return revision


def read_current(store: JsonFileRepository, schedule_id: str, revision: str) -> str:
    """The stored text, after checking that it is still the revision the caller saw."""
    text = store.read(schedule_id)
    if text is None:
        raise not_found()
    if revision_of(text) != revision:
        raise APIError("schedule_changed", 409)
    return text


def load_owned(schedule_id: str, text: str, owner: str) -> dict:
    try:
        return load(schedule_id, text, owner_folder_name(owner))
    except ValueError:
        raise not_found() from None


# ----------------------------------------------------------------------------- operations


def stored(config: dict, folder: str) -> list[tuple[dict, str]]:
    """Usable schedules of one owner folder as ``(record, text)``, oldest first."""
    entries, skipped = folder_repository(config, folder).read_all(MAX_SCHEDULES)
    usable = []
    for schedule_id, text in entries:
        try:
            usable.append((load(schedule_id, text, folder), text))
        except ValueError:
            skipped.append(schedule_id)
    if skipped:
        logger.warning("Skipped unusable schedule files: %s", ", ".join(sorted(skipped)))
    usable.sort(key=lambda item: (item[0]["created_at"], item[0]["id"]))
    return usable


def list_schedules(config: dict, owner: str) -> list[dict]:
    return [public(record, text) for record, text in stored(config, owner_folder_name(owner))]


def owned_record(config: dict, owner: str, schedule_id: str) -> dict:
    require_known(schedule_id)
    text = repository(config, owner).read(schedule_id)
    if text is None:
        raise not_found()
    return load_owned(schedule_id, text, owner)


def create_schedule(config: dict, owner: str, body: dict) -> dict:
    reject_unknown_fields(body)
    fields = normalize({"access_mode": "ask", "enabled": True, **body_fields(body)})
    require_available(config, fields)
    require_agent(config, fields)
    now = clock()
    stamp = timestamp()
    record = {
        "id": uuid.uuid4().hex,
        "owner": owner,
        **fields,
        "created_at": stamp,
        "updated_at": stamp,
        "next_run": next_run(fields["cadence"], now) if fields["enabled"] else None,
        "last_run": None,
        "failures": 0,
        "paused_reason": "",
    }
    text = serialize(record)
    store = repository(config, owner)
    with write_lock:
        if len(store.ids()) >= MAX_SCHEDULES:
            raise APIError("schedule_limit", 409)
        store.create(record["id"], text)
    return public(record, text)


def retime(current: dict, fields: dict, now: float) -> dict:
    """The timing fields an edit changes: pausing clears the run, resuming starts fresh."""
    if not fields["enabled"]:
        return {"next_run": None, "paused_reason": ""}
    if not current["enabled"]:
        return {
            "next_run": next_run(fields["cadence"], now),
            "failures": 0,
            "paused_reason": "",
        }
    if fields["cadence"] != current["cadence"] or current["next_run"] is None:
        return {"next_run": next_run(fields["cadence"], now)}
    return {}


def replace_schedule(config: dict, owner: str, schedule_id: str, body: dict) -> dict:
    """Change the editable fields that ``body`` names; the others keep their stored value."""
    require_known(schedule_id)
    reject_unknown_fields(body)
    revision = require_revision(body)
    store = repository(config, owner)
    with write_lock:
        current = load_owned(schedule_id, read_current(store, schedule_id, revision), owner)
        fields = normalize({**{key: current[key] for key in EDITABLE}, **body_fields(body)})
        # A paused schedule may keep a route that is gone, so it can still be renamed or deleted.
        if fields["enabled"] or any(fields[key] != current[key] for key in ROUTE):
            require_available(config, fields)
        if fields["enabled"] or any(fields[key] != current[key] for key in ("prompt", "agent")):
            require_agent(config, fields)
        record = {
            **current,
            **fields,
            "updated_at": timestamp(),
            **retime(current, fields, clock()),
        }
        text = serialize(record)
        store.replace(schedule_id, text)
    return public(record, text)


def delete_schedule(config: dict, owner: str, schedule_id: str, body: dict) -> dict:
    require_known(schedule_id)
    revision = require_revision(body)
    store = repository(config, owner)
    with write_lock:
        read_current(store, schedule_id, revision)
        store.delete(schedule_id)
    return {"deleted": True}


# ----------------------------------------------------------------------------- running


def agent_selection(config: dict, name: str) -> dict:
    """The selection of the agent's current revision: an edited agent follows on the next run."""
    text = harness_agents.repository(config).read(name)
    if text is None:
        raise APIError("schedule_agent_missing", 422)
    return {
        "id": harness_agents.RESOURCE_PREFIX + name,
        "revision": harness_agents.revision_of(text),
        "token": "@@" + name,
    }


def page_section(config: dict, record: dict, page_id: str) -> str:
    """The page's text as of now, fenced so that its own ``@@`` or ``//`` words stay plain text."""
    try:
        page = pages.read_page(config, record["owner"], record["project_id"], page_id)
    except APIError:
        raise APIError("schedule_page_missing", 422) from None
    fence = "`" * max([3, *(len(run) + 1 for run in re.findall(r"`+", page["body"]))])
    return f"Page {json.dumps(page['title'])}:\n{fence}markdown\n{page['body']}\n{fence}"


def job_request(config: dict, record: dict) -> dict:
    """The body of the ``POST /v1/jobs`` a run submits: a fresh conversation, no parent.

    The picked agent and the pages are read as they are at run time (D41).
    """
    request = {
        key: record[key]
        for key in ("prompt", "project_id", "backend", "model", "effort", "access_mode")
    }
    if record["agent"]:
        selection = agent_selection(config, record["agent"])
        request["resource_selections"] = [selection]
        if not re.search(rf"(?<!\S){re.escape(selection['token'])}(?=\s|$)", record["prompt"]):
            request["prompt"] = f"{selection['token']} {record['prompt']}"
    if record["page_ids"]:
        request["prompt"] = "\n\n".join(
            [
                request["prompt"],
                *(page_section(config, record, item) for item in record["page_ids"]),
            ]
        )
    return request


def origin(record: dict) -> dict:
    """What marks a conversation as started by this schedule, and whether it may go online."""
    return {
        "schedule_id": record["id"],
        "schedule_title": record["title"],
        "schedule_internet": record["allow_internet"],
    }


def due(config: dict, now: float) -> list[dict]:
    """Enabled schedules of every owner whose ``next_run`` has come, earliest first."""
    found = matching(
        config,
        lambda record: (
            record["enabled"] and record["next_run"] is not None and record["next_run"] <= now
        ),
    )
    return sorted(found, key=lambda record: (record["next_run"], record["id"]))


def awaiting_outcome(config: dict) -> list[dict]:
    """Schedules whose last run was submitted and has not reported how it ended."""
    return matching(
        config,
        lambda record: (
            (record["last_run"] or {}).get("state") == "submitted"
            and record["last_run"]["job_id"] is not None
        ),
    )


def matching(config: dict, wanted: Callable[[dict], bool]) -> list[dict]:
    """The stored schedules of every owner for which ``wanted(record)`` holds."""
    found = []
    try:
        folders = owner_folders(config)
    except APIError:
        logger.warning("The schedules folder is not safe to read; no schedule can run.")
        return []
    for folder in folders:
        try:
            usable = stored(config, folder)
        except APIError:
            logger.warning("A schedules folder (%s) is not safe to read.", folder)
            continue
        found.extend(record for record, _ in usable if wanted(record))
    return found


def counted_failure(current: dict, error: str) -> dict:
    """One more failed run in a row; the limit pauses the schedule and says why."""
    failures = current["failures"] + 1
    if failures < FAILURE_LIMIT or not current["enabled"]:
        return {"failures": failures}
    return {
        "failures": failures,
        "enabled": False,
        "next_run": None,
        "paused_reason": (
            f"Paused after {FAILURE_LIMIT} failed runs in a row (last error: {error}). "
            "Check the route and the project, then turn the schedule back on."
        ),
    }


def after_run(current: dict, now: float, job_id: str | None, error: str | None) -> dict:
    """The changes after a due run: the next occurrence counts from ``now``, never catching up.

    An accepted submit leaves the count of failures alone: the run's outcome decides it.
    """
    changes = {"next_run": next_run(current["cadence"], now)}
    if error is None:
        return {**changes, "last_run": {"job_id": job_id, "at": int(now), "state": "submitted"}}
    return {
        **changes,
        "last_run": {"job_id": None, "at": int(now), "state": "failed", "error": error},
        **counted_failure(current, error),
    }


def after_outcome(
    current: dict, job_id: str, state: str, error: str, needs_you: bool
) -> dict | None:
    """The changes once run ``job_id`` ended; ``None`` when the schedule moved on to another run."""
    last_run = current["last_run"] or {}
    if last_run.get("job_id") != job_id or last_run.get("state") != "submitted":
        return None
    settled = {**last_run, "state": state}
    if needs_you:
        settled["needs_you"] = True
    if state not in FAILED_OUTCOMES:
        return {"last_run": settled, "failures": 0}
    return {"last_run": {**settled, "error": error}, **counted_failure(current, error)}


def record_outcome(
    config: dict, record: dict, *, state: str, error: str, needs_you: bool = False
) -> None:
    """Store how the last run of ``record`` ended, counting it towards pausing the schedule."""
    job_id = record["last_run"]["job_id"]
    rewrite(
        config,
        record["owner"],
        record["id"],
        lambda current: after_outcome(current, job_id, state, error, needs_you),
    )


def skip_due(config: dict, record: dict, now: float) -> None:
    """Move past a due time while the previous run is still active: one run at a time (D15)."""
    update_due(config, record, lambda current: {"next_run": next_run(current["cadence"], now)})


def alerts(config: dict, owner: str, projects: set[str]) -> list[dict]:
    """Attention items: paused schedules and last runs that skipped an approval (D15)."""
    found = []
    try:
        usable = stored(config, owner_folder_name(owner))
    except APIError:
        logger.warning("The schedules folder of an owner is not safe to read.")
        return []
    for record, _ in usable:
        if record["project_id"] not in projects:
            continue
        item = {
            "schedule_id": record["id"],
            "title": record["title"],
            "project_id": record["project_id"],
        }
        last_run = record["last_run"] or {}
        if not record["enabled"] and record["paused_reason"]:
            found.append(
                {**item, "reason": "paused", "job_id": None, "message": record["paused_reason"]}
            )
        elif last_run.get("needs_you"):
            found.append(
                {
                    **item,
                    "reason": "needs_you",
                    "job_id": last_run["job_id"],
                    "message": "The last run skipped an action that needed your approval.",
                }
            )
    return found


def rewrite(
    config: dict, owner: str, schedule_id: str, changes: Callable[[dict], dict | None]
) -> None:
    """Store ``changes(current)`` over the stored schedule; ``None`` or a missing file does nothing."""
    store = repository(config, owner)
    with write_lock:
        text = store.read(schedule_id)
        if text is None:
            return
        try:
            current = load_owned(schedule_id, text, owner)
        except APIError:
            return
        update = changes(current)
        if update is not None:
            store.replace(schedule_id, serialize({**current, **update}))


def update_due(config: dict, record: dict, changes: Callable[[dict], dict]) -> None:
    """Apply ``changes`` only while the schedule still waits for the run ``record`` was read for.

    A schedule that was edited, paused or deleted since it was read keeps what the user made of
    it: the outcome of a run that no longer matches it is dropped.
    """

    def while_waiting(current: dict) -> dict | None:
        waiting = current["enabled"] and current["next_run"] == record["next_run"]
        return changes(current) if waiting else None

    rewrite(config, record["owner"], record["id"], while_waiting)


def finish_due(
    config: dict, record: dict, now: float, *, job_id: str | None = None, error: str | None = None
) -> None:
    """Record the run that was due at ``record["next_run"]``: submitted as ``job_id`` or ``error``."""
    update_due(config, record, lambda current: after_run(current, now, job_id, error))


def pause(config: dict, record: dict, reason: str) -> None:
    changes = {"enabled": False, "next_run": None, "paused_reason": reason}
    update_due(config, record, lambda _current: changes)


def note_manual_run(config: dict, owner: str, schedule_id: str, job_id: str, now: float) -> None:
    """Remember a run-now as the last run; the cadence and the failure count are untouched."""
    last_run = {"job_id": job_id, "at": int(now), "state": "submitted"}
    rewrite(config, owner, schedule_id, lambda _current: {"last_run": last_run})
