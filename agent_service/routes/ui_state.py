"""Durable UI preferences of the local owner: read and merge."""

import asyncio

from starlette.responses import JSONResponse

from .. import ui_state
from ..errors import APIError
from . import api_route, body


async def preferences(request, service, identity):
    if request.method == "PATCH":
        data = await body(request, limit=ui_state.BODY_BYTES)
        values = data.get("values")
        if not isinstance(values, dict):
            raise APIError("ui_state_invalid_value", field="values")
        # Temporary job ids remain recognizable after their in-memory session is gone.
        values = dict(values)
        activity = values.get("conversation_activity")
        if isinstance(activity, dict):
            values["conversation_activity"] = {
                key: value for key, value in activity.items() if not str(key).startswith("tmp-")
            }
        scroll = values.get("conversation_scroll")
        if isinstance(scroll, list):
            values["conversation_scroll"] = [
                item
                for item in scroll
                if not (isinstance(item, list) and item and str(item[0]).startswith("tmp-"))
            ]
        result = await asyncio.to_thread(ui_state.update, service.config, identity[0], values)
    else:
        result = await asyncio.to_thread(ui_state.read, service.config, identity[0])
    return JSONResponse(result)  # api_route adds Cache-Control: no-store


ROUTES = [api_route("/v1/ui-state", preferences, methods=["GET", "PATCH"])]
