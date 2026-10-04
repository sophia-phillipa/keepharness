"""Optional domain references from configured invocation patterns."""

import asyncio
import contextvars
import json
import re
import subprocess
import sys
import threading

from .errors import APIError


def validate_pattern(pattern):
    if pattern is None:
        return None
    if not isinstance(pattern, str) or not pattern or len(pattern) > 512:
        raise ValueError("Invalid work-item pattern.")
    try:
        re.compile(pattern)
    except re.error:
        raise ValueError("Invalid work-item pattern.") from None
    return pattern


def validate_reference(value):
    if value is not None and (
        not isinstance(value, str)
        or not 1 <= len(value) <= 128
        or value != value.strip()
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise APIError("invalid_work_item")
    return value


def _match_payload(config, project, data):
    """The JSON the worker matches, or None when no configured pattern applies."""
    requests = []
    catalogs = {
        catalog["id"]: catalog
        for catalog in config.get("catalogs", [])
        if isinstance(catalog, dict)
        and catalog.get("trusted") is True
        and catalog.get("id") in project.get("catalogs", [])
    }
    for invocation in data.get("invocations", []):
        patterns = [project.get("work_item_pattern")]
        parts = invocation.get("resource_id", "").split("/")
        if len(parts) > 2 and parts[0] == "catalog":
            patterns.append(catalogs.get(parts[1], {}).get("work_item_pattern"))
        for pattern in patterns:
            if pattern is None:
                continue
            try:
                validate_pattern(pattern)
                requests.append([pattern, invocation.get("args", "")])
            except (re.error, ValueError):
                raise APIError("invalid_work_item_pattern") from None
    if not requests:
        return None
    payload = json.dumps(requests)
    if len(payload.encode()) > 2_000_000:
        raise APIError("payload_limit", 413)
    return payload


def _reference(payload):
    """Match in the worker, at most ``_MAX_WORKERS`` at a time; this call blocks."""
    if not _slots.acquire(timeout=_ADMISSION_SECONDS):
        raise APIError("queue_full", 429, 5)
    try:
        matches = json.loads(_run_worker(payload))
    except (subprocess.TimeoutExpired, OSError, ValueError):
        raise APIError("invalid_work_item_pattern") from None
    finally:
        _slots.release()
    values = {validate_reference(value) for value in matches}
    if len(values) > 1:
        raise APIError("ambiguous_work_item")
    return next(iter(values), None)


# What prematch_reference() computed for this task: (payload, reference or the APIError).
_prematched = contextvars.ContextVar("work_item_prematched", default=None)


def invocation_reference(config, project, data):
    """Blocking: async callers await ``prematch_reference`` first so this returns at once."""
    payload = _match_payload(config, project, data)
    if payload is None:
        return None
    prematched = _prematched.get()
    if prematched is None or prematched[0] != payload:
        return _reference(payload)
    _prematched.set(None)
    if isinstance(prematched[1], APIError):
        raise prematched[1]
    return prematched[1]


async def prematch_reference(config, project, data):
    """Run the worker off the event loop for the next ``invocation_reference`` in this task.

    Only the outcome is kept, a failure included, so the synchronous call that follows raises
    the same error without matching again. Anything that is not a plain match failure is left
    for that call to raise in its usual order.
    """
    try:
        payload = _match_payload(config, project, data)
    except Exception:
        return
    if payload is None:
        return
    try:
        outcome = await asyncio.to_thread(_reference, payload)
    except APIError as error:
        outcome = error
    _prematched.set((payload, outcome))


# Interpreter start-up is not attacker-controlled, so a busy host only delays it: the
# allowance is generous. Matching is what backtracking can inflate, so only it is tight.
_STARTUP_SECONDS = 10.0
_MATCH_SECONDS = 0.5
# A submit that finds every worker busy for this long is retryable, not an invalid pattern.
_MAX_WORKERS = 4
_ADMISSION_SECONDS = 2.0
_slots = threading.BoundedSemaphore(_MAX_WORKERS)


def _run_worker(payload):
    """Run the matcher in one isolated child and return its stdout.

    The child prints a ready line once the interpreter and ``re`` are loaded; the match
    deadline starts only then, so a loaded host never rejects a valid pattern.
    Every exit path kills and reaps the child; no worker can outlive admission.
    """
    with subprocess.Popen(
        [sys.executable, "-I", "-c", _MATCH_REFERENCES],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    ) as child:
        startup_guard = threading.Timer(_STARTUP_SECONDS, child.kill)
        startup_guard.start()
        try:
            if child.stdout.readline() != "ready\n":
                raise ValueError("pattern worker failed to start")
            startup_guard.cancel()
            output, _ = child.communicate(payload, timeout=_MATCH_SECONDS)
            if child.returncode:
                raise ValueError("pattern worker failed")
            return output
        except BaseException:
            child.kill()
            raise
        finally:
            startup_guard.cancel()


_MATCH_REFERENCES = r"""
import json, re, sys
print("ready", flush=True)
matches = set()
for pattern, text in json.load(sys.stdin):
    for match in re.finditer(pattern, text):
        value = match.group(0)
        if len(value) > 128:
            value = value[:129]
        matches.add(value)
        if len(matches) > 1 or not value or len(value) > 128:
            print(json.dumps(list(matches)))
            sys.exit(0)
print(json.dumps(list(matches)))
"""
