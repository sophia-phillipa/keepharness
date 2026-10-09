from unittest.mock import patch

from adapters.claude.stream import Stream


def test_claude_live_usage_counts_messages_once_and_preserves_final_metrics():
    events = []
    with patch("adapters.claude.stream.time.monotonic", return_value=10):
        stream = Stream(lambda *args: events.append(args))
    with patch("adapters.claude.stream.time.monotonic", return_value=12):
        for message_id, counts in [("one", [2, 20, 20]), ("two", [1, 10])]:
            stream.consume(
                {
                    "type": "stream_event",
                    "event": {"type": "message_start", "message": {"id": message_id, "usage": {}}},
                }
            )
            for count in counts:
                stream.consume(
                    {
                        "type": "stream_event",
                        "event": {"type": "message_delta", "usage": {"output_tokens": count}},
                    }
                )
        metrics = [data for kind, data in events if kind == "usage_metrics"]
        assert [m["output_tokens"] for m in metrics] == [2, 20, 20, 21, 30]
        assert metrics[-1]["inference_seconds"] == 2
        stream.consume(
            {
                "type": "result",
                "subtype": "success",
                "usage": {"output_tokens": 30},
                "result": "done",
            }
        )
        assert stream.finish("fixture")["metrics"]["output_tokens"] == 30


def test_claude_missing_or_invalid_usage_never_estimates_from_text():
    events = []
    stream = Stream(lambda *args: events.append(args))
    stream.consume(
        {"type": "stream_event", "event": {"type": "message_start", "message": {"id": "one"}}}
    )
    for count in [None, -1, True, "20", float("nan")]:
        stream.consume(
            {
                "type": "stream_event",
                "event": {"type": "message_delta", "usage": {"output_tokens": count}},
            }
        )
    assert not events


def test_native_codex_live_usage_excludes_other_sessions(tmp_path):
    import asyncio
    from contextlib import asynccontextmanager

    from adapters.codex.backend import run_native

    def usage(thread, count):
        return {
            "method": "thread/tokenUsage/updated",
            "params": {
                "threadId": thread,
                "tokenUsage": {"total": {"outputTokens": count}, "last": {"outputTokens": count}},
            },
        }

    class RPC:
        notifications = iter(
            [
                {"method": "turn/started"},
                usage("own", 20),
                usage("other", 500),
                usage("own", 30),
                {"method": "turn/completed", "params": {"turn": {"status": "completed"}}},
            ]
        )

        async def call(self, *args):
            return {"thread": {"id": "own"}}

        async def send(self, *args):
            pass

        async def receive(self):
            return next(self.notifications)

    @asynccontextmanager
    async def connection(*args, **kwargs):
        yield RPC()

    events = []
    with (
        patch("adapters.codex.native.connection", connection),
    ):
        result = asyncio.run(
            run_native(
                {"binary": "fixture"},
                "fixture",
                lambda *args: events.append(args),
                {},
                None,
                "low",
                tmp_path,
                None,
            )
        )
    assert result["metrics"]["output_tokens"] == 30
    live = [data["metrics"] for kind, data in events if kind == "context_usage"]
    assert [m["output_tokens"] for m in live] == [20, 30]
    assert all(m["inference_seconds"] > 0 for m in live)
