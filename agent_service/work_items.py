"""Optional domain references from configured invocation patterns."""

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


def invocation_reference(config, project, data):
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
    try:
        matches = json.loads(_run_worker(payload))
    except (subprocess.TimeoutExpired, OSError, ValueError):
        raise APIError("invalid_work_item_pattern") from None
    values = {validate_reference(value) for value in matches}
    if len(values) > 1:
        raise APIError("ambiguous_work_item")
    return next(iter(values), None)


# Interpreter start-up is not attacker-controlled, so a busy host only delays it: the
# allowance is generous. Matching is what backtracking can inflate, so only it is tight.
_STARTUP_SECONDS = 10.0
_MATCH_SECONDS = 0.5


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
            ready = child.stdout.readline()
        finally:
            startup_guard.cancel()
        try:
            if ready != "ready\n":
                raise ValueError("pattern worker failed to start")
            output, _ = child.communicate(payload, timeout=_MATCH_SECONDS)
            if child.returncode:
                raise ValueError("pattern worker failed")
            return output
        except BaseException:
            child.kill()
            raise


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
