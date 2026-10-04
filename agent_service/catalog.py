"""Public project catalog assembled by the canonical resource discovery path."""

from . import resources


def catalog(config, project, project_id=None, *, owner=False):
    if project_id is None:
        project_id = next(
            (key for key, value in config.get("projects", {}).items() if value is project), None
        )
    result = {
        "agents": [],
        "skills": [],
        "warnings": [],
        "items": [],
        "scope": "Selected project, its trusted catalogs, and configured engines.",
    }
    for provider, service in config.get("services", {}).items():
        if not service.get("enabled"):
            continue
        result["agents"].append(
            {
                "name": provider,
                "description": ", ".join(service.get("models", [])),
                "source": "Service configuration",
                "status": "Configured",
            }
        )
        if project_id is None or project_id not in service.get("projects", []):
            continue
        discovered = resources.discover(config, project_id, provider, owner=owner)
        result["warnings"].extend(discovered["warnings"])
        existing = {value["resource_id"] for value in result["items"]}
        for item in discovered["items"]:
            if item["resource_id"] not in existing:
                result["items"].append(item)
                existing.add(item["resource_id"])
            # Harness agents are provider-independent: they appear once, in ``items``.
            if item["kind"] not in ("agent", "skill") or item["scope"] == "harness":
                continue
            result[item["kind"] + "s"].append(
                {
                    "name": item["name"],
                    "description": item["description"] or "No description provided in the file.",
                    "source": item["source"],
                    "status": "Available" if item["selectable"] else item["unavailable_reason"],
                }
            )
    return result
