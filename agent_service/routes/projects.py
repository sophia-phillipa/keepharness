"""Projects, project folders, services, catalog, resources and integrations."""

import asyncio
import subprocess
import time
from pathlib import Path

from starlette.responses import JSONResponse

from .. import harness_agents, maestro, service_control, workspaces
from ..approval_sessions import require_approval_session
from ..catalog import catalog as project_catalog_items
from ..errors import APIError
from ..persistence.db import encoded, private_file
from ..project_icons import discover_project_icon
from ..resources import without_host_paths
from ..secret_vault import redact_secrets
from . import api_route, body

PROJECTS_LOCAL_ONLY = "project_management_local_only"


def owner_view(identity, value):
    """Absolute host paths stay with the local owner (SEC-R2-4)."""
    if identity[0] == harness_agents.LOCAL_CLIENT:
        return value
    return without_host_paths(value)


def project_git(root):
    """Read the branch (or detached revision) without changing the repository."""
    if not root:
        return None
    try:
        result = subprocess.run(
            ["git", "-C", root, "symbolic-ref", "--quiet", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode:
            result = subprocess.run(
                ["git", "-C", root, "rev-parse", "--short", "HEAD"],
                capture_output=True,
                text=True,
                timeout=2,
            )
        return result.stdout.strip() if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


async def resources(request, service, identity):
    params = request.query_params
    value = await asyncio.to_thread(
        service.resource_catalog,
        identity,
        params.get("project_id"),
        params.get("backend"),
        params.get("model"),
        params.get("execution_mode"),
        params.get("access_mode"),
    )
    service.project(service.identity(request, revalidate=True), params.get("project_id"))
    return JSONResponse(owner_view(identity, value), headers={"Cache-Control": "no-store"})


async def integrations(request, service, identity):
    params = request.query_params
    value = await asyncio.to_thread(
        service.integration_view,
        identity,
        params.get("project_id"),
        params.get("backend"),
        params.get("model"),
        params.get("execution_mode"),
        params.get("access_mode"),
    )
    service.project(service.identity(request, revalidate=True), params.get("project_id"))
    return JSONResponse(value, headers={"Cache-Control": "no-store"})


async def catalog(request, service, identity):
    config = service.config
    project_id = request.query_params.get("project_id")
    service.project(identity, project_id)
    value = await asyncio.to_thread(
        project_catalog_items,
        config,
        config["projects"][project_id],
        project_id,
        owner=identity[0] == harness_agents.LOCAL_CLIENT,
    )
    service.project(service.identity(request, revalidate=True), project_id)
    return JSONResponse(owner_view(identity, value), headers={"Cache-Control": "no-store"})


async def project_directories(request, service, identity):
    config = service.config
    harness_agents.require_local_client(identity, PROJECTS_LOCAL_ONLY)
    if not config.get("project_registration"):
        raise APIError("project_registration_disabled", 403)
    params = request.query_params
    root_id = params.get("root_id", "home")
    root = workspaces.system_root(root_id)
    folder = params.get("path", "")
    query = params.get("q", "")
    if len(query) > 200:
        raise APIError("invalid_query")
    if Path(folder).is_absolute():
        try:
            folder = str(Path(folder).resolve().relative_to(root.resolve()))
        except ValueError:
            raise APIError("path_not_authorized", 403)
    try:
        start = int(params.get("start", 1))
        limit = int(params.get("limit", 100))
    except ValueError:
        raise APIError("invalid_range")
    result = await asyncio.to_thread(
        workspaces.browse_system, root, folder, start, limit, True, query
    )
    service.identity(request, revalidate=True)
    if not config.get("project_registration"):
        raise APIError("project_registration_disabled", 403)
    for entry in result["entries"]:
        entry["absolute_path"] = str(root / entry["path"])
    return JSONResponse(
        {
            "roots": [
                {
                    "id": rid,
                    "label": {
                        "home": "Personal folder",
                        "media-user": "External media",
                    }[rid],
                }
                for rid, _ in workspaces.system_roots()
            ],
            "root_id": root_id,
            "absolute_path": str(workspaces.system_path(root, folder)),
            **result,
        }
    )


async def project_revision(request, service, identity):
    project_id = request.query_params.get("project_id")
    spec = service.project(identity, project_id)
    revision = await asyncio.to_thread(project_git, spec.get("root"))
    service.project(service.identity(request, revalidate=True), project_id)
    return JSONResponse({"revision": revision}, headers={"Cache-Control": "no-store"})


async def projects(request, service, identity):
    config = service.config
    if request.method in ("PATCH", "POST"):
        # Registering or repointing a folder reaches the host's disk: the owner only.
        harness_agents.require_local_client(identity, PROJECTS_LOCAL_ONLY)
    if request.method == "PATCH":
        data = await body(request)
        if not isinstance(data, dict) or not isinstance(data.get("project_id"), str):
            raise APIError("invalid_project")
        pid = data["project_id"]
        service.project(identity, pid)
        if pid == "sem-projeto":
            raise APIError("project_edit_forbidden", 403)
        if pid in service.deleting_project_folders or service.conversation_repository.project_busy(
            pid
        ):
            raise APIError("project_busy", 409)
        return JSONResponse({"project_id": service.add_project(data, pid)})
    if request.method == "POST":
        return JSONResponse(
            {"project_id": service.add_project(await body(request))}, status_code=201
        )
    project_catalog = {
        "projects": [
            p
            for p in identity[1]["projects"]
            if p in config["projects"] and p not in service.deleted_project_folders
        ],
        "details": {
            p: {
                "label": config["projects"][p].get("label", p),
                "root": config["projects"][p].get("root"),
                "additional_roots": config["projects"][p].get("additional_roots", []),
                "apply_changes": bool(config["projects"][p].get("apply_changes")),
            }
            for p in identity[1]["projects"]
            if p in config["projects"] and p not in service.deleted_project_folders
        },
    }
    seen = {}
    for pid, detail in project_catalog["details"].items():
        detail["canonical_id"] = pid
        if detail.get("root"):
            try:
                roots = [detail["root"], *detail["additional_roots"]]
                physical = tuple(
                    (info.st_dev, info.st_ino) for info in (Path(root).stat() for root in roots)
                )
                key = (detail["label"].casefold(), physical)
                detail["canonical_id"] = seen.setdefault(key, pid)
            except OSError:
                pass
        canonical = detail["canonical_id"]
        detail["icon"] = (
            project_catalog["details"][canonical]["icon"]
            if canonical != pid
            else await asyncio.to_thread(discover_project_icon, detail.get("root"))
        )
    current = service.identity(request, revalidate=True)
    for pid in project_catalog["projects"]:
        service.project(current, pid)
    return JSONResponse(project_catalog)


async def project_folder(request, service, identity):
    project = request.query_params.get("project_id")
    if request.method == "DELETE":
        harness_agents.require_local_client(identity, PROJECTS_LOCAL_ONLY)
        return JSONResponse(
            await service.delete_project_folder(identity, project, await body(request))
        )
    return JSONResponse(service.project_folder_deletion(identity, project))


async def services(request, service, identity):
    config = service.config
    data = await body(request)
    project = data.get("project_id")
    spec = service.project(identity, project)
    permitted = any(
        m.get("mode") == "native" and m["permissions"].get("shell")
        for m in maestro.candidates(config, project)
    )
    if not permitted:
        raise APIError("service_control_denied", 403)
    action = data.get("action", "list")
    if action in ("start", "stop", "restart"):
        # Host services are machine-wide: only the owner on this computer changes them, and with
        # the same human authority as answering an approval (a body flag is the caller's claim).
        require_approval_session(request, config, identity, revalidate=True)
        harness_agents.require_local_client(identity, "service_control_denied")
    result = redact_secrets(
        await service_control.operate(config, spec, action, data.get("unit", ""))
    )
    with open(service.root / "service-actions.jsonl", "a", opener=private_file) as log:
        log.write(
            encoded(
                redact_secrets(
                    {
                        "time": time.time(),
                        "owner": identity[0],
                        "project": project,
                        **result,
                    }
                )
            )
            + "\n"
        )
    service.project(service.identity(request, revalidate=True), project)
    if not any(
        m.get("mode") == "native" and m["permissions"].get("shell")
        for m in maestro.candidates(service.config, project)
    ):
        raise APIError("service_control_denied", 403)
    return JSONResponse(result)


ROUTES = [
    api_route("/v1/resources", resources),
    api_route("/v1/integrations", integrations),
    api_route("/v1/catalog", catalog),
    api_route("/v1/project-directories", project_directories),
    api_route("/v1/project-git", project_revision),
    api_route("/v1/projects", projects, methods=["GET", "POST", "PATCH"]),
    api_route("/v1/project-folder", project_folder, methods=["GET", "DELETE"]),
    api_route("/v1/services", services, methods=["POST"]),
]
