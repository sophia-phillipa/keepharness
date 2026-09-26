"""Model catalog and provider usage."""

from starlette.responses import JSONResponse

from . import api_route


async def usage(request, service, identity):
    backend = request.query_params.get("backend", "codex")
    if backend == "claude":
        return JSONResponse(await service.claude_quota(identity[0]))
    if backend != "codex":
        return JSONResponse(
            {"provider": backend, "available": False, "reason": "quota_not_reported"}
        )
    return JSONResponse(await service.quota())


async def models(request, service, identity):
    config = service.config
    project_id = request.query_params.get("project_id") or (
        "sem-projeto"
        if "sem-projeto" in identity[1]["projects"]
        else next(iter(identity[1]["projects"]), None)
    )
    service.project(identity, project_id)
    return JSONResponse(
        {
            "models": await service.models_with_context(project_id),
            "project_id": project_id,
            "providers": {
                p: c.get("enabled", False) for p, c in config.get("services", {}).items()
            },
            "uploads_enabled": service.uploads_enabled(project_id),
            "admin_url": config.get("admin_url"),
        }
    )


ROUTES = [
    api_route("/v1/usage", usage),
    api_route("/v1/models", models),
]
