"""Harness-owned agents: list, create, replace and delete."""

import asyncio

from starlette.responses import JSONResponse

from .. import harness_agents
from . import api_route, body


async def collection(request, service, identity):
    if request.method == "POST":
        harness_agents.require_local_client(identity)
        data = await body(request)
        created = await asyncio.to_thread(harness_agents.create_agent, service.config, data)
        return JSONResponse(created, status_code=201)
    agents = await asyncio.to_thread(harness_agents.list_agents, service.config)
    service.identity(request, revalidate=True)
    if identity[0] != harness_agents.LOCAL_CLIENT:
        agents = [harness_agents.public_view(agent) for agent in agents]
    return JSONResponse({"agents": agents})


async def member(request, service, identity):
    harness_agents.require_local_client(identity)
    data = await body(request)
    operation = (
        harness_agents.delete_agent if request.method == "DELETE" else harness_agents.replace_agent
    )
    result = await asyncio.to_thread(operation, service.config, request.path_params["agent"], data)
    return JSONResponse(result)


ROUTES = [
    api_route("/v1/harness-agents", collection, methods=["GET", "POST"]),
    api_route("/v1/harness-agents/{agent}", member, methods=["PUT", "DELETE"]),
]
