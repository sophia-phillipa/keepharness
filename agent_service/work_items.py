"""Optional domain references from configured invocation patterns."""

import json
import re
import subprocess
import sys

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
        # One isolated child for the entire request, including startup in the budget.
        # run() kills and reaps the child on timeout; no worker can outlive admission.
        result = subprocess.run(
            [sys.executable, "-I", "-c", _MATCH_REFERENCES],
            input=payload,
            capture_output=True,
            text=True,
            timeout=0.1,
        )
        if result.returncode:
            raise ValueError("pattern worker failed")
        matches = json.loads(result.stdout)
    except (subprocess.TimeoutExpired, OSError, ValueError):
        raise APIError("invalid_work_item_pattern") from None
    values = {validate_reference(value) for value in matches}
    if len(values) > 1:
        raise APIError("ambiguous_work_item")
    return next(iter(values), None)


_MATCH_REFERENCES = r"""
import json, re, sys
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
