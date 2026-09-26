import json
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from starlette.testclient import TestClient

from adapters import run_native as run
from adapters.deepseek import account as deepseek
from control.server import create_app


class DeepseekTest(unittest.IsolatedAsyncioTestCase):
    async def test_models_balance_and_key_privacy(self):
        with tempfile.TemporaryDirectory() as d:
            deepseek.store_key(d, "fixture-secret-key-123")
            self.assertEqual(deepseek.key_file(d).stat().st_mode & 0o777, 0o600)
            original = httpx.AsyncClient

            def respond(request):
                self.assertEqual(request.headers["Authorization"], "Bearer fixture-secret-key-123")
                return httpx.Response(
                    200,
                    json={"data": [{"id": "deepseek-test"}]}
                    if request.url.path == "/models"
                    else {
                        "is_available": True,
                        "balance_infos": [{"currency": "USD", "total_balance": "1.00"}],
                    },
                )

            with patch(
                "adapters.deepseek.account.httpx.AsyncClient",
                side_effect=lambda **kw: original(**kw, transport=httpx.MockTransport(respond)),
            ):
                result = await deepseek.check(d)
            self.assertTrue(result["authenticated"])
            self.assertIn("deepseek-test", result["models"])
            self.assertNotIn("fixture-secret", json.dumps(result))

    async def test_rejected_key_does_not_leak_response(self):
        with tempfile.TemporaryDirectory() as d:
            deepseek.store_key(d, "fixture-secret-key-123")
            original = httpx.AsyncClient
            with patch(
                "adapters.deepseek.account.httpx.AsyncClient",
                side_effect=lambda **kw: original(
                    **kw,
                    transport=httpx.MockTransport(
                        lambda r: httpx.Response(401, text="fixture-secret-key-123")
                    ),
                ),
            ):
                with self.assertRaisesRegex(ValueError, "rejected") as caught:
                    await deepseek.check(d)
            self.assertNotIn("fixture-secret", str(caught.exception))

    async def test_native_api_provider_routes_without_key_in_arguments(self):
        captured = {}

        class RPC:
            async def call(self, method, params):
                captured[method] = params
                return {"thread": {"id": "test"}}

            async def send(self, *args):
                pass

            async def receive(self):
                return {"method": "turn/completed", "params": {"turn": {"status": "completed"}}}

        @asynccontextmanager
        async def connection(command, **kw):
            captured["command"] = command
            captured["env"] = kw["env"]
            yield RPC()

        with tempfile.TemporaryDirectory() as d:
            key = Path(d, "key")
            key.write_text("fixture-private-key")
            with (
                patch("adapters.codex.native.connection", connection),
                patch("adapters.codex.native.configurations", return_value={"codex": {}}),
                patch("adapters.codex.native.inventory", return_value={"codex": []}),
            ):
                result = await run(
                    {
                        "binary": "codex",
                        "api_provider": {"url": deepseek.API, "key_file": str(key)},
                    },
                    "test",
                    lambda *a: None,
                    {"permissions": {}},
                    "deepseek-test",
                    "high",
                    Path(d) / "session",
                    "deepseek",
                    AsyncMock(),
                )
            self.assertEqual(captured["thread/start"]["modelProvider"], "tail_api")
            self.assertEqual(captured["env"]["TAIL_HARNESS_API_KEY"], "fixture-private-key")
            self.assertNotIn("fixture-private-key", " ".join(captured["command"]))
            self.assertIn(
                "model_providers.tail_api.requires_openai_auth=false", captured["command"]
            )
            self.assertEqual(result["backend"], "deepseek")


class ProviderLifecycleTest(unittest.TestCase):
    def test_byok_export_and_delete(self):
        inventory = {
            "platform": "Linux",
            "services": [],
            "binaries": {},
            "projects": [],
            "network": {"online": False, "hostname": None},
        }
        with (
            tempfile.TemporaryDirectory() as d,
            patch("control.server.scan", AsyncMock(return_value=inventory)),
        ):
            with TestClient(create_app(d), base_url="http://127.0.0.1:8094") as c:
                c.get("/")
                h = {"X-Harness-Admin": "1"}
                self.assertEqual(
                    c.post(
                        "/api/provider-token",
                        json={"provider": "deepseek", "token": "fixture-secret-key"},
                        headers=h,
                    ).status_code,
                    200,
                )
                self.assertTrue(c.get("/api/state").json()["credentials"]["deepseek"])
                self.assertNotIn("fixture-secret-key", c.get("/api/state").text)
                self.assertNotIn(
                    "fixture-secret-key", c.post("/api/settings-export", json={}, headers=h).text
                )
                self.assertEqual(
                    c.post(
                        "/api/provider-delete", json={"provider": "deepseek"}, headers=h
                    ).status_code,
                    200,
                )
                self.assertFalse(deepseek.key_file(d).exists())
