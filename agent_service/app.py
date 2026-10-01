"""Authenticated API, durable single-worker queue and resumable events."""

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from starlette.applications import Starlette

from control import env
from tail_ui import StaticGZipMiddleware

from .conversation_context import context_overflow  # noqa: F401  (re-exported)
from .errors import APIError
from .routes import LimitedStream  # noqa: F401  (re-exported)
from .routes import activity as activity_routes
from .routes import conversations as conversation_routes
from .routes import files as file_routes
from .routes import effects as effect_routes
from .routes import models as model_routes
from .routes import projects as project_routes
from .routes import spans as span_routes
from .routes import system as system_routes
from .routes.projects import project_git  # noqa: F401  (re-exported)
from .services.conversation_service import ConversationService

logger = logging.getLogger(__name__)

# Historical name; tests and scripts still import ``Service``.
Service = ConversationService


def create_app(config, runtime_path=None):
    runtime_path = Path(runtime_path) if runtime_path else None
    service = Service(config)

    @asynccontextmanager
    async def lifespan(app):
        worker = asyncio.create_task(service.worker())

        async def watch_runtime():
            previous = None
            while True:
                try:
                    stat = runtime_path.stat()
                    marker = (stat.st_ino, stat.st_mtime_ns, stat.st_size)
                    if marker != previous:
                        candidate = json.loads(runtime_path.read_text())
                        await service.apply_runtime_config(candidate)
                        service.config_reload_error = None
                        previous = marker
                except (OSError, ValueError, TypeError, AttributeError, APIError) as exc:
                    code = exc.code if isinstance(exc, APIError) else "runtime_config_invalid"
                    if code != service.config_reload_error:
                        logger.warning("Runtime config reload failed: %s", code, exc_info=exc)
                    service.config_reload_error = code
                await asyncio.sleep(0.25)

        watcher = asyncio.create_task(watch_runtime()) if runtime_path else None
        try:
            yield
        finally:
            if watcher:
                watcher.cancel()
                try:
                    await watcher
                except asyncio.CancelledError:
                    pass
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass
            await service.effects.close()
            service.db.close()

    app = Starlette(
        routes=[
            *system_routes.ROUTES,
            *project_routes.ROUTES,
            *file_routes.ROUTES,
            *model_routes.ROUTES,
            *conversation_routes.ROUTES,
            *activity_routes.ROUTES,
            *span_routes.ROUTES,
            *effect_routes.ROUTES,
        ],
        lifespan=lifespan,
    )
    app.add_middleware(
        StaticGZipMiddleware,
        paths=("/", "/ui.js", "/run-console.js", "/tour.js", "/ui.css", "/tour.css", "/vendor/markdown-it.min.js"),
    )
    app.state.service = service
    return app


if __name__ == "__main__":
    import uvicorn

    from .log_config import configure_logging

    configure_logging()
    os.umask(0o077)
    agent_config = env.read("AGENT_CONFIG")
    if agent_config is None:
        raise KeyError(env.PRODUCT.env_prefix + "_AGENT_CONFIG")
    config = json.loads(Path(agent_config).read_text())
    uvicorn.run(
        create_app(config, Path(agent_config)),
        host=config.get("bind", "127.0.0.1"),
        port=config.get("port", 8095),
        access_log=False,
        limit_concurrency=64,
        timeout_keep_alive=5,
        proxy_headers=False,
    )
