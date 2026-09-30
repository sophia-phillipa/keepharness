"""Optional domain references from configured invocation patterns."""

import re

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
    matches = set()
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
                values = re.finditer(pattern, invocation.get("args", ""))
                for match in values:
                    matches.add(validate_reference(match.group(0)))
                    if len(matches) > 1:
                        raise APIError("ambiguous_work_item")
            except (re.error, ValueError):
                raise APIError("invalid_work_item_pattern") from None
    return next(iter(matches), None)
