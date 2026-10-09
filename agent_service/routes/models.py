"""Model catalog and provider usage."""

from starlette.responses import JSONResponse

from adapters.shared.provider_setup import claude_access_settings, codex_access_settings

from .. import approval_policy
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
    for model in models:
        backend = model.get("backend")
        if backend not in ("codex", "claude"):
            continue
        access_modes = {}
        for mode in ("ask", "auto", "full", "read_only"):
            permissions = approval_policy.effective_permissions(model.get("permissions", {}), mode)
            unrestricted = config.get(backend, {}).get("unrestricted") is True
            access_modes[mode] = (
                codex_access_settings(
                    mode,
                    permissions,
                    unrestricted and mode == "full" and bool(permissions.get("shell")),
                )
                if backend == "codex"
                else claude_access_settings(mode, permissions, unrestricted)
            )
        model["access_modes"] = access_modes
    service.project(service.identity(request, revalidate=True), project_id)
    return JSONResponse(
        {
            "models": models,
            "project_id": project_id,
            "providers": {
                p: c.get("enabled", False) for p, c in config.get("services", {}).items()
            },
            "uploads_enabled": service.uploads_enabled(project_id),
            # The access menu offers Full access once the owner enabled it (D11).
            "full_access": not approval_policy.mode_disabled(config, "full"),
            # Lets the UI default owner-only choices (project folders in a handoff) without Full mode.
            "local_owner": True,
            "admin_url": config.get("admin_url"),
        }
    )


ROUTES = [
    api_route("/v1/usage", usage),
    api_route("/v1/models", models),
]
