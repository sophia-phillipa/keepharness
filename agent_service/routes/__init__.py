"""HTTP layer of the agent service: request parsing, the per-route auth wrapper and error shapes.

Authentication stays per route (inside ``api_route``), never app middleware, so unknown
paths (404) and wrong methods (405) are answered by the router before any auth check.
"""

import asyncio
import json
import logging
import uuid

from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from .. import tools
from ..errors import APIError

logger = logging.getLogger(__name__)


class LimitedStream(StreamingResponse):
    """Release the reserved connection even when disconnected before iteration."""

    def __init__(self, *args, release, **kwargs):
        super().__init__(*args, **kwargs)
        self.release = release

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            self.release()


async def body(request, limit=200000):
    chunks = bytearray()
    try:
        async with asyncio.timeout(10):
            async for chunk in request.stream():
                if len(chunks) + len(chunk) > limit:
                    raise APIError("payload_limit", 413)
                chunks.extend(chunk)
    except TimeoutError:
        raise APIError("request_timeout", 408)
    try:
        data = json.loads(chunks)
        json.dumps(data, allow_nan=False, ensure_ascii=False).encode("utf-8")
    except (ValueError, UnicodeError, RecursionError):
        raise APIError("invalid_json")
    if not isinstance(data, dict):
        raise APIError("object_required")
    owner = getattr(request.state, "authenticated_owner", None)
    if owner is not None:
        current = request.app.state.service.identity(request, revalidate=True)
        if current[0] != owner:
            raise APIError("authentication_required", 401)
    return data


def error_response(exc):
    code = exc.code if isinstance(exc, APIError) else str(exc)
    status = exc.status if isinstance(exc, APIError) else 422
    return JSONResponse(
        {
            "code": code,
            "message": code,
            "retryable": status in (429, 503),
            "request_id": uuid.uuid4().hex,
            **({"field": exc.field} if getattr(exc, "field", None) else {}),
            **({"owner": exc.owner} if getattr(exc, "owner", None) else {}),
            **({"login": exc.login} if getattr(exc, "login", None) else {}),
        },
        status_code=status,
        headers={"Retry-After": str(exc.retry_after)}
        if isinstance(exc, APIError) and exc.retry_after
        else {},
    )


def no_store(response):
    """Private data is never cached: one rule for every /v1 JSON answer."""
    if isinstance(response, JSONResponse):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


def api_route(path, handler, methods=None, *, authenticated=True):
    """A route whose handler receives ``(request, service, identity)``.

    ``service`` is ``request.app.state.service``; its ``config`` is the live dict that
    runtime reloads mutate in place. ``identity`` is resolved first unless the route is
    public; API and tool errors become JSON, anything else a logged 500.
    """

    async def endpoint(request):
        service = request.app.state.service
        try:
            identity = service.identity(request) if authenticated else None
            request.state.authenticated_owner = identity[0] if identity else None
            return no_store(await handler(request, service, identity))
        except (APIError, tools.ToolError) as exc:
            return no_store(error_response(exc))
        except Exception:
            request_id = uuid.uuid4().hex
            logger.exception(
                "Internal error on %s %s (request %s)", request.method, request.url.path, request_id
            )
            return no_store(
                JSONResponse(
                    {"code": "internal_error", "retryable": False, "request_id": request_id},
                    status_code=500,
                )
            )

    return Route(path, endpoint, methods=methods)
