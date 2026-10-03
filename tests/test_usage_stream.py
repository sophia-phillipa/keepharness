"""Regression for cumulative usage notifications before a resumed turn starts."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from adapters import run_native as run
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
            patch("adapters.codex.native.configurations", return_value={"codex": {}}),
            patch("adapters.codex.native.inventory", return_value={"codex": []}),
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


# --------------------------------------------------------------------------------- Claude


def claude_call(stream, message_id, usage, parent=None):
    item = {
        "type": "stream_event",
        "event": {"type": "message_start", "message": {"id": message_id, "usage": usage}},
    }
    stream.consume({**item, "parent_tool_use_id": parent} if parent else item)


def test_claude_context_usage_is_the_last_calls_whole_prompt_not_the_turns_sum():
    from adapters.claude.stream import Stream

    events = []
    stream = Stream(lambda kind, data: events.append((kind, data)))
    # A tool-using turn: two model calls that share one cached prefix.
    claude_call(
        stream,
        "one",
        {
            "input_tokens": 2,
            "cache_read_input_tokens": 47053,
            "cache_creation_input_tokens": 9575,
            "output_tokens": 1,
        },
    )
    claude_call(
        stream,
        "two",
        {
            "input_tokens": 3,
            "cache_read_input_tokens": 56628,
            "cache_creation_input_tokens": 200,
            "output_tokens": 1,
        },
    )
    live = [data for kind, data in events if kind == "context_usage"]
    assert [item["last"]["totalTokens"] for item in live] == [56630, 56831]
    assert live[-1]["last"]["cachedInputTokens"] == 56628
    assert live[-1]["metrics"]["output_tokens"] == 2
    assert not [kind for kind, _ in events if kind == "usage_metrics"]
    stream.consume(
        {
            "type": "result",
            "subtype": "success",
            "result": "done",
            "usage": {
                "input_tokens": 5,
                "cache_read_input_tokens": 103681,
                "cache_creation_input_tokens": 9775,
                "output_tokens": 29,
            },
        }
    )
    result = stream.finish("fixture")
    assert result["context_usage"]["last"]["totalTokens"] == 56831
    metrics = result["metrics"]
    # Turn totals are cumulative and count the cache, like Codex's input_tokens does.
    assert metrics["usage_scope"] == "turn"
    assert metrics["input_tokens"] == 5 + 103681 + 9775
    assert (metrics["cached_tokens"], metrics["cache_creation_tokens"]) == (103681, 9775)
    assert metrics["output_tokens"] == 29


def test_claude_subagent_calls_do_not_move_the_main_conversations_context():
    from adapters.claude.stream import Stream

    events = []
    stream = Stream(lambda kind, data: events.append((kind, data)))
    claude_call(stream, "main", {"input_tokens": 10, "output_tokens": 1})
    claude_call(stream, "sub", {"input_tokens": 99999, "output_tokens": 1}, parent="toolu_1")
    stream.consume({"type": "result", "subtype": "success", "result": "ok", "usage": {}})
    assert stream.finish("fixture")["context_usage"]["last"]["totalTokens"] == 10


def test_claude_without_input_counts_reports_no_context_and_no_made_up_input():
    from adapters.claude.stream import Stream

    stream = Stream(lambda *_: None)
    claude_call(stream, "one", {"output_tokens": 4})
    stream.consume(
        {"type": "result", "subtype": "success", "result": "ok", "usage": {"output_tokens": 4}}
    )
    result = stream.finish("fixture")
    assert "context_usage" not in result
    assert result["metrics"]["input_tokens"] is None


def test_a_finished_run_reports_its_queue_time_apart_from_its_total(tmp_path):
    """The UI shows "Worked for" as total_seconds - queue_seconds (QA-R1-2); keep both honest."""
    import asyncio
    import time
    from unittest.mock import AsyncMock

    from agent_service.app import Service
    from tests.test_shared_projects import config

    async def exercise():
        service = Service(config(tmp_path))
        service.execute = AsyncMock(return_value={"answer": "ok"})
        identity = ("a", service.config["clients"]["a"])
        job = service.submit(
            identity,
            {
                "project_id": "sem-projeto",
                "backend": "codex",
                "model": "codex-test",
                "effort": "low",
                "prompt": "hello",
            },
        )
        # The job has waited four and a half minutes behind other work.
        service.db.execute(
            "UPDATE jobs SET created=? WHERE id=?", (time.time() - 277, job["job_id"])
        )
        service.db.commit()
        worker = asyncio.create_task(service.worker())
        try:
            async with asyncio.timeout(2):
                while service.job(identity, job["job_id"])["state"] in ("queued", "running"):
                    await asyncio.sleep(0.01)
            result = json.loads(service.job(identity, job["job_id"])["result"])
            assert 276 <= result["queue_seconds"] <= 280
            assert 0 <= result["total_seconds"] - result["queue_seconds"] < 2
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            service.db.close()

    asyncio.run(exercise())
