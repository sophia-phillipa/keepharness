"""Local administration app. Provider access and tailnet exposure require explicit choices."""

import shutil  # noqa: F401  (tests patch control.server.shutil.which)
from contextlib import asynccontextmanager

from starlette.applications import Starlette

from .manager import Manager, migrate_local_ai_directory  # noqa: F401  (re-exported)
from .routes import ADMIN_BODY_LIMIT, ADMIN_OPERATION_LIMIT, ROUTES  # noqa: F401  (re-exported)


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
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'",
            }
        )
        return response

    app.state.manager = manager
    app.state.admin_port = port
    return app
