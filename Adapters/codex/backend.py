"""Public entry points for the Codex provider."""

import json

from Adapters.shared.workspace import prepare_workspace

from .native import RuntimeOptions, build_command, run_turn
from .scoped import run as run_scoped

SPEC_REVISION = 5

__all__ = ["SPEC_REVISION", "run_native", "run_scoped"]


async def run_native(config, prompt, event, project, model, effort, session_dir, approve):
    workspace = prepare_workspace(project, prompt, session_dir)
    command = build_command(config["binary"], workspace.permissions)
    model_provider = config.get("local_provider")
    if model_provider:
        command += ["-c", "model_provider=" + json.dumps(model_provider)]
    runtime = RuntimeOptions(command, model_provider=model_provider)
    return await run_turn(
        config,
        event,
        project,
        model,
        effort,
        session_dir,
        approve,
        workspace,
        runtime,
        "codex",
    )
