import asyncio
from unittest.mock import AsyncMock

import pytest

from Adapters.codex.rpc import sync_title


@pytest.mark.parametrize("error", [RuntimeError("codex_rpc_error"), asyncio.TimeoutError()])
def test_title_failure_does_not_start_inference_or_claim_success(error):
    rpc = AsyncMock()
    rpc.call.side_effect = error
    events = []
    asyncio.run(sync_title(rpc, "thread", "Display title", lambda k, v: events.append(k)))
    rpc.call.assert_awaited_once_with(
        "thread/name/set", {"threadId": "thread", "name": "Display title"}
    )
    assert events == ["session_title_sync_failed"]


@pytest.mark.parametrize("title", [None, "", "   "])
def test_empty_title_does_not_issue_rpc(title):
    rpc = AsyncMock()
    asyncio.run(sync_title(rpc, "thread", title, lambda *a: None))
    rpc.call.assert_not_called()


def test_scoped_codex_sets_title_on_creation_and_resume(tmp_path):
    from contextlib import asynccontextmanager, nullcontext
    from types import SimpleNamespace
    from unittest.mock import patch

    from Adapters.codex.scoped import run

    rpc = AsyncMock()
    rpc.call.return_value = {"thread": {"id": "scoped-id"}}
    rpc.receive.return_value = {
        "method": "turn/completed",
        "params": {"turn": {"status": "completed"}},
    }

    @asynccontextmanager
    async def connection(*args, **kwargs):
        yield rpc

    workspace = SimpleNamespace(command=[], home=tmp_path)
    with (
        patch(
            "Adapters.codex.scoped.prepare_scoped", side_effect=lambda *a: nullcontext(workspace)
        ),
        patch("Adapters.codex.scoped.connection", connection),
        patch("Adapters.codex.scoped.collect_changes", return_value={}),
    ):
        for title in ("First title", "Renamed title"):
            asyncio.run(
                run(
                    {},
                    "Prompt",
                    lambda *a: None,
                    project={"_conversation_title": title},
                    session_dir=tmp_path,
                )
            )
    calls = rpc.call.await_args_list
    assert [c.args[0] for c in calls] == [
        "thread/start",
        "thread/name/set",
        "thread/resume",
        "thread/name/set",
    ]
    assert calls[1].args[1] == {"threadId": "scoped-id", "name": "First title"}
    assert calls[3].args[1] == {"threadId": "scoped-id", "name": "Renamed title"}
