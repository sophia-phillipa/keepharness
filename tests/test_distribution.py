"""Distribution, restart policy, service templates and offline lifecycle tests."""

import asyncio
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from starlette.testclient import TestClient

from control.download_model import download
from control.install import files
from control.server import Manager, create_app

INVENTORY = {
    "platform": "Linux",
    "services": [],
    "binaries": {},
    "projects": [],
    "network": {"online": False, "hostname": None},
}


class DistributionTest(unittest.TestCase):
    def test_service_paths_and_permissions(self):
        config = files(Path("/tmp/user with space"), "/tmp/env/bin/python", 8100)
        self.assertEqual(len(config), 3)
        unit = next(text for path, (text, mode) in config.items() if path.suffix == ".service")
        self.assertIn("--port 8100", unit)
        self.assertIn("Restart=on-failure", unit)
        self.assertIn("UMask=0077", unit)
        self.assertNotIn("sudo", unit)
        self.assertIn('"/tmp/user with space/.local/share/tail-harness"', unit)
        self.assertEqual(
            next(mode for path, (text, mode) in config.items() if path.name == "tail-harness-open"),
            0o700,
        )

    def test_cli_help_and_bad_port(self):
        result = subprocess.run(
            [sys.executable, "-m", "control", "--help"], capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("--scan", result.stdout)
        result = subprocess.run(
            [sys.executable, "-m", "control", "--port", "0"], capture_output=True
        )
        self.assertNotEqual(result.returncode, 0)

    def test_packaged_assets_exist(self):
        from importlib.resources import files as resources

        for package, names in [
            ("control", ["admin.js", "admin.css", "index.html"]),
            ("agent_service", ["ui.js", "ui.css", "index.html", "VERSION", "project_mcp.py"]),
        ]:
            for name in names:
                self.assertTrue(resources(package).joinpath(name).is_file(), (package, name))

    def test_restart_failed_keeps_admin_available(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "autostart").touch()
            manager = Manager(d)
            manager.settings["services"]["codex"].update(enabled=True, models=["fixture"])
            Path(d, "settings.json").write_text(json.dumps(manager.settings))
            with (
                patch("control.discovery.scan", AsyncMock(return_value=INVENTORY)),
                patch.object(
                    Manager, "start", AsyncMock(side_effect=ValueError("CLI unavailable"))
                ) as start,
            ):
                with TestClient(create_app(d), base_url="http://127.0.0.1:8094") as client:
                    client.get("/")
                    state = client.get("/api/state").json()
                    self.assertEqual(state["status"]["startup_error"], "CLI unavailable")
                    start.assert_awaited_once()

    def test_fresh_install_does_not_enable_providers(self):
        with tempfile.TemporaryDirectory() as d:
            with (
                patch("control.discovery.scan", AsyncMock(return_value=INVENTORY)),
                patch.object(Manager, "start", AsyncMock()) as start,
            ):
                with TestClient(create_app(d), base_url="http://127.0.0.1:8094") as c:
                    c.get("/")
                    state = c.get("/api/state").json()
                    self.assertFalse(
                        any(s["enabled"] for s in state["settings"]["services"].values())
                    )
                    start.assert_not_awaited()

    def test_manual_stop_disables_restore_shutdown_preserves_it(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as d:
                m = Manager(d)
                marker = Path(d, "autostart")
                marker.touch()
                await m.stop(force=True)
                self.assertTrue(marker.exists())
                await m.stop()
                self.assertFalse(marker.exists())

        asyncio.run(scenario())

    def test_model_checksum_mismatch_discards_partial_file(self):
        import io

        with tempfile.TemporaryDirectory() as d:
            with (
                patch(
                    "control.download_model.CATALOG",
                    {"test": ("owner/model", "revision", "model.gguf", "wrong")},
                ),
                patch(
                    "control.download_model.urllib.request.urlopen",
                    return_value=io.BytesIO(b"fixture"),
                ),
            ):
                with self.assertRaisesRegex(ValueError, "SHA-256"):
                    download("test", d)
            self.assertEqual(list(Path(d).iterdir()), [])

    def test_model_checksum_success(self):
        import hashlib
        import io

        with tempfile.TemporaryDirectory() as d:
            with (
                patch(
                    "control.download_model.CATALOG",
                    {
                        "test": (
                            "owner/model",
                            "revision",
                            "model.gguf",
                            hashlib.sha256(b"fixture").hexdigest(),
                        )
                    },
                ),
                patch(
                    "control.download_model.urllib.request.urlopen",
                    return_value=io.BytesIO(b"fixture"),
                ),
            ):
                download("test", d)
            self.assertEqual(Path(d, "model.gguf").read_bytes(), b"fixture")


class ReadinessTest(unittest.TestCase):
    def test_wait_retries_until_http_ready(self):
        from unittest.mock import MagicMock

        from control.install import wait_ready

        response = MagicMock()
        response.__enter__.return_value.status = 200
        with (
            patch(
                "control.install.urllib.request.urlopen",
                side_effect=[OSError("starting"), response],
            ) as request,
            patch("control.install.time.sleep"),
        ):
            wait_ready(8100)
            self.assertEqual(request.call_count, 2)
