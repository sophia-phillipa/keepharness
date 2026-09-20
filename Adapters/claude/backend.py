"""Public entry points for Claude Code; effort follows its configured CLI profile."""

from Adapters.shared.workspace import prepare_workspace
from . import native
from .scoped import run as run_scoped


SPEC_REVISION = 1

async def run_native(
    config, prompt, event, project, model, effort, session_dir, approve
):
    workspace = prepare_workspace(project, prompt, session_dir)
    return await native.run(
        config,
        workspace.prompt,
        event,
        workspace.cwd,
        model,
        workspace.home,
        workspace.permissions,
        config.get("integrations", []),
        approve,
        workspace.images,
        project.get("access_mode", "ask"),
        workspace.roots[1:],
    )
