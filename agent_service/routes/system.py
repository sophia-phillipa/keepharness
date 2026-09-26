"""Login, version, capabilities and the static browser UI."""

import hashlib
import hmac
from pathlib import Path

from starlette.responses import FileResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

import adapters
from tail_ui import asset_response

from ..config import PACKAGE_DIR, REPOSITORY_ROOT, VERSION_FILE
from ..errors import APIError
from ..persistence.db import encoded
from . import api_route, body


async def login(request, service, identity):
    config = service.config
    service.limit(("public", "login"), 20, "login_rate_limit")
    data = await body(request)
    token = data.get("token", "")
    if not isinstance(token, str):
        raise APIError("invalid_token")
    digest = hashlib.sha256(token.encode()).hexdigest()
    if not token or not any(
        hmac.compare_digest(digest, c["sha256"]) for c in config["clients"].values()
    ):
        raise APIError("authentication_required", 401)
    if request.headers.get("origin") not in config.get("origins", []):
        raise APIError("origin_denied", 403)
    response = JSONResponse({"authenticated": True})
    response.set_cookie(
        "harness_token",
        token,
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
    )
    return response


async def capabilities(request, service, identity):
    value = service.capabilities()
    etag = '"' + hashlib.sha256(encoded(value).encode()).hexdigest() + '"'
    return (
        Response(status_code=304, headers={"ETag": etag})
        if request.headers.get("if-none-match") == etag
        else JSONResponse(value, headers={"ETag": etag})
    )


async def version(request, service, identity):
    config = service.config
    source_files = [
        PACKAGE_DIR / name
        for name in (
            "ui.js",
            "ui.css",
            "vendor/markdown-it.min.js",
            "index.html",
            "app.py",
            "config.py",
            "maestro.py",
            "workspaces.py",
            "mcp_bridge.py",
            "VERSION",
        )
    ]
    for package in ("routes", "persistence", "services"):
        source_files += sorted((PACKAGE_DIR / package).rglob("*.py"))
    source_files += sorted(Path(adapters.__file__).parent.rglob("*.py"))
    digest = hashlib.sha256(b"".join(path.read_bytes() for path in source_files)).hexdigest()[:12]
    return JSONResponse(
        {
            "version": VERSION_FILE.read_text().strip(),
            "build": digest,
            "config_revision": config.get("config_revision"),
            "config_reload_error": service.config_reload_error,
        }
    )


async def ui(request):
    config = request.app.state.service.config
    # Keep browser storage and authenticated history on the shared origin.
    # API/MCP callers retain their own identities and never follow this route.
    destination = config.get("browser_url")
    if (
        request.url.path == "/"
        and request.url.hostname in ("127.0.0.1", "localhost", "::1")
        and destination
        and destination.rstrip("/") in config.get("origins", [])
        and destination.rstrip("/") != str(request.base_url).rstrip("/")
    ):
        return RedirectResponse(
            destination,
            status_code=307,
            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
        )
    if request.url.path.startswith("/assets/"):
        return asset_response(request.url.path)
    if request.url.path == "/setup-mcp.sh":
        return FileResponse(
            PACKAGE_DIR / "setup-mcp.sh",
            media_type="text/x-shellscript",
            filename="setup-mcp.sh",
            headers={"Cache-Control": "no-store"},
        )
    if request.url.path == "/guide":
        return FileResponse(REPOSITORY_ROOT / "README.md", media_type="text/plain")
    name = {
        "/vendor/markdown-it.min.js": "vendor/markdown-it.min.js",
        "/ui.js": "ui.js",
        "/ui.css": "ui.css",
        "/mcp_bridge.py": "mcp_bridge.py",
    }.get(request.url.path, "index.html")
    return FileResponse(
        PACKAGE_DIR / name,
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
        },
    )


ROUTES = [
    api_route("/v1/login", login, methods=["POST"], authenticated=False),
    api_route("/.well-known/agent-capabilities.json", capabilities),
    api_route("/v1/version", version),
    Route("/guide", ui),
    Route("/", ui),
    Route("/vendor/markdown-it.min.js", ui),
    Route("/ui.js", ui),
    Route("/ui.css", ui),
    Route("/assets/{path:path}", ui),
    Route("/mcp_bridge.py", ui),
    Route("/setup-mcp.sh", ui),
]
