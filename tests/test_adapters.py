"""Provider boundaries: dispatch never silently falls back to another provider."""

import asyncio
import importlib
from unittest.mock import AsyncMock, patch

import pytest

from agent_service.tools import ToolError


@pytest.mark.parametrize("provider", ["codex", "claude", "gemini", "deepseek", "local"])
def test_native_dispatch_uses_only_the_selected_adapter(provider, tmp_path):
    adapters = importlib.import_module("Adapters")
    implementation = importlib.import_module(f"Adapters.{provider}.backend")
    config = {"binary": "fixture"}
    project = {"permissions": {}}
    event = lambda *args: None
    approve = AsyncMock()
    expected = {"answer": "fixture", "backend": provider}
    with patch.object(
        implementation, "run_native", AsyncMock(return_value=expected)
    ) as run:
        result = asyncio.run(
            adapters.run_native(
                config,
                "prompt",
                event,
                project,
                "model",
                "configured",
                tmp_path,
                provider,
                approve,
            )
        )
    assert result == expected
    run.assert_awaited_once_with(
        config,
        "prompt",
        event,
        project,
        "model",
        "configured",
        tmp_path,
        approve,
    )


@pytest.mark.parametrize("provider", ["codex", "claude"])
def test_scoped_dispatch_uses_provider_specific_implementation(provider):
    adapters = importlib.import_module("Adapters")
    implementation = importlib.import_module(f"Adapters.{provider}.backend")
    with patch.object(
        implementation, "run_scoped", AsyncMock(return_value={"answer": "ok"})
    ) as run:
        result = asyncio.run(
            adapters.run_scoped(
                {},
                "prompt",
                None,
                {},
                "model",
                "low",
                staged={"project/a": "text"},
                session_dir=None,
                provider=provider,
            )
        )
    assert result == {"answer": "ok"}
    run.assert_awaited_once_with(
        {},
        "prompt",
        None,
        {},
        "model",
        "low",
        staged={"project/a": "text"},
        session_dir=None,
    )


@pytest.mark.parametrize("provider", ["local", "deepseek", "unknown"])
def test_unsupported_scoped_provider_is_rejected(provider):
    adapters = importlib.import_module("Adapters")
    with pytest.raises(ToolError, match="backend_unavailable"):
        asyncio.run(adapters.run_scoped({}, "", None, provider=provider))


def test_unknown_native_provider_is_rejected(tmp_path):
    adapters = importlib.import_module("Adapters")
    with pytest.raises(ToolError, match="backend_unavailable"):
        asyncio.run(
            adapters.run_native({}, "", None, {}, "", "", tmp_path, "unknown", None)
        )
