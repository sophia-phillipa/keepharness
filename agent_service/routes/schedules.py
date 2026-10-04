"""Scheduled tasks: list, create, change and delete the caller's schedules, and run one now."""

import asyncio

from starlette.responses import JSONResponse

from .. import schedules
from ..services import scheduler
from . import api_route, body


def check_project(service, identity, data):
    """A project named in the body must be one the caller may use; the domain checks its shape."""
    project_id = data.get("project_id")
    if isinstance(project_id, str):
        service.project(identity, project_id)


async def collection(request, service, identity):
    if request.method == "POST":
        data = await body(request)
        check_project(service, identity, data)
        created = await asyncio.to_thread(
            schedules.create_schedule, service.config, identity[0], data
        )
        return JSONResponse(created, status_code=201)
    listing = await asyncio.to_thread(schedules.list_schedules, service.config, identity[0])
    service.identity(request, revalidate=True)
    return JSONResponse({"schedules": listing})


async def member(request, service, identity):
    data = await body(request)
    check_project(service, identity, data)
    operation = (
        schedules.delete_schedule if request.method == "DELETE" else schedules.replace_schedule
    )
    result = await asyncio.to_thread(
        operation, service.config, identity[0], request.path_params["schedule"], data
    )
    return JSONResponse(result)


async def run_now(request, service, identity):
    schedule_id = request.path_params["schedule"]
    record = await asyncio.to_thread(
        schedules.owned_record, service.config, identity[0], schedule_id
    )
    service.project(identity, record["project_id"])
    submitted = await scheduler.submit(
        service, identity, record, request.headers.get("idempotency-key")
    )
    await asyncio.to_thread(
        schedules.note_manual_run,
        service.config,
        identity[0],
        schedule_id,
        submitted["job_id"],
        schedules.clock(),
    )
    return JSONResponse({"job_id": submitted["job_id"]}, status_code=202)


ROUTES = [
    api_route("/v1/schedules", collection, methods=["GET", "POST"]),
    api_route("/v1/schedules/{schedule}", member, methods=["PUT", "DELETE"]),
    api_route("/v1/schedules/{schedule}/run", run_now, methods=["POST"]),
]
