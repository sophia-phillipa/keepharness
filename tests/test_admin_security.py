"""Administrative boundary checks; isolated state, no CLI or inference calls."""

import asyncio
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from agent_service.errors import UserMessageError
from control import local_access
from control.server import ADMIN_BODY_LIMIT, ADMIN_OPERATION_LIMIT, create_app


class AdminSecurityTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.app = create_app(self.folder.name)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://127.0.0.1:8094"
        )
        self.headers = {"X-Harness-Admin": "1"}
        # The owner's browser holds a session issued for the install; the admin cookie needs it.
        self.client.cookies.set(
            local_access.COOKIE, local_access.issue_session(self.app.state.manager.state)
        )
        await self.client.get("/")

    async def asyncTearDown(self):
        await self.client.aclose()
        self.folder.cleanup()

    async def test_origin_host_cookie_and_header_are_enforced(self):
        for headers in (
            {"Host": "evil.test:8094"},
            {"Origin": "https://evil.test"},
            {"Sec-Fetch-Site": "cross-site"},
            {"Tailscale-User-Login": "person@example.test"},
        ):
            response = await self.client.post(
                "/api/settings-export", json={}, headers={**self.headers, **headers}
            )
            self.assertEqual(response.status_code, 403)
        self.assertEqual((await self.client.post("/api/settings-export", json={})).status_code, 400)
        self.assertEqual(
            (
                await self.client.post(
                    "/api/settings-export",
                    json={},
                    headers={**self.headers, "Cookie": "admin=\\351"},
                )
            ).status_code,
            401,
        )
        self.client.cookies.clear()
        self.assertEqual(
            (
                await self.client.post("/api/settings-export", json={}, headers=self.headers)
            ).status_code,
            401,
        )
        remote = httpx.ASGITransport(app=self.app, client=("10.0.0.2", 1234))
        async with httpx.AsyncClient(transport=remote, base_url="http://127.0.0.1:8094") as client:
            self.assertEqual((await client.get("/")).status_code, 403)

    async def test_admin_errors_are_codes(self):
        leak = "/home/leaky/.secret/token.json"

        async def denied(request, manager, data):
            raise PermissionError(13, "Permission denied", leak)

        async def missing_key(request, manager, data):
            return data["internal_field_name"]

        with patch.dict(
            "control.routes.POST_ROUTES",
            {"/api/settings-export": denied, "/api/vpn-key": missing_key},
        ):
            os_error = await self.client.post(
                "/api/settings-export", json={}, headers=self.headers
            )
            key_error = await self.client.post("/api/vpn-key", json={}, headers=self.headers)
        bad_json = await self.client.post(
            "/api/settings-export", content=b"{not json", headers=self.headers
        )
        for response, code in (
            (os_error, "operation_failed"),
            (key_error, "invalid_request"),
            (bad_json, "invalid_json"),
        ):
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json(), {"error": code})
        self.assertNotIn("leaky", os_error.text + key_error.text + bad_json.text)

    async def test_unexpected_value_errors_are_codes_and_deliberate_ones_keep_their_sentence(self):
        async def library(request, manager, data):
            int("/home/leaky/not-a-number")

        async def runtime(request, manager, data):
            raise RuntimeError("/home/leaky/runtime detail")

        async def deliberate(request, manager, data):
            raise UserMessageError("Pick an existing project.")

        with patch.dict(
            "control.routes.POST_ROUTES",
            {"/api/a": library, "/api/b": runtime, "/api/c": deliberate},
        ):
            replies = [
                await self.client.post(path, json={}, headers=self.headers)
                for path in ("/api/a", "/api/b", "/api/c")
            ]
        for response in replies:
            self.assertEqual(response.status_code, 400)
        self.assertEqual(replies[0].json(), {"error": "operation_failed"})
        self.assertEqual(replies[1].json(), {"error": "operation_failed"})
        self.assertEqual(replies[2].json(), {"error": "Pick an existing project."})

    async def test_a_slow_supervised_restart_does_not_turn_admin_mutations_away(self):
        manager = self.app.state.manager

        class Proc:
            def __init__(self, returncode):
                self.returncode, self.never = returncode, asyncio.Event()

            async def wait(self):
                if self.returncode is None:
                    await self.never.wait()
                return self.returncode

        ready, waiting, spawned = asyncio.Event(), asyncio.Event(), Proc(None)

        async def start(supervised=False):
            manager.proc = spawned
            return spawned

        async def await_ready(proc):
            waiting.set()
            await ready.wait()

        manager.proc = Proc(1)
        manager.start, manager.await_ready = start, await_ready
        with patch("control.manager.RESTART_DELAY", 0):
            manager.watch()
            async with asyncio.timeout(5):
                await waiting.wait()
            response = await self.client.post("/api/settings-export", json={}, headers=self.headers)
            self.assertEqual(response.status_code, 200)
            self.assertFalse(manager.lock.locked())
            ready.set()
            async with asyncio.timeout(5):
                while not (manager.state / "autostart").exists():
                    await asyncio.sleep(0.01)
        manager.unwatch()

    async def test_large_stream_rejected_before_remaining_body_is_read(self):
        reads = []

        async def body():
            reads.append(1)
            yield b"x" * (ADMIN_BODY_LIMIT + 1)
            reads.append(2)
            yield b"never consume the remainder"

        response = await self.client.post("/api/settings", content=body(), headers=self.headers)
        self.assertEqual(response.status_code, 413)
        self.assertEqual(reads, [1])
        self.assertFalse(self.app.state.manager.path.exists())
        response = await self.client.post(
            "/api/settings",
            content=b"{}",
            headers={**self.headers, "Content-Length": str(ADMIN_BODY_LIMIT + 1)},
        )
        self.assertEqual(response.status_code, 413)

    async def test_slow_body_expires_and_releases_mutation_gate(self):
        async def body():
            yield b"{"
            await asyncio.sleep(1)
            yield b"}"

        with patch("control.routes.ADMIN_BODY_TIMEOUT", 0.02):
            response = await self.client.post("/api/settings", content=body(), headers=self.headers)
        self.assertEqual(response.status_code, 408)
        self.assertFalse(self.app.state.manager.lock.locked())
        self.assertEqual(
            (
                await self.client.post("/api/settings-export", json={}, headers=self.headers)
            ).status_code,
            200,
        )

    async def test_malformed_structures_and_deep_json_are_controlled(self):
        payloads = [
            {"projects": [None]},
            {"services": {"codex": None}},
            {"services": []},
            {"projects": [{"id": []}]},
            {"services": {"codex": {"permissions": []}}},
        ]
        with patch("control.integrations.inventory", return_value={}):
            for payload in payloads:
                response = await self.client.post(
                    "/api/settings", json=payload, headers=self.headers
                )
                self.assertEqual(response.status_code, 400, payload)
                self.assertIn("error", response.json())
        response = await self.client.post(
            "/api/settings", content="[" * 2000 + "0" + "]" * 2000, headers=self.headers
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(self.app.state.manager.path.exists())

    async def test_concurrent_mutation_rejected_without_queueing(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def scan():
            entered.set()
            await release.wait()
            return {}

        self.app.state.manager.refresh = scan
        first = asyncio.create_task(self.client.post("/api/scan", json={}, headers=self.headers))
        await entered.wait()
        try:
            second = await asyncio.wait_for(
                self.client.post("/api/scan", json={}, headers=self.headers), 0.5
            )
            self.assertEqual(second.status_code, 429)
            self.assertEqual(second.headers["retry-after"], "1")
        finally:
            release.set()
            self.assertEqual((await first).status_code, 200)

    async def test_cli_operation_limit_does_not_prevent_cancel(self):
        manager = self.app.state.manager
        manager.operations.jobs = {
            str(i): {"state": "running"} for i in range(ADMIN_OPERATION_LIMIT)
        }
        with patch.object(manager.operations, "launch") as launch:
            for endpoint in ("provider-login", "integration", "model-install", "local-start"):
                response = await self.client.post("/api/" + endpoint, json={}, headers=self.headers)
                self.assertEqual(response.status_code, 429)
            launch.assert_not_called()
        self.assertEqual(
            (
                await self.client.post(
                    "/api/cancel-operation", json={"id": "0"}, headers=self.headers
                )
            ).status_code,
            200,
        )

    async def test_csp_allows_data_images_for_the_select_chevron(self):
        for path in ("/", "/api/state"):
            response = await self.client.get(path, headers=self.headers)
            self.assertIn("img-src 'self' data:", response.headers["content-security-policy"])

    async def test_panel_static_files_are_compressed_and_revalidated(self):
        for path in ("/admin.js", "/admin.css", "/assets/tabler.min.css"):
            first = await self.client.get(path, headers={"Accept-Encoding": "gzip"})
            self.assertEqual(first.status_code, 200)
            self.assertEqual(first.headers["content-encoding"], "gzip")
            self.assertEqual(first.headers["cache-control"], "no-cache")
            again = await self.client.get(path, headers={"If-None-Match": first.headers["etag"]})
            self.assertEqual(again.status_code, 304)
        # The page that sets the admin cookie and the API stay uncached and uncompressed.
        page = await self.client.get("/", headers={"Accept-Encoding": "gzip"})
        self.assertEqual(page.headers["cache-control"], "no-store")
        api = await self.client.get(
            "/api/state", headers={**self.headers, "Accept-Encoding": "gzip"}
        )
        self.assertEqual(api.headers["cache-control"], "no-store")
        self.assertNotIn("content-encoding", api.headers)

    def stranger(self):
        """Another account on this computer: loopback, but without the install secret."""
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://127.0.0.1:8094"
        )

    async def test_admin_cookie_needs_install_secret(self):
        key = Path(self.folder.name) / local_access.KEY_FILE
        self.assertEqual(stat.S_IMODE(key.stat().st_mode), 0o600)
        async with self.stranger() as client:
            page = await client.get("/")
            self.assertNotIn("admin", page.cookies)
            client.cookies.set(local_access.COOKIE, "guess")
            self.assertNotIn("admin", (await client.get("/")).cookies)
            response = await client.get("/api/state", headers=self.headers)
            self.assertEqual(response.status_code, 401)
            self.assertIn("keepharness open", response.json()["error"])

    async def test_raw_secret_cookie_from_before_sessions_is_refused(self):
        async with self.stranger() as client:
            client.cookies.set(local_access.COOKIE, self.app.state.manager.local_secret)
            self.assertNotIn("admin", (await client.get("/")).cookies)
            response = await client.get("/api/state", headers=self.headers)
            self.assertEqual(response.status_code, 401)
            self.assertIn("keepharness open", response.json()["error"])

    async def test_revoked_or_expired_sessions_sign_the_browser_out(self):
        state = self.app.state.manager.state
        async with self.stranger() as client:
            client.cookies.set(local_access.COOKIE, local_access.issue_session(state))
            self.assertIn("admin", (await client.get("/")).cookies)
        self.assertGreaterEqual(local_access.revoke_sessions(state), 1)
        async with self.stranger() as client:
            client.cookies.set(local_access.COOKIE, local_access.issue_session(state, now=0))
            self.assertNotIn("admin", (await client.get("/")).cookies)

    async def test_open_link_without_a_writable_session_list_signs_nobody_in(self):
        ticket = local_access.open_ticket(self.app.state.manager.local_secret)
        with patch.object(local_access, "issue_session", side_effect=PermissionError):
            async with self.stranger() as client:
                opened = await client.get("/open", params={"ticket": ticket})
                self.assertEqual(opened.status_code, 503)
                self.assertNotIn(local_access.COOKIE, client.cookies)
                self.assertNotIn("admin", client.cookies)

    async def test_open_link_admits_one_browser_once(self):
        ticket = local_access.open_ticket(self.app.state.manager.local_secret)
        async with self.stranger() as client:
            opened = await client.get("/open", params={"ticket": ticket})
            self.assertEqual((opened.status_code, opened.headers["location"]), (303, "/"))
            self.assertEqual(opened.headers["cache-control"], "no-store")
            # The browser gets a random session token; only its SHA-256 stays on disk.
            token = client.cookies.get(local_access.COOKIE)
            self.assertNotEqual(token, self.app.state.manager.local_secret)
            sessions = local_access.read_sessions(self.app.state.manager.state)
            self.assertIn(local_access.digest(token), sessions)
            stored = (Path(self.folder.name) / local_access.SESSIONS_FILE).read_text()
            self.assertNotIn(token, stored)
            self.assertNotIn(self.app.state.manager.local_secret, stored)
            self.assertEqual(
                (await client.get("/api/state", headers=self.headers)).status_code, 200
            )
        async with self.stranger() as replay:
            again = await replay.get("/open", params={"ticket": ticket})
            self.assertEqual(again.status_code, 403)
            self.assertNotIn(local_access.COOKIE, replay.cookies)
            forged = await replay.get("/open", params={"ticket": ticket[:-1] + "0"})
            self.assertEqual(forged.status_code, 403)


def test_open_command_prints_a_one_time_link(tmp_path, capsys, monkeypatch):
    from urllib.parse import parse_qs, urlsplit

    from control.cli import main

    opened = []
    monkeypatch.setattr("webbrowser.open", opened.append)
    main(["--state", str(tmp_path), "--port", "18094", "open"])
    link = capsys.readouterr().out.strip().splitlines()[-1]
    assert link.startswith("http://127.0.0.1:18094/open?ticket=")
    assert opened == [link]
    secret = (tmp_path / local_access.KEY_FILE).read_text()
    assert secret not in link
    ticket = parse_qs(urlsplit(link).query)["ticket"][0]
    used = {}
    assert local_access.consume_ticket(secret, ticket, used)
    assert not local_access.consume_ticket(secret, ticket, used)


def test_sessions_are_private_hashed_bounded_and_revocable(tmp_path):
    import stat as stat_module

    tokens = [local_access.issue_session(tmp_path, now=1000 + i) for i in range(3)]
    path = tmp_path / local_access.SESSIONS_FILE
    assert stat_module.S_IMODE(path.stat().st_mode) == 0o600
    assert all(token not in path.read_text() for token in tokens)
    cookies = {local_access.COOKIE: tokens[0]}
    assert local_access.has_session(cookies, tmp_path, now=1001)
    assert not local_access.has_session(cookies, tmp_path, now=1000 + local_access.COOKIE_SECONDS)
    assert not local_access.has_session({local_access.COOKIE: "x" * 300}, tmp_path, now=1001)
    # A full list drops its oldest sessions first.
    for i in range(local_access.MAX_SESSIONS):
        local_access.issue_session(tmp_path, now=2000 + i)
    assert len(local_access.read_sessions(tmp_path)) == local_access.MAX_SESSIONS
    assert not local_access.has_session(cookies, tmp_path, now=2000)
    latest = {local_access.COOKIE: local_access.issue_session(tmp_path, now=3000)}
    assert local_access.revoke_sessions(tmp_path) == local_access.MAX_SESSIONS
    assert not local_access.has_session(latest, tmp_path, now=3001)
    # A damaged list signs nobody in.
    path.write_text("not json")
    assert local_access.read_sessions(tmp_path) == {}


def test_open_revoke_signs_every_browser_out(tmp_path, capsys):
    from control.cli import main

    cookies = {local_access.COOKIE: local_access.issue_session(tmp_path)}
    main(["--state", str(tmp_path), "open", "--revoke"])
    assert "Signed out 1 browser session(s)." in capsys.readouterr().out
    assert not local_access.has_session(cookies, tmp_path)
