"""Tail-owned agents: list, create, replace and delete."""

import asyncio

from starlette.responses import JSONResponse

from .. import tail_agents
from . import api_route, body

NO_STORE = {"Cache-Control": "no-store"}


async def collection(request, service, identity):
    if request.method == "POST":
        data = await body(request)
        created = await asyncio.to_thread(tail_agents.create_agent, service.config, data)
        return JSONResponse(created, status_code=201, headers=NO_STORE)
    agents = await asyncio.to_thread(tail_agents.list_agents, service.config)
    service.identity(request, revalidate=True)
    return JSONResponse({"agents": agents}, headers=NO_STORE)


async def member(request, service, identity):
    data = await body(request)
    operation = (
        tail_agents.delete_agent if request.method == "DELETE" else tail_agents.replace_agent
    )
    result = await asyncio.to_thread(operation, service.config, request.path_params["agent"], data)
    return JSONResponse(result, headers=NO_STORE)


ROUTES = [
    api_route("/v1/tail-agents", collection, methods=["GET", "POST"]),
    api_route("/v1/tail-agents/{agent}", member, methods=["PUT", "DELETE"]),
]
