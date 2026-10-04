"""Model catalog and provider usage."""

from starlette.responses import JSONResponse

from .. import approval_policy, maestro
from ..errors import ToolError
from ..harness_agents import LOCAL_CLIENT
from . import api_route


async def usage(request, service, identity):
    backend = request.query_params.get("backend", "codex")
    if backend == "claude":
        result = await service.claude_quota(identity[0])
    elif backend == "deepseek":
        result = await service.deepseek_quota()
    elif backend != "codex":
        return JSONResponse(
            {"provider": backend, "available": False, "reason": "quota_not_reported"}
        )
    else:
        result = await service.quota()
    service.identity(request, revalidate=True)
    return JSONResponse(result)


async def models(request, service, identity):
    config = service.config
    project_id = request.query_params.get("project_id") or (
        "sem-projeto"
        if "sem-projeto" in identity[1]["projects"]
        else next(iter(identity[1]["projects"]), None)
    )
    service.project(identity, project_id)
    models = await service.models_with_context(project_id)
    service.project(service.identity(request, revalidate=True), project_id)
    try:
        maestro.coordinator(config, project_id)
        planning_available = True
    except ToolError:
        planning_available = False
    return JSONResponse(
        {
            "maestro": planning_available,
            "models": models,
            "project_id": project_id,
            "providers": {
                p: c.get("enabled", False) for p, c in config.get("services", {}).items()
            },
            "uploads_enabled": service.uploads_enabled(project_id),
            # The access menu offers Full access only to the owner, once enabled (D11).
            "full_access": identity[0] == LOCAL_CLIENT
            and not approval_policy.mode_disabled(config, "full"),
            "admin_url": config.get("admin_url"),
        }
    )


ROUTES = [
    api_route("/v1/usage", usage),
    api_route("/v1/models", models),
]
