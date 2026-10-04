"""Space pages: list, create, read, replace and delete the caller's Markdown pages."""

import asyncio

from starlette.responses import JSONResponse

from .. import pages
from . import api_route, body


async def collection(request, service, identity):
    if request.method == "POST":
        data = await body(request, pages.REQUEST_LIMIT)
        project_id = pages.project_of(data)
        service.project(identity, project_id)
        created = await asyncio.to_thread(
            pages.create_page, service.config, identity[0], project_id, data
        )
        return JSONResponse(created, status_code=201)
    project_id = pages.project_of(request.query_params)
    service.project(identity, project_id)
    listing = await asyncio.to_thread(pages.list_pages, service.config, identity[0], project_id)
    service.project(service.identity(request, revalidate=True), project_id)
    return JSONResponse({"pages": listing})


async def member(request, service, identity):
    page_id = request.path_params["page"]
    if request.method == "GET":
        project_id = pages.project_of(request.query_params)
        service.project(identity, project_id)
        page = await asyncio.to_thread(
            pages.read_page, service.config, identity[0], project_id, page_id
        )
        service.project(service.identity(request, revalidate=True), project_id)
        return JSONResponse(page)
    data = await body(request, pages.REQUEST_LIMIT)
    project_id = pages.project_of(data)
    service.project(identity, project_id)
    operation = pages.delete_page if request.method == "DELETE" else pages.replace_page
    result = await asyncio.to_thread(
        operation, service.config, identity[0], project_id, page_id, data
    )
    return JSONResponse(result)


ROUTES = [
    api_route("/v1/pages", collection, methods=["GET", "POST"]),
    api_route("/v1/pages/{page}", member, methods=["GET", "PUT", "DELETE"]),
]
