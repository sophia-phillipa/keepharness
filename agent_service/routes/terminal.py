"""Project shells over HTTP: start, input, resize, stream and end, for the local owner only.

Every call re-checks the owner and the approval session before any spawn or session access, and an
open stream re-checks every ``RECHECK_SECONDS``. A failed check ends the stream with ``revoked``;
the shell keeps running and the owner can reattach with a fresh approval session.
"""

import asyncio
import contextlib
import json
import time
from collections.abc import Iterator
from pathlib import Path

from starlette.responses import JSONResponse, Response

from .. import approval_sessions, workspaces
from ..errors import APIError
from ..terminal import TerminalError, TerminalSession
from ..tools import ToolError
from . import LimitedStream, api_route, body

LOCAL_OWNER = "local"
NO_PROJECT = "sem-projeto"
MAX_INPUT_BYTES = 4096
MAX_SIZE = 500
RECHECK_SECONDS = 5.0
WAKE_FALLBACK_SECONDS = 1.0  # bounds the delay if a wake-up is ever missed; output wakes the stream
NO_STORE = {"Cache-Control": "no-store"}


def _gate_sync(request, config, identity):
    if identity[0] != LOCAL_OWNER:
        raise APIError("terminal_local_only", 403)
    approval_sessions.require_approval_session(request, config, identity, revalidate=True)


async def gate(request, service, identity):
    await asyncio.to_thread(_gate_sync, request, service.config, identity)


async def call(function, *args):
    try:
        return await asyncio.to_thread(function, *args)
    except TerminalError as exc:
        raise APIError(exc.code, exc.status) from exc


async def session_of(request):
    return await call(request.app.state.terminal.get, request.path_params["session"])


def project_cwd(project, spec):
    """The project root; sem-projeto and folder-less projects start in the owner's home folder."""
    if project == NO_PROJECT or not spec.get("root"):
        return Path.home(), "~"
    try:
        root = workspaces.project_root(spec, "root")
    except ToolError as exc:
        raise APIError("terminal_unavailable", 503) from exc
    return root, root.name


def no_content():
    return Response(status_code=204, headers=NO_STORE)


def frame(name, payload, seq=None):
    head = f"id: {seq}\n" if seq is not None else ""
    return f"{head}event: {name}\ndata: {json.dumps(payload)}\n\n"


async def start_session(request, service, identity):
    await gate(request, service, identity)
    data = await body(request)
    project = data.get("project_id")
    if not isinstance(project, str) or project not in service.config["projects"]:
        raise APIError("project_not_found", 404)
    spec = service.project(identity, project)
    cwd, label = project_cwd(project, spec)
    try:
        session, created = await asyncio.to_thread(
            request.app.state.terminal.start, project, str(cwd)
        )
    except TerminalError as exc:
        raise APIError(exc.code, exc.status) from exc
    except OSError as exc:
        raise APIError("terminal_unavailable", 503) from exc
    return JSONResponse(
        {"session_id": session.id, "cwd_label": label, "created": created},
        status_code=201 if created else 200,
    )


async def send_input(request, service, identity):
    await gate(request, service, identity)
    data = await body(request)
    text = data.get("data")
    if not isinstance(text, str):
        raise APIError("invalid_input", 422, field="data")
    if len(text.encode("utf-8")) > MAX_INPUT_BYTES:
        raise APIError("payload_limit", 413)
    session = await session_of(request)
    await call(session.write, text)
    return no_content()


def is_size(value):
    return type(value) is int and 1 <= value <= MAX_SIZE


async def resize_session(request, service, identity):
    await gate(request, service, identity)
    data = await body(request)
    cols, rows = data.get("cols"), data.get("rows")
    if not is_size(cols) or not is_size(rows):
        raise APIError("invalid_size", 422)
    session = await session_of(request)
    await call(session.resize, cols, rows)
    return no_content()


async def close_session(request, service, identity):
    await gate(request, service, identity)
    try:
        session = await session_of(request)
    except APIError as exc:
        if exc.code == "terminal_not_found":
            return no_content()
        raise
    await asyncio.to_thread(session.end, "deleted")
    return no_content()


def resume_after(request):
    try:
        return max(0, int(request.headers.get("last-event-id", "0")))
    except ValueError:
        return 0


@contextlib.contextmanager
def _output_wakeup(session: TerminalSession) -> Iterator[asyncio.Event]:
    """Yield an event set on every event append; the listener is removed on exit."""
    loop = asyncio.get_running_loop()
    wake = asyncio.Event()

    def notify() -> None:
        # Runs on the reader thread with the session lock held: only schedule the set.
        with contextlib.suppress(RuntimeError):  # the loop is closed, so nothing waits
            loop.call_soon_threadsafe(wake.set)

    session.add_listener(notify)
    try:
        yield wake
    finally:
        session.remove_listener(notify)


async def _wait_for_output(wake: asyncio.Event) -> None:
    with contextlib.suppress(TimeoutError):
        async with asyncio.timeout(WAKE_FALLBACK_SECONDS):
            await wake.wait()


async def frames(request, service, identity, session, cursor):
    checked = time.monotonic()
    with _output_wakeup(session) as wake:
        while True:
            if time.monotonic() - checked >= RECHECK_SECONDS:
                checked = time.monotonic()
                try:
                    await gate(request, service, identity)
                except APIError as exc:
                    yield frame("revoked", {"code": exc.code})
                    return
            wake.clear()  # cleared before the read: an append after this point sets it again
            for event in await asyncio.to_thread(session.events_after, cursor):
                cursor = event.seq
                yield frame(event.name, event.payload, event.seq)
                if event.name == "exit":
                    return
            if await request.is_disconnected():
                return
            await _wait_for_output(wake)


async def stream_session(request, service, identity):
    await gate(request, service, identity)
    session = await session_of(request)
    session.attach_stream()
    return LimitedStream(
        frames(request, service, identity, session, resume_after(request)),
        release=session.detach_stream,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


ROUTES = [
    api_route("/v1/terminal/sessions", start_session, methods=["POST"]),
    api_route("/v1/terminal/sessions/{session}/input", send_input, methods=["POST"]),
    api_route("/v1/terminal/sessions/{session}/resize", resize_session, methods=["POST"]),
    api_route("/v1/terminal/sessions/{session}/stream", stream_session, methods=["GET"]),
    api_route("/v1/terminal/sessions/{session}", close_session, methods=["DELETE"]),
]
