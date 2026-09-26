import asyncio
from unittest.mock import AsyncMock, patch

from Adapters.gemini.backend import run_native


def test_gemini_reports_unsupported_title_without_fabricating_acp_method(tmp_path):
    events = []
    with patch(
        "Adapters.gemini.backend.native.run", AsyncMock(return_value={"answer": "ok"})
    ) as run:
        asyncio.run(
            run_native(
                {},
                "User prompt",
                lambda k, v: events.append((k, v)),
                {"permissions": {}, "_conversation_title": "Shared title"},
                "auto",
                "configured",
                tmp_path,
                None,
            )
        )
    assert events == [
        (
            "session_title_sync_unsupported",
            {"backend": "gemini", "reason": "gemini_acp_title_unsupported"},
        )
    ]
    assert run.call_args.args[1] == "User prompt"
