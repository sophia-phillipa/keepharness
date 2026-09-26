"""Explicit provider dispatch; transport details belong to each adapter."""

from collections.abc import Mapping
from types import MappingProxyType

from agent_service.tools import ToolError

from .base import ProviderAdapter, ScopedProviderAdapter

# Suffixed names: a bare ``claude`` here would replace the ``adapters.claude`` subpackage
# attribute with its backend module and break ``import adapters.claude.account``.
from .claude import backend as claude_backend
from .codex import backend as codex_backend
from .deepseek import backend as deepseek_backend
from .gemini import backend as gemini_backend
from .local import backend as local_backend

PROVIDERS: Mapping[str, ProviderAdapter] = MappingProxyType(
    {
        "codex": codex_backend,
        "claude": claude_backend,
        "gemini": gemini_backend,
        "deepseek": deepseek_backend,
        "local": local_backend,
    }
)
# Gemini is listed because it answers run_scoped with its own gemini_scoped_unsupported.
SCOPED_PROVIDERS: Mapping[str, ScopedProviderAdapter] = MappingProxyType(
    {"codex": codex_backend, "claude": claude_backend, "gemini": gemini_backend}
)


def get_adapter(provider) -> ProviderAdapter:
    try:
        return PROVIDERS[provider]
    except KeyError:
        raise ToolError("backend_unavailable") from None


async def run_native(config, prompt, event, project, model, effort, session_dir, provider, approve):
    adapter = get_adapter(provider)
    return await adapter.run_native(
        config, prompt, event, project, model, effort, session_dir, approve
    )


async def run_scoped(
    config,
    prompt,
    event,
    project=None,
    model="gpt-6-astra",
    effort="low",
    staged=None,
    session_dir=None,
    provider="codex",
):
    get_adapter(provider)
    adapter = SCOPED_PROVIDERS.get(provider)
    if adapter is None:
        raise ToolError("backend_unavailable")
    return await adapter.run_scoped(
        config,
        prompt,
        event,
        project,
        model,
        effort,
        staged=staged,
        session_dir=session_dir,
    )
