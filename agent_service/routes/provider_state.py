"""Provider trust writes behind the harness owner and browser-origin gate."""

from pathlib import Path
from types import SimpleNamespace

from starlette.responses import JSONResponse

from control.provider_state import ProviderStateService
from control.routes import approve_provider_mcp, trust_provider_project

from . import api_route, body


def state_service(service):
    if not hasattr(service, "provider_state"):
        service.provider_state = ProviderStateService(
            Path(service.config.get("control_state_dir") or service.config["state_dir"]),
            lambda: [{"id": key, **value} for key, value in service.config["projects"].items()],
            track_notices=False,
        )
    return service.provider_state


async def read(request, service, identity):
    project_id = request.query_params.get("project_id", "sem-projeto")
    service.project(identity, project_id)
    result = await state_service(service).read(request.query_params.get("provider", ""), project_id)
    service.identity(request, revalidate=True)
    return result if isinstance(result, JSONResponse) else JSONResponse(result)


async def write(request, service, identity):
    data = await body(request)
    service.project(service.identity(request, revalidate=True), data.get("project_id"))
    manager = SimpleNamespace(provider_state=state_service(service))
    handler = (
        trust_provider_project if request.url.path.endswith("/trust") else approve_provider_mcp
    )
    result = await handler(request, manager, data)
    return result if isinstance(result, JSONResponse) else JSONResponse(result)


ROUTES = [
    api_route("/v1/provider-state", read, ["GET"]),
    api_route("/v1/provider-state/trust", write, ["POST"]),
    api_route("/v1/provider-state/mcp-approvals", write, ["POST"]),
]
