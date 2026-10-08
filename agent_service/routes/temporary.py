"""Lifecycle of temporary chats; all content uses the existing authenticated APIs."""

from starlette.responses import JSONResponse

from . import api_route


async def sessions(request, service, identity):
    if request.method == "POST":
        return JSONResponse({"id": service.temporary.open(identity)}, status_code=201)
    sid = request.path_params["session"]
    if request.method == "GET":
        service.temporary.get(identity, sid)
        return JSONResponse({"id": sid})
    await service.temporary.close(identity, sid)
    return JSONResponse({"id": sid, "deleted": True})


ROUTES = [
    api_route("/v1/temporary", sessions, methods=["POST"]),
    api_route("/v1/temporary/{session}", sessions, methods=["GET", "DELETE"]),
]
