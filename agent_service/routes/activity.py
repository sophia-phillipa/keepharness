"""Activity across conversations and manual project-scoped run references."""

from starlette.responses import JSONResponse

from ..errors import APIError
from . import api_route, body


async def activity(request, service, identity):
    return JSONResponse(
        service.activity(
            identity, request.query_params.get("project_id"), request.query_params.get("work_item")
        )
    )


async def work_item(request, service, identity):
    data = await body(request)
    if not isinstance(data, dict) or "work_item" not in data:
        raise APIError("invalid_work_item")
    return JSONResponse(
        service.tag_work_item(identity, request.path_params["job"], data["work_item"])
    )


ROUTES = [
    api_route("/v1/activity", activity),
    api_route("/v1/jobs/{job}/work-item", work_item, methods=["PATCH"]),
]
