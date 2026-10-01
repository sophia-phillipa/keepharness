"""Owner inspection and human-only reconciliation; no dispatch or credential API."""

from starlette.responses import JSONResponse

from ..approval_sessions import require_approval_session
from . import api_route, body


async def effects(request, service, identity):
    job_id = request.path_params["job"]
    service.job(identity, job_id)
    return JSONResponse(
        {"effects": service.effects.for_job(job_id)}, headers={"Cache-Control": "no-store"}
    )


async def reconcile(request, service, identity):
    require_approval_session(request, service.config, identity)
    data = await body(request)
    require_approval_session(request, service.config, identity, revalidate=True)
    result = await service.effects.reconcile(
        request.path_params["effect"], identity, data.get("decision")
    )
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


ROUTES = [
    api_route("/v1/jobs/{job}/effects", effects),
    api_route("/v1/effects/{effect}/reconcile", reconcile, ["POST"]),
]
