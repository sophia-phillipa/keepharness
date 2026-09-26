"""Regression for cumulative usage notifications before a resumed turn starts."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from Adapters import run_native as run
from agent_service.tools import ToolError


class UsageStreamTest(unittest.IsolatedAsyncioTestCase):
    async def execute(self, root, notifications, events=None):
        executable = root / "fixture-cli"
        executable.write_text(
            "#!"
            + sys.executable
            + "\n"
            + """
import json, sys
notifications = """
            + repr(notifications)
            + """
for line in sys.stdin:
    request = json.loads(line)
    method = request.get('method')
    if method == 'initialize':
        print(json.dumps({'id': request['id'], 'result': {}}), flush=True)
    elif method == 'thread/resume':
        print(json.dumps({'id': request['id'], 'result': {'thread': {'id': 'fixture-session'}}}), flush=True)
    elif method == 'turn/start':
        for event in notifications:
            print(json.dumps(event), flush=True)
"""
        )
        executable.chmod(0o700)

        async def approve(*args):
            raise AssertionError("Usage fixture must not request permissions")

        with (
            patch("Adapters.codex.native.configurations", return_value={"codex": {}}),
            patch("Adapters.codex.native.inventory", return_value={"codex": []}),
        ):
            return await run(
                {"binary": str(executable)},
                "fixture",
                lambda *args: events.append(args) if events is not None else None,
                {"permissions": {}},
                "fixture-model",
                "low",
                root / "session",
                "codex",
                approve,
            )

    async def test_resumed_usage_ignores_pre_turn_and_duplicate_notifications(self):
        def usage(inputs, outputs):
            return {
                "method": "thread/tokenUsage/updated",
                "params": {
                    "tokenUsage": {
                        "total": {"inputTokens": inputs, "outputTokens": outputs},
                        "last": {"inputTokens": 100, "outputTokens": 30},
                    }
                },
            }

        for baseline in (None, {"inputTokens": 900, "outputTokens": 400}):
            with self.subTest(baseline=baseline), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                marker = root / "session" / "native-thread.json"
                marker.parent.mkdir()
                saved = {"id": "fixture-session"}
                if baseline is not None:
                    saved["usage_total"] = baseline
                marker.write_text(json.dumps(saved))
                events = []
                foreign = usage(90000, 80000)
                foreign["params"]["threadId"] = "another-session"
                result = await self.execute(
                    root,
                    [
                        usage(1000, 444),
                        {"method": "turn/started"},
                        usage(1100, 478),
                        usage(1100, 478),
                        foreign,
                        usage(1200, 505),
                        {"method": "turn/completed", "params": {"turn": {"status": "completed"}}},
                    ],
                    events,
                )
                live = [data for kind, data in events if kind == "context_usage"]
                self.assertEqual(live[-1]["metrics"]["output_tokens"], 61)
                self.assertGreater(live[-1]["metrics"]["inference_seconds"], 0)
                self.assertEqual(len(live), 4)
                self.assertEqual(result["metrics"]["usage_scope"], "turn")
                self.assertEqual(result["metrics"]["input_tokens"], 200)
                self.assertEqual(result["metrics"]["output_tokens"], 61)
                self.assertEqual(
                    json.loads(marker.read_text())["usage_total"],
                    {"inputTokens": 1200, "outputTokens": 505},
                )

    async def test_rejected_turn_preserves_existing_usage_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / "session" / "native-thread.json"
            marker.parent.mkdir()
            saved = {
                "id": "fixture-session",
                "usage_total": {"inputTokens": 500, "outputTokens": 80},
            }
            marker.write_text(json.dumps(saved))
            with self.assertRaisesRegex(ToolError, "fixture rejection"):
                await self.execute(
                    root,
                    [{"method": "error", "params": {"error": {"message": "fixture rejection"}}}],
                )
            self.assertEqual(json.loads(marker.read_text()), saved)
