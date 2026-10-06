"""Read-only skills listing for Customize > Skills: whitelisted fields, never a path."""
import asyncio
import logging
from pathlib import Path

from agent_service.resources import discover

from .catalog_admin import catalog_config

NO_PROJECT = "sem-projeto"
SCOPES = ("project", "catalog", "user")  # harness-scope items carry no skills
logger = logging.getLogger(__name__)


def list_skills(config, project_id):
    warnings = []
    if project_id not in config["projects"]:
        if project_id:
            warnings.append("Unknown project; showing the skills available without a project.")
        project_id = NO_PROJECT
    found = {}
    for provider, service in config.get("services", {}).items():
        if not service.get("enabled") or project_id not in service.get("projects", []):
            continue
        mode = service.get("mode", "native")
        if mode != "native":  # only native executions load skill files
            continue
        try:
            discovered = discover(
                config, project_id, provider, execution_mode=mode, include_workflows=False, owner=True
            )
        except Exception:  # one broken provider must not hide the others; details stay in the log
            logger.exception("Skill discovery failed for %s", provider)
            warnings.append(f"Could not list the skills of {provider}.")
            continue
        if discovered["warnings"]:  # discovery warnings name host paths, so only the fact is shown
            warnings.append(f"Some {provider} resources could not be read.")
        for item in discovered["items"]:
            if item["kind"] != "skill" or item["scope"] not in SCOPES or not item["selectable"]:
                continue
            # One entry per skill file; the path is only a key and is never returned.
            entry = found.setdefault(
                (item["scope"], str(Path(item["source"]).resolve())),
                {
                    "name": item["name"],
                    "description": item["description"],
                    "scope": item["scope"],
                    "providers": set(),
                },
            )
            entry["providers"].add(provider)
    items = [{**entry, "providers": sorted(entry["providers"])} for entry in found.values()]
    items.sort(key=lambda entry: (SCOPES.index(entry["scope"]), entry["name"].casefold()))
    return {"items": items, "warnings": warnings}


async def read_customize_skills(request, manager):
    project_id = request.query_params.get("project_id", "")
    return await asyncio.to_thread(list_skills, catalog_config(manager), project_id)
