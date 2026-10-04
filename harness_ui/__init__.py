"""Shared, offline UI assets for the admin and conversation interfaces."""

import os
from pathlib import Path

from starlette.middleware.gzip import GZipMiddleware
from starlette.responses import FileResponse, Response
from starlette.staticfiles import NotModifiedResponse

ASSETS = Path(__file__).with_name("assets")
PUBLIC = frozenset(
    (
        "tabler.min.css",
        "tabler.min.js",
        "themes.css",
        "theme.js",
        "components.js",
        "icons.svg",
        "file-icons.svg",
        "file-icons-data.js",
        "file-icons-LICENSE.txt",
        "inter-latin.woff2",
        "favicon.ico",
        "apple-touch-icon.png",
    )
)


def static_response(path, request_headers=None, headers=None):
    """Serve a UI file revalidated on every use: ``no-cache`` with an ETag, 304 when unchanged."""
    response = FileResponse(
        path, stat_result=os.stat(path), headers={"Cache-Control": "no-cache", **(headers or {})}
    )
    tags = (request_headers or {}).get("if-none-match", "")
    if response.headers["etag"] in [tag.strip().removeprefix("W/") for tag in tags.split(",")]:
        return NotModifiedResponse(response.headers)
    return response


def asset_response(path, request_headers=None):
    name = path.removeprefix("/assets/")
    if name not in PUBLIC:
        return Response(status_code=404)
    return static_response(ASSETS / name, request_headers, {"X-Content-Type-Options": "nosniff"})


class StaticGZipMiddleware(GZipMiddleware):
    """Compress only the static UI files: API responses may carry secrets (BREACH)."""

    def __init__(self, app, paths):
        super().__init__(app, minimum_size=1024)
        self.paths = frozenset(paths)

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if scope["type"] == "http" and (path in self.paths or path.startswith("/assets/")):
            await super().__call__(scope, receive, send)
        else:
            await self.app(scope, receive, send)
