"""Login, version, capabilities and the static browser UI."""

import hashlib
import hmac
from html import escape
from pathlib import Path
from urllib.parse import urlsplit

from starlette.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

import adapters
import harness_ui
from harness_ui import ASSETS, PUBLIC, asset_response, static_response

from ..approval_sessions import SESSION_COOKIE, SESSION_SECONDS, consume_enrollment, revoke_session
from ..config import PACKAGE_DIR, REPOSITORY_ROOT, VERSION_FILE
from ..errors import APIError
from ..persistence.db import encoded
from . import api_route, body


def secure_cookie(request, config):
    """The browser may use TLS even when the trusted proxy forwards HTTP locally."""
    browser_scheme = urlsplit(config.get("browser_url") or "").scheme
    return request.url.scheme == "https" or browser_scheme == "https"


async def approve_device(request, service, identity):
    """A CLI-issued link requires a same-origin confirmation before redemption."""
    headers = {
        "Cache-Control": "no-store",
        "Referrer-Policy": "same-origin",
        "Content-Security-Policy": "default-src 'none'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
        "X-Content-Type-Options": "nosniff",
    }
    nonce = request.query_params.get("nonce", "")
    if not nonce or len(nonce) > 128:
        raise APIError("approval_enrollment_invalid", 403)
    if request.method == "GET":
        return HTMLResponse(
            '<!doctype html><html lang="en"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            "<title>Enable human approvals</title><h1>Enable human approvals</h1>"
            "<p>Continue only if you generated this link with the owner CLI. "
            "This browser will be able to approve actions for that owner.</p>"
            '<form method="post" action="/approve-device?nonce=' + escape(nonce, quote=True) + '">'
            '<button type="submit">Enable approvals on this browser</button></form></html>',
            headers=headers,
        )
    if (
        request.headers.get("origin") not in service.config.get("origins", [])
        or request.headers.get("sec-fetch-site") == "cross-site"
    ):
        raise APIError("origin_denied", 403)
    service.limit(("public", "enrollment"), 20, "enrollment_rate_limit")
    token = consume_enrollment(service.config, nonce)
    response = RedirectResponse("/", status_code=303, headers=headers)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_SECONDS,
        httponly=True,
        samesite="strict",
        secure=secure_cookie(request, service.config),
    )
    return response


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
        secure=secure_cookie(request, config),
    )
    return response


async def logout(request, service, identity):
    if getattr(request.state, "approval_session_owner", None) == identity[0]:
        revoke_session(service.config, request.cookies[SESSION_COOKIE])
    response = JSONResponse({"authenticated": False})
    for name in (SESSION_COOKIE, "harness_token"):
        response.delete_cookie(
            name, httponly=True, samesite="strict", secure=secure_cookie(request, service.config)
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


class BuildVersions:
    """Metadata fingerprints, not content checksums; polling never opens source files."""

    def __init__(self):
        self.version = VERSION_FILE.read_text().strip()
        self._marker = None
        disk = self.disk_versions()
        self.build = disk["disk_build"]
        self.source_build = disk["disk_source_build"]

    def disk_versions(self):
        ui_files = [
            PACKAGE_DIR / name
            for name in (
                "ui.js", "run-console.js", "tour.js", "ui.css", "tour.css",
                "vendor/markdown-it.min.js", "index.html",
            )
        ]
        ui_files += [ASSETS / name for name in sorted(PUBLIC)]
        source_files = sorted(PACKAGE_DIR.rglob("*.py"))
        source_files += sorted(Path(adapters.__file__).parent.rglob("*.py"))
        source_files += [Path(harness_ui.__file__), VERSION_FILE]

        def metadata(paths):
            values = []
            for path in paths:
                try:
                    stat = path.stat()
                    values.append((str(path), stat.st_mtime_ns, stat.st_size))
                except FileNotFoundError:
                    values.append((str(path), None, None))
            return tuple(values)

        ui_marker = metadata(ui_files)
        source_marker = metadata(source_files)
        marker = (ui_marker, source_marker)
        if marker != self._marker:
            self._disk = {
                "disk_build": hashlib.sha256(repr(marker).encode()).hexdigest()[:12],
                "disk_source_build": hashlib.sha256(repr(source_marker).encode()).hexdigest()[:12],
                "ui_build": hashlib.sha256(repr(ui_marker).encode()).hexdigest()[:12],
            }
            self._marker = marker
        return self._disk


async def version(request, service, identity):
    builds = request.app.state.build_versions
    return JSONResponse(
        {
            "product": "keepharness",
            "version": builds.version,
            "build": builds.build,
            "source_build": builds.source_build,
            **builds.disk_versions(),
            "config_revision": service.config.get("config_revision"),
            "config_reload_error": service.config_reload_error,
        }
    )


# Served as downloads to the MCP bridge installer: the script and the hashed lock it installs from.
DOWNLOADS = {
    "/setup-mcp.sh": ("setup-mcp.sh", "text/x-shellscript"),
    "/bridge-requirements.txt": ("bridge-requirements.txt", "text/plain"),
}


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
        return asset_response(request.url.path, request.headers)
    if request.url.path in DOWNLOADS:
        name, media_type = DOWNLOADS[request.url.path]
        return FileResponse(
            PACKAGE_DIR / name,
            media_type=media_type,
            filename=name,
            headers={"Cache-Control": "no-store"},
        )
    if request.url.path == "/guide":
        return FileResponse(REPOSITORY_ROOT / "README.md", media_type="text/plain")
    name = {
        "/vendor/markdown-it.min.js": "vendor/markdown-it.min.js",
        "/ui.js": "ui.js",
        "/run-console.js": "run-console.js",
        "/tour.js": "tour.js",
        "/ui.css": "ui.css",
        "/tour.css": "tour.css",
        "/mcp_bridge.py": "mcp_bridge.py",
    }.get(request.url.path, "index.html")
    return static_response(
        PACKAGE_DIR / name,
        request.headers,
        {
            "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; "
            + admin_frame_source(config.get("admin_url"))
            + "frame-ancestors 'none'; base-uri 'none'",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
        },
    )


def admin_frame_source(admin_url):
    """Settings › System frames the local admin; nothing else may be framed."""
    parts = urlsplit(admin_url or "")
    try:
        port = parts.port
    except ValueError:
        return ""
    if parts.scheme != "http" or parts.hostname not in ("127.0.0.1", "localhost") or not port:
        return ""
    return f"frame-src http://{parts.hostname}:{port}; "


ROUTES = [
    api_route("/approve-device", approve_device, methods=["GET", "POST"], authenticated=False),
    api_route("/v1/login", login, methods=["POST"], authenticated=False),
    api_route("/v1/logout", logout, methods=["POST"]),
    api_route("/.well-known/agent-capabilities.json", capabilities),
    api_route("/v1/version", version),
    Route("/guide", ui),
    Route("/", ui),
    Route("/vendor/markdown-it.min.js", ui),
    Route("/ui.js", ui),
    Route("/run-console.js", ui),
    Route("/tour.js", ui),
    Route("/ui.css", ui),
    Route("/tour.css", ui),
    Route("/assets/{path:path}", ui),
    Route("/mcp_bridge.py", ui),
    Route("/setup-mcp.sh", ui),
    Route("/bridge-requirements.txt", ui),
]
