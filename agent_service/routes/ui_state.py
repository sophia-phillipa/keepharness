"""Durable UI preferences of the local owner: read and merge."""

import asyncio

from starlette.responses import JSONResponse

from .. import ui_state
from ..errors import APIError
from . import api_route, body


async def preferences(request, service, identity):
    ui_state.require_owner(identity)
    if request.method == "PATCH":
        data = await body(request, limit=ui_state.BODY_BYTES)
        values = data.get("values")
        if not isinstance(values, dict):
            raise APIError("ui_state_invalid_value", field="values")
        result = await asyncio.to_thread(ui_state.update, service.config, identity[0], values)
    else:
        result = await asyncio.to_thread(ui_state.read, service.config, identity[0])
    return JSONResponse(result)  # api_route adds Cache-Control: no-store


ROUTES = [api_route("/v1/ui-state", preferences, methods=["GET", "PATCH"])]
