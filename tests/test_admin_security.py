"""Administrative boundary checks; isolated state, no CLI or inference calls."""

import asyncio
import tempfile
import unittest
from unittest.mock import patch

import httpx

from control.server import ADMIN_BODY_LIMIT, ADMIN_OPERATION_LIMIT, create_app


class AdminSecurityTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.app = create_app(self.folder.name)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://127.0.0.1:8094"
        )
        self.headers = {"X-Harness-Admin": "1"}
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
