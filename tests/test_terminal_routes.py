# P6b terminal routes and gates. Tests run against agent_service/routes/terminal.py, mounted in
# agent_service/app.py (not written yet: red). The route module is imported only where a test needs
# it, so the HTTP tests fail on the missing route (404), not on an import.
#
# HTTP shape pinned here (docs/workspace-toggles.md, Terminal Contracts):
#
#   POST   /v1/terminal/sessions                     body {"project_id"}
#          201 {"session_id", "cwd_label", "created": true}
#          200 {"session_id", "cwd_label", "created": false}   reattach to the project's live session
#          403 terminal_local_only | approval_session_required | approval_session_expired
#          404 project_not_found | 409 terminal_limit | 503 terminal_unavailable
#   POST   /v1/terminal/sessions/{id}/input          body {"data"}, at most 4096 UTF-8 bytes
#          204; 413 payload_limit above the cap; 409 terminal_exited after exit
#   POST   /v1/terminal/sessions/{id}/resize         body {"cols", "rows"} (1-500 each)
#          204
#   DELETE /v1/terminal/sessions/{id}                204, idempotent
#   GET    /v1/terminal/sessions/{id}/stream         text/event-stream
#          SSE frames: "id: <seq>", "event: output | exit | revoked", "data: <JSON>"
#          data: output {"seq", "text", "echo"}; exit {"code"}; revoked {"code"}
#          Last-Event-ID: <seq> replays only the events whose seq is greater than that
#          404 terminal_not_found (unknown id)
#
# Gate on every call, before any work: identity "local" first (403 terminal_local_only), then an
# approval session checked with revalidate=True. The session id alone authorizes nothing.
#
# Seams the tests rely on (the implementer keeps these names):
#   - agent_service.routes.terminal.RECHECK_SECONDS: module constant, read on every recheck, default <= 30.
#   - The stream re-runs the gate every RECHECK_SECONDS. On a failed check it sends "event: revoked"
#     with the error code and closes. The shell keeps running.
#   - The stream closes after the "exit" event. A stream opened after exit replays the ring buffer.
#   - A stream without Last-Event-ID replays the whole ring buffer first.
#   - App lifespan shutdown calls TerminalManager.close_all().
#   - The shell is $SHELL (the fixture sets /bin/sh) and its environment comes from child_environment().
#
# Tests drive the ASGI app directly (Stream below) so a test can wait for the first SSE chunk, which
# proves the stream is attached, before it revokes the session. Every wait has a real-time guard.

import asyncio
import hashlib
import json
import time

import httpx
import pytest

from agent_service import app as app_module
from agent_service import approval_sessions
from agent_service.app import create_app
from agent_service.terminal import TerminalManager

ORIGIN = "http://127.0.0.1:18095"
GUARD_SECONDS = 10.0
PROJECT = {"project_id": "sem-projeto"}


@pytest.fixture
def terminal_app(make_harness_config, monkeypatch):
    monkeypatch.setenv("SHELL", "/bin/sh")
    # Present in the harness process, so the env test can only pass if the shell does not inherit it.
    monkeypatch.setenv("KEEPHARNESS_P6B_CANARY", "1")
    app = create_app(
        make_harness_config(
            clients={
                owner: {
                    "sha256": hashlib.sha256(owner.encode()).hexdigest(),
                    "projects": ["sem-projeto"],
                }
                for owner in ("local", "other")
            }
        )
    )
    yield app
    app.state.service.db.close()


def enroll(app, owner="local"):
    config = app.state.service.config
    return approval_sessions.consume_enrollment(
        config, approval_sessions.issue_enrollment(config, owner)
    )


def browser(app, token=None):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 12345)),
        base_url=ORIGIN,
        cookies={"harness_session": token} if token else {},
    )


def run_app(app, scenario):
    """Run the scenario inside the app lifespan, so shutdown closes every shell the test left open."""

    async def main():
        async with app.router.lifespan_context(app):
            return await scenario()

    return asyncio.run(main())


async def start_session(client):
    response = await client.post("/v1/terminal/sessions", json=PROJECT)
    assert response.status_code == 201, response.text
    return response.json()["session_id"]


class Stream:
    """GET .../stream through the ASGI app; chunks are readable while the response is still open."""

    def __init__(self, app, token, session_id, last_event_id=None):
        self.status = None
        self.chunks = asyncio.Queue()
        self._requested = False
        self._release = asyncio.Event()
        headers = [
            (b"host", b"127.0.0.1:18095"),
            (b"cookie", f"harness_session={token}".encode()),
            (b"accept", b"text/event-stream"),
        ]
        if last_event_id is not None:
            headers.append((b"last-event-id", str(last_event_id).encode()))
        path = f"/v1/terminal/sessions/{session_id}/stream"
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "root_path": "",
            "query_string": b"",
            "headers": headers,
            "client": ("127.0.0.1", 12345),
            "server": ("127.0.0.1", 18095),
        }
        self.task = asyncio.create_task(app(scope, self._receive, self._send))

    async def _receive(self):
        if not self._requested:
            self._requested = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await self._release.wait()
        return {"type": "http.disconnect"}

    async def _send(self, message):
        if message["type"] == "http.response.start":
            self.status = message["status"]
        elif message["type"] == "http.response.body" and message.get("body"):
            self.chunks.put_nowait(message["body"])

    async def next_chunk(self):
        return await asyncio.wait_for(self.chunks.get(), GUARD_SECONDS)

    async def finish(self):
        await asyncio.wait_for(self.task, GUARD_SECONDS)
        parts = []
        while not self.chunks.empty():
            parts.append(self.chunks.get_nowait())
        return b"".join(parts).decode()


def parse_sse(text):
    """(id or None, event name, decoded data) for each frame."""
    events = []
    for block in text.split("\n\n"):
        fields = {}
        for line in block.splitlines():
            key, separator, value = line.partition(": ")
            if separator:
                fields[key] = value
        if "event" in fields:
            event_id = int(fields["id"]) if "id" in fields else None
            events.append((event_id, fields["event"], json.loads(fields["data"])))
    return events


@pytest.fixture
def no_spawn(monkeypatch):
    starts = []

    def refuse(self, project_id, cwd):
        starts.append((project_id, cwd))
        raise AssertionError("TerminalManager.start was called")

    monkeypatch.setattr(TerminalManager, "start", refuse)
    return starts


@pytest.mark.parametrize(
    "owner, with_session, code",
    [
        ("other", True, "terminal_local_only"),
        ("local", False, "approval_session_required"),
    ],
    ids=["non-local-with-session", "owner-without-session"],
)
def test_start_is_refused_before_any_process_starts(
    terminal_app, no_spawn, owner, with_session, code
):
    token = enroll(terminal_app, owner) if with_session else None

    async def scenario():
        async with browser(terminal_app, token) as client:
            response = await client.post("/v1/terminal/sessions", json=PROJECT)
        assert response.status_code == 403, response.text
        assert response.json()["code"] == code

    run_app(terminal_app, scenario)
    assert no_spawn == []


def test_every_call_regates_after_the_session_is_revoked(terminal_app):
    config = terminal_app.state.service.config
    token = enroll(terminal_app)

    async def scenario():
        async with browser(terminal_app, token) as client:
            session_id = await start_session(client)
            approval_sessions.revoke_session(config, token)
            calls = [
                ("POST", "/v1/terminal/sessions", {"json": PROJECT}),
                ("POST", f"/v1/terminal/sessions/{session_id}/input", {"json": {"data": "x"}}),
                (
                    "POST",
                    f"/v1/terminal/sessions/{session_id}/resize",
                    {"json": {"cols": 80, "rows": 24}},
                ),
                ("GET", f"/v1/terminal/sessions/{session_id}/stream", {}),
                ("DELETE", f"/v1/terminal/sessions/{session_id}", {}),
            ]
            for method, path, kwargs in calls:
                response = await client.request(method, path, **kwargs)
                assert response.status_code == 403, f"{method} {path}: {response.text}"
                assert response.json()["code"] == "approval_session_required", f"{method} {path}"

        # The refused DELETE must not have ended the shell: a fresh enrollment reattaches to it.
        async with browser(terminal_app, enroll(terminal_app)) as client:
            again = await client.post("/v1/terminal/sessions", json=PROJECT)
        assert again.status_code == 200, again.text
        assert again.json()["created"] is False
        assert again.json()["session_id"] == session_id

    run_app(terminal_app, scenario)


def test_stream_recheck_default_is_within_30_seconds():
    from agent_service.routes import terminal as terminal_routes

    assert terminal_routes.RECHECK_SECONDS <= 30


def test_revoked_session_closes_stream_with_revoked_and_shell_keeps_running(
    terminal_app, monkeypatch
):
    monkeypatch.setattr("agent_service.routes.terminal.RECHECK_SECONDS", 0.1)
    config = terminal_app.state.service.config
    token = enroll(terminal_app)

    async def scenario():
        async with browser(terminal_app, token) as client:
            session_id = await start_session(client)
            await client.post(
                f"/v1/terminal/sessions/{session_id}/input", json={"data": "echo ready\n"}
            )
        stream = Stream(terminal_app, token, session_id)
        # First chunk = the stream passed the gate and is attached; only then revoke.
        await stream.next_chunk()
        assert stream.status == 200
        approval_sessions.revoke_session(config, token)
        events = parse_sse(await stream.finish())

        assert events[-1][1] == "revoked"
        assert events[-1][2]["code"] in {"approval_session_required", "approval_session_expired"}
        assert all(name != "exit" for _, name, _ in events), "the shell must still be running"

        async with browser(terminal_app, enroll(terminal_app)) as client:
            again = await client.post("/v1/terminal/sessions", json=PROJECT)
        assert again.status_code == 200, again.text
        assert again.json()["session_id"] == session_id
        assert again.json()["created"] is False

    run_app(terminal_app, scenario)


def test_last_event_id_replays_only_later_events(terminal_app):
    token = enroll(terminal_app)

    async def scenario():
        async with browser(terminal_app, token) as client:
            session_id = await start_session(client)
            await client.post(
                f"/v1/terminal/sessions/{session_id}/input",
                json={"data": "printf 'one\\n'; printf 'two\\n'; printf 'three\\n'; exit\n"},
            )
        full = parse_sse(await Stream(terminal_app, token, session_id).finish())
        ids = [event_id for event_id, _, _ in full if event_id is not None]
        assert len(ids) >= 2, full
        assert full[-1][1] == "exit"

        resume_after = ids[0]
        resumed = parse_sse(
            await Stream(terminal_app, token, session_id, last_event_id=resume_after).finish()
        )
        assert [event_id for event_id, _, _ in resumed] == ids[1:]
        assert resumed[-1][1] == "exit"

    run_app(terminal_app, scenario)


def test_gap_event_is_streamed_with_its_id_and_not_repeated_on_resume(terminal_app, monkeypatch):
    monkeypatch.setattr("agent_service.terminal.EVENT_LIMIT", 2)
    token = enroll(terminal_app)

    async def scenario():
        async with browser(terminal_app, token) as client:
            session_id = await start_session(client)
            await client.post(
                f"/v1/terminal/sessions/{session_id}/input",
                json={"data": "printf 'one\\n'; printf 'two\\n'; printf 'three\\n'; exit\n"},
            )
        # Read to the exit event first: the ring has dropped events only once the shell is done.
        await Stream(terminal_app, token, session_id, last_event_id=0).finish()
        full = parse_sse(await Stream(terminal_app, token, session_id, last_event_id=0).finish())
        gaps = [event_id for event_id, name, _ in full if name == "gap"]
        assert len(gaps) == 1, full
        first_kept = next(int(event_id) for event_id, name, _ in full if name != "gap")
        assert int(gaps[0]) + 1 == first_kept, full
        resumed = parse_sse(
            await Stream(terminal_app, token, session_id, last_event_id=gaps[0]).finish()
        )
        assert all(name != "gap" for _, name, _ in resumed), resumed

    run_app(terminal_app, scenario)


@pytest.mark.parametrize(
    "data, status",
    [
        ("a" * 4096, 204),
        ("a" * 4097, 413),
        ("é" * 2049, 413),  # 2049 characters but 4098 UTF-8 bytes
    ],
    ids=["4096-ascii-ok", "4097-ascii", "2049-utf8-chars"],
)
def test_input_cap_is_measured_in_utf8_bytes(terminal_app, data, status):
    token = enroll(terminal_app)

    async def scenario():
        async with browser(terminal_app, token) as client:
            session_id = await start_session(client)
            response = await client.post(
                f"/v1/terminal/sessions/{session_id}/input", json={"data": data}
            )
        assert response.status_code == status, response.text
        if status == 413:
            assert response.json()["code"] == "payload_limit"

    run_app(terminal_app, scenario)


def test_shell_environment_has_no_keepharness_names(terminal_app):
    token = enroll(terminal_app)

    async def scenario():
        async with browser(terminal_app, token) as client:
            session_id = await start_session(client)
            await client.post(
                f"/v1/terminal/sessions/{session_id}/input", json={"data": "env; exit\n"}
            )
        return await Stream(terminal_app, token, session_id).finish()

    output = parse_sse(run_app(terminal_app, scenario))
    printed = "".join(data.get("text", "") for _, name, data in output if name == "output")
    # Non-vacuous: the env listing really arrived, with the terminal's own TERM.
    assert "PATH=" in printed
    assert "TERM=dumb" in printed
    assert "KEEPHARNESS_" not in printed


def test_app_shutdown_closes_every_terminal_session(make_harness_config, monkeypatch):
    original_start = TerminalManager.start
    original_close_all = TerminalManager.close_all
    started = []
    closed = []

    def record_start(self, project_id, cwd):
        session, created = original_start(self, project_id, cwd)
        started.append(session)
        return session, created

    def record_close_all(self):
        closed.append(self)
        original_close_all(self)

    monkeypatch.setenv("SHELL", "/bin/sh")
    monkeypatch.setattr(TerminalManager, "start", record_start)
    monkeypatch.setattr(TerminalManager, "close_all", record_close_all)
    app = create_app(
        make_harness_config(
            clients={
                "local": {
                    "sha256": hashlib.sha256(b"local").hexdigest(),
                    "projects": ["sem-projeto"],
                }
            }
        )
    )
    token = enroll(app)

    async def scenario():
        async with browser(app, token) as client:
            await start_session(client)

    try:
        run_app(app, scenario)
        assert len(started) == 1
        assert len(closed) == 1
        assert not started[0].is_alive()
    finally:
        app.state.service.db.close()


def test_lifespan_reaper_ends_idle_sessions_even_with_a_foreground_child(
    make_harness_config, monkeypatch, tmp_path
):
    now = [0.0]
    monkeypatch.setenv("SHELL", "/bin/sh")
    monkeypatch.setattr(app_module, "REAP_SECONDS", 0.01)
    monkeypatch.setattr(
        app_module,
        "TerminalManager",
        lambda *, clock: TerminalManager(clock=lambda: now[0], idle_timeout=1800.0),
    )
    app = create_app(make_harness_config())

    async def scenario():
        session, _ = await asyncio.to_thread(app.state.terminal.start, "sem-projeto", str(tmp_path))
        session.write("sleep 30\n")
        now[0] += 1800.0
        deadline = time.monotonic() + GUARD_SECONDS
        while session.is_alive():
            assert time.monotonic() < deadline, "idle session was not reaped"
            await asyncio.sleep(0.01)

    try:
        run_app(app, scenario)
    finally:
        app.state.service.db.close()
