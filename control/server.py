"""Local administration app. Provider access and tailnet exposure require explicit choices."""

import shutil  # noqa: F401  (tests patch control.server.shutil.which)
from contextlib import asynccontextmanager

from starlette.applications import Starlette

from tail_ui import StaticGZipMiddleware

from .manager import Manager, migrate_local_ai_directory  # noqa: F401  (re-exported)
from .routes import ADMIN_BODY_LIMIT, ADMIN_OPERATION_LIMIT, ROUTES  # noqa: F401  (re-exported)


def frame_ancestors(harness_port):
    """Only the harness's own local page may show the admin (Settings › System)."""
    if not isinstance(harness_port, int) or isinstance(harness_port, bool):
        return "frame-ancestors 'none'"
    return f"frame-ancestors http://127.0.0.1:{harness_port} http://localhost:{harness_port}"


def create_app(state, port=8094):
    manager = Manager(state)
    manager.admin_port = port

    @asynccontextmanager
    async def lifespan(app):
        await manager.refresh()
        if any(spec.get("enabled") for spec in manager.settings["services"].values()):
            try:
                await manager.start()
            except (ValueError, RuntimeError, OSError) as exc:
                manager.startup_error = str(exc)
        yield
        await manager.stop(force=True)
        await manager.operations.close()

    app = Starlette(routes=ROUTES, lifespan=lifespan)

    @app.middleware("http")
    async def security(request, call_next):
        response = await call_next(request)
        # Static panel files set their own revalidation policy; everything else is no-store.
        response.headers.setdefault("Cache-Control", "no-store")
        response.headers.update(
            {
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; script-src 'self'; style-src 'self'; connect-src 'self'; "
                + frame_ancestors(manager.settings.get("port"))
                + "; base-uri 'none'",
            }
        )
        return response

    app.add_middleware(StaticGZipMiddleware, paths=("/", "/admin.js", "/catalogs.js", "/admin.css"))

    app.state.manager = manager
    app.state.admin_port = port
    return app
