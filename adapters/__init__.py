"""Explicit provider dispatch; transport details belong to each adapter."""

from agent_service.tools import ToolError

from .claude import backend as claude
from .codex import backend as codex
from .deepseek import backend as deepseek
from .gemini import backend as gemini
from .local import backend as local

PROVIDERS = {
    "codex": codex,
    "claude": claude,
    "gemini": gemini,
    "deepseek": deepseek,
    "local": local,
}


def get_adapter(provider):
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
    adapter = get_adapter(provider)
    if not hasattr(adapter, "run_scoped"):
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
