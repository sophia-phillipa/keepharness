"""Public entry points for Gemini CLI; its effort remains CLI-configured."""

from agent_service.tools import ToolError
from Adapters.shared.workspace import prepare_workspace

from . import native


SPEC_REVISION = 2


async def run_native(
    config, prompt, event, project, model, effort, session_dir, approve
):
    if effort != "configured":
        raise ToolError("gemini_effort_unavailable")
    workspace = prepare_workspace(project, prompt, session_dir)
    if project.get("_conversation_title"):
        event("session_title_sync_unsupported", {"backend": "gemini", "reason": "gemini_acp_title_unsupported"})
    return await native.run(
        config,
        workspace.prompt,
        event,
        workspace.cwd,
        model,
        workspace.home,
        workspace.permissions,
        project.get("access_mode", "ask"),
        workspace.images,
        approve,
        workspace.roots[1:],
    )


async def run_scoped(
    config,
    prompt,
    event,
    project=None,
    model="auto",
    effort="configured",
    staged=None,
    session_dir=None,
):
    """Do not claim isolated write support before Gemini exposes that contract."""
    raise ToolError("gemini_scoped_unsupported")
