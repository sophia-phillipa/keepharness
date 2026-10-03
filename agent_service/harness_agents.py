"""Harness-owned agents: validation, availability, persona and resource items.

An agent is a persona (purpose, instructions, tasks, target output) plus the route it runs
on (backend, model, effort). It is offered to the composer as a conversational agent, which
only prepends text, so every provider can run it. The files live in
``HarnessAgentRepository``; see ``docs/harness-agents.md``.
"""

import hashlib
import json
import logging
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType

from . import maestro
from .errors import APIError
from .persistence.harness_agent_repository import AGENT_ID, MAX_FILE_BYTES, HarnessAgentRepository
from .resources import ENGINES

logger = logging.getLogger(__name__)

MAX_AGENTS = 100
MAX_TASKS = 12
PURPOSE_LENGTH = (1, 300)
INSTRUCTIONS_LENGTH = (1, 20000)
TASK_LENGTH = (1, 200)
TARGET_OUTPUT_LENGTH = (0, 500)
EDITABLE = frozenset(
    {"purpose", "instructions", "tasks", "target_output", "backend", "model", "effort"}
)
# Echoed by a listing, so a client may send them back; they never change what is stored.
READ_ONLY = frozenset(
    {"id", "created_at", "updated_at", "revision", "available", "unavailable_reason"}
)
PROVIDER_NAMES = MappingProxyType(
    {
        "codex": "Codex",
        "claude": "Claude",
        "deepseek": "DeepSeek",
        "gemini": "Gemini",
        "local": "Local model",
    }
)
GROUP = "Your agents"
# The identity the service gives the browser on this computer (``ConversationService.identity``).
LOCAL_CLIENT = "local"
RESOURCE_PREFIX = "harness/agents/"
FOLDER = "harness-agents"
# Before 0.15.0, when KeepHarness was Tail Harness, the folder was "tail-agents" and jobs
# selected an agent as "tail/agents/<id>"; both are carried over once, never merged.
LEGACY_FOLDER = "tail-agents"
LEGACY_RESOURCE_PREFIX = "tail/agents/"
CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# Serializes read-compare-write sequences; the service runs as a single process.
write_lock = threading.Lock()


def invalid(field: str) -> APIError:
    return APIError("harness_agent_invalid", 400, field=field)


def require_local_client(identity: tuple) -> None:
    """Agents are machine-wide; only the browser on this computer may change them."""
    if identity[0] != LOCAL_CLIENT:
        raise APIError("harness_agent_local_only", 403)


def only_harness_agents(selections: object) -> bool:
    """True when ``selections`` is empty or a list of Harness agent references and nothing else."""
    return not selections or (
        isinstance(selections, list)
        and all(
            isinstance(ref, dict) and str(ref.get("id", "")).startswith(RESOURCE_PREFIX)
            for ref in selections
        )
    )


def revision_of(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def repository(config: dict) -> HarnessAgentRepository:
    state = config.get("control_state_dir") or config["state_dir"]
    return HarnessAgentRepository(Path(state) / FOLDER)


def migrate_legacy_folder(config: dict) -> None:
    """Move the agents folder from before the rename unless the current one exists."""
    state = config.get("control_state_dir") or config.get("state_dir")
    if not state:
        return
    legacy, current = Path(state) / LEGACY_FOLDER, Path(state) / FOLDER
    if legacy.is_dir() and not os.path.lexists(current):
        legacy.rename(current)
        logger.warning("Moved %s to %s after the KeepHarness rename", legacy, current)


def upgrade_selection(ref: object) -> object:
    """A stored selection with the resource id this version uses."""
    if isinstance(ref, dict) and str(ref.get("id", "")).startswith(LEGACY_RESOURCE_PREFIX):
        return {**ref, "id": RESOURCE_PREFIX + ref["id"].removeprefix(LEGACY_RESOURCE_PREFIX)}
    return ref


# ----------------------------------------------------------------------------- validation


def clean_text(value: object, field: str, limits: tuple[int, int]) -> str:
    if not isinstance(value, str) or CONTROL_CHARACTERS.search(value):
        raise invalid(field)
    value = value.strip()
    if not limits[0] <= len(value) <= limits[1]:
        raise invalid(field)
    return value


def reject_unknown_fields(body: dict) -> None:
    unknown = sorted(set(body) - EDITABLE - READ_ONLY - {"name"})
    if unknown:
        raise invalid(unknown[0])


def normalize(body: dict, name: str) -> dict:
    """The stored fields of ``name`` from a request or file body; ``harness_agent_invalid`` if not."""
    if "id" in body and body["id"] != name:
        raise invalid("id")
    tasks = body.get("tasks", [])
    if not isinstance(tasks, list) or len(tasks) > MAX_TASKS:
        raise invalid("tasks")
    fields = {
        "id": name,
        "name": name,
        "purpose": clean_text(body.get("purpose"), "purpose", PURPOSE_LENGTH),
        "instructions": clean_text(body.get("instructions"), "instructions", INSTRUCTIONS_LENGTH),
        "tasks": [clean_text(task, "tasks", TASK_LENGTH) for task in tasks],
        "target_output": clean_text(
            body.get("target_output", ""), "target_output", TARGET_OUTPUT_LENGTH
        ),
    }
    for field in ("backend", "model", "effort"):
        value = body.get(field)
        if not isinstance(value, str) or not value:
            raise invalid(field)
        fields[field] = value
    if fields["backend"] not in ENGINES:
        raise invalid("backend")
    return fields


def serialize(record: dict) -> str:
    text = json.dumps(record, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    if len(text.encode()) > MAX_FILE_BYTES:
        # Multi-byte characters can overflow the file limit; the instructions are the bulk.
        raise invalid("instructions")
    return text


def load(agent_id: str, text: str) -> dict:
    """A stored record, held to the request limits; ``ValueError`` when the file is unusable."""
    try:
        data = json.loads(text)
        if not isinstance(data, dict) or data.get("id") != agent_id or data.get("name") != agent_id:
            raise ValueError("invalid_harness_agent")
        record = normalize(data, agent_id)
    except (ValueError, RecursionError, APIError):
        raise ValueError("invalid_harness_agent") from None
    for key in ("created_at", "updated_at"):
        record[key] = data[key] if isinstance(data.get(key), str) else ""
    return record


# ----------------------------------------------------------------------------- availability


def offered(config: dict, project_id: str | None = None) -> dict[tuple[str, str], list[str]]:
    """``{(backend, model): efforts}`` for enabled services, limited to one project if given."""
    return {
        (backend, model): maestro.model_efforts(config, backend, model)
        for backend, service in config.get("services", {}).items()
        if service.get("enabled")
        and (project_id is None or project_id in service.get("projects", []))
        for model in service.get("models", [])
    }


def availability_problem(
    offers: dict[tuple[str, str], list[str]], record: dict
) -> tuple[str, str] | None:
    """``(field, reason)`` for the first part of the route that is not offered, else None."""
    backend, model, effort = record["backend"], record["model"], record["effort"]
    provider = PROVIDER_NAMES[backend]
    if not any(key[0] == backend for key in offers):
        return "backend", provider + " is not enabled."
    if (backend, model) not in offers:
        return "model", "Model " + model + " is not offered by " + provider + "."
    if effort not in offers[(backend, model)]:
        return "effort", "Effort " + effort + " is not offered for " + model + "."
    return None


def describe(record: dict, text: str, offers: dict[tuple[str, str], list[str]]) -> dict:
    problem = availability_problem(offers, record)
    return {
        **record,
        "revision": revision_of(text),
        "available": problem is None,
        "unavailable_reason": problem[1] if problem else "",
    }


def compose_persona(record: dict) -> str:
    lines = [
        'You are the agent "' + record["name"] + '". Purpose: ' + record["purpose"],
        "Follow these instructions:",
        record["instructions"],
    ]
    if record["tasks"]:
        lines += ["Tasks you handle:", *("- " + task for task in record["tasks"])]
    if record["target_output"]:
        lines.append("Target output: " + record["target_output"])
    return "\n".join(lines)


# ----------------------------------------------------------------------------- operations


def stored_agents(config: dict) -> tuple[list[tuple[dict, str]], list[str]]:
    """Usable stored agents as ``(record, text)`` and the ids of files that were skipped."""
    entries, skipped = repository(config).read_all(MAX_AGENTS)
    usable = []
    for agent_id, text in entries:
        try:
            usable.append((load(agent_id, text), text))
        except ValueError:
            skipped.append(agent_id)
    return usable, sorted(skipped)


def list_agents(config: dict) -> list[dict]:
    stored, skipped = stored_agents(config)
    if skipped:
        logger.warning("Skipped unusable Harness agent files: %s", ", ".join(skipped))
    offers = offered(config)
    return [describe(record, text, offers) for record, text in stored]


def require_available(config: dict, record: dict) -> None:
    problem = availability_problem(offered(config), record)
    if problem:
        raise invalid(problem[0])


def require_known(agent_id: str) -> None:
    if not AGENT_ID.fullmatch(agent_id):
        raise APIError("harness_agent_not_found", 404)


def require_revision(body: dict) -> str:
    revision = body.get("revision")
    if not isinstance(revision, str) or not revision:
        raise invalid("revision")
    return revision


def read_current(store: HarnessAgentRepository, agent_id: str, revision: str) -> str:
    """The stored text, after checking that it is still the revision the caller saw."""
    text = store.read(agent_id)
    if text is None:
        raise APIError("harness_agent_not_found", 404)
    if revision_of(text) != revision:
        raise APIError("harness_agent_changed", 409)
    return text


def create_agent(config: dict, body: dict) -> dict:
    name = body.get("name")
    if not isinstance(name, str) or not AGENT_ID.fullmatch(name):
        raise invalid("name")
    reject_unknown_fields(body)
    fields = normalize(body, name)
    require_available(config, fields)
    now = timestamp()
    record = {**fields, "created_at": now, "updated_at": now}
    text = serialize(record)
    store = repository(config)
    with write_lock:
        existing = store.ids()
        if name in existing:
            raise APIError("harness_agent_exists", 409)
        if len(existing) >= MAX_AGENTS:
            raise APIError("harness_agent_limit", 409)
        try:
            store.create(name, text)
        except FileExistsError:
            raise APIError("harness_agent_exists", 409) from None
    return describe(record, text, offered(config))


def replace_agent(config: dict, agent_id: str, body: dict) -> dict:
    require_known(agent_id)
    if body.get("name", agent_id) != agent_id:
        raise invalid("name")
    reject_unknown_fields(body)
    revision = require_revision(body)
    fields = normalize(body, agent_id)
    require_available(config, fields)
    now = timestamp()
    store = repository(config)
    with write_lock:
        current = read_current(store, agent_id, revision)
        try:
            created_at = load(agent_id, current)["created_at"]
        except ValueError:
            created_at = now
        record = {**fields, "created_at": created_at or now, "updated_at": now}
        text = serialize(record)
        store.replace(agent_id, text)
    return describe(record, text, offered(config))


def delete_agent(config: dict, agent_id: str, body: dict) -> dict:
    require_known(agent_id)
    revision = require_revision(body)
    store = repository(config)
    with write_lock:
        read_current(store, agent_id, revision)
        store.delete(agent_id)
    return {"deleted": True}


# ----------------------------------------------------------------------------- resource items


def resource_item(
    record: dict, text: str, problem: tuple[str, str] | None, *, private: bool
) -> dict:
    resource_id = RESOURCE_PREFIX + record["name"]
    reason = problem[1] if problem else ""
    item = {
        "id": resource_id,
        "resource_id": resource_id,
        "revision": revision_of(text),
        "kind": "agent",
        "name": record["name"],
        "description": record["purpose"],
        "scope": "harness",
        "origin": "harness",
        # A logical name: the persona travels in the prompt, so no host path is disclosed.
        "source": resource_id + ".json",
        "namespace": "",
        "argument_hint": "",
        "backend": record["backend"],
        "model": record["model"],
        "effort": record["effort"],
        "mode": "conversational",
        "native_command": False,
        "maintenance": False,
        "group": GROUP,
        "selectable": problem is None,
        "unavailable_reason": reason,
        "preflight_hint": (
            "Edit the agent to choose a provider, model and effort that are available."
            if problem
            else "Runs on " + record["model"] + " · " + PROVIDER_NAMES[record["backend"]]
        ),
    }
    if private:
        item.update(_text=text, _body=compose_persona(record), _meta=record)
    return item


def add_resources(result: dict, config: dict, project_id: str, *, private: bool) -> None:
    """Append the Harness agents to a discovery ``result``; they exist for every backend."""
    if not (config.get("control_state_dir") or config.get("state_dir")):
        return
    try:
        stored, skipped = stored_agents(config)
    except (APIError, OSError):
        result["warnings"].append(
            "Harness agents are unavailable: the agents folder is not safe to read."
        )
        return
    result["warnings"].extend(
        "Could not read the Harness agent " + agent_id for agent_id in skipped
    )
    offers = offered(config, project_id)
    result["items"].extend(
        resource_item(record, text, availability_problem(offers, record), private=private)
        for record, text in stored
    )
