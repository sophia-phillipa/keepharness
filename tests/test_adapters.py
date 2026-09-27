"""Provider boundaries: dispatch never silently falls back to another provider."""

import asyncio
import importlib
import inspect
from unittest.mock import AsyncMock, patch

import pytest

from agent_service.tools import ToolError


@pytest.mark.parametrize("provider", ["codex", "claude", "gemini", "deepseek", "local"])
def test_native_dispatch_uses_only_the_selected_adapter(provider, tmp_path):
    adapters = importlib.import_module("adapters")
    implementation = importlib.import_module(f"adapters.{provider}.backend")
    config = {"binary": "fixture"}
    project = {"permissions": {}}

    def event(*args):
        return None

    approve = AsyncMock()
    expected = {"answer": "fixture", "backend": provider}
    with patch.object(implementation, "run_native", AsyncMock(return_value=expected)) as run:
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
    adapters = importlib.import_module("adapters")
    implementation = importlib.import_module(f"adapters.{provider}.backend")
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
    adapters = importlib.import_module("adapters")
    with pytest.raises(ToolError, match="backend_unavailable"):
        asyncio.run(adapters.run_scoped({}, "", None, provider=provider))


def test_unknown_native_provider_is_rejected(tmp_path):
    adapters = importlib.import_module("adapters")
    with pytest.raises(ToolError, match="backend_unavailable"):
        asyncio.run(adapters.run_native({}, "", None, {}, "", "", tmp_path, "unknown", None))


def _parameters(function, *, skip_self=False):
    parameters = list(inspect.signature(function).parameters.values())
    return [(item.name, item.kind) for item in parameters[1 if skip_self else 0 :]]


def test_registries_are_read_only_and_cover_every_scoped_backend():
    adapters = importlib.import_module("adapters")
    with pytest.raises(TypeError):
        adapters.PROVIDERS["other"] = object()
    with pytest.raises(TypeError):
        adapters.SCOPED_PROVIDERS["other"] = object()
    assert set(adapters.PROVIDERS) == {"codex", "claude", "gemini", "deepseek", "local"}
    assert set(adapters.SCOPED_PROVIDERS) == {
        name for name, module in adapters.PROVIDERS.items() if "run_scoped" in vars(module)
    }
    for name, module in adapters.SCOPED_PROVIDERS.items():
        assert adapters.PROVIDERS[name] is module


@pytest.mark.parametrize("provider", ["codex", "claude", "gemini", "deepseek", "local"])
def test_backend_signatures_match_the_adapter_protocols(provider):
    from adapters.base import ProviderAdapter, ScopedProviderAdapter

    implementation = importlib.import_module(f"adapters.{provider}.backend")
    assert _parameters(implementation.run_native) == _parameters(
        ProviderAdapter.run_native, skip_self=True
    )
    if "run_scoped" in vars(implementation):
        assert _parameters(implementation.run_scoped) == _parameters(
            ScopedProviderAdapter.run_scoped, skip_self=True
        )


def test_gemini_scoped_keeps_its_own_unsupported_error():
    adapters = importlib.import_module("adapters")
    with pytest.raises(ToolError, match="^gemini_scoped_unsupported$"):
        asyncio.run(adapters.run_scoped({}, "", None, provider="gemini"))


@pytest.mark.parametrize("provider", ["codex", "claude", "gemini", "deepseek", "local"])
def test_package_attributes_are_the_provider_subpackages_not_their_backends(provider):
    """F-33: the dispatch registry must not shadow ``adapters.<provider>``."""
    import sys

    import adapters
    import adapters.claude.account as account

    assert account is sys.modules["adapters.claude.account"]
    assert getattr(adapters, provider) is sys.modules[f"adapters.{provider}"]


def test_no_phantom_codex_model_fallback():
    """F-53: a turn always carries its own model; nothing falls back to a made-up id."""
    from adapters.codex import scoped
    from agent_service.services.conversation_service import ConversationService

    adapters = importlib.import_module("adapters")
    for function in (adapters.run_scoped, scoped.run):
        assert inspect.signature(function).parameters["model"].default is None
    assert "gpt-6-astra" not in inspect.getsource(ConversationService)
