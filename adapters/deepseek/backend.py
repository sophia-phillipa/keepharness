"""DeepSeek BYOK policy over Codex's client-managed Responses history."""

from control.product import PRODUCT

import json
import os
from pathlib import Path

from adapters.codex.native import RuntimeOptions, build_command, run_turn
from adapters.shared.workspace import prepare_workspace
from agent_service.tools import ToolError

SPEC_REVISION = 1
EFFORTS = ("configured", "none", "low", "high", "max")


def runtime_options(config, permissions):
    """Never fall back to the user's global OpenAI provider or credentials."""
    api = config.get("api_provider") or {}
    if not config.get("binary") or not api.get("url") or not api.get("key_file"):
        raise ToolError("deepseek_api_configuration_required")
    environment = {**os.environ, PRODUCT.env_prefix + "_API_KEY": Path(api["key_file"]).read_text().strip()}
    command = build_command(config["binary"], permissions, hosted_search=False)
    command += [
        "-c",
        'model_provider="tail_api"',
        "-c",
        'model_providers.tail_api.name="DeepSeek"',
        "-c",
        "model_providers.tail_api.base_url=" + json.dumps(api["url"]),
        "-c",
        'model_providers.tail_api.wire_api="responses"',
        "-c",
        "model_providers.tail_api.requires_openai_auth=false",
        "-c",
        'model_providers.tail_api.env_key=' + json.dumps(PRODUCT.env_prefix + '_API_KEY'),
        # The stateless HTTP API needs full client history, not websocket deltas.
        "-c",
        "model_providers.tail_api.supports_websockets=false",
        "-c",
        "model_supports_reasoning_summaries=true",
        "-c",
        'model_reasoning_summary="none"',
    ]
    return RuntimeOptions(
        command,
        environment=environment,
        model_provider="tail_api",
        session_metadata={
            "adapter": "deepseek",
            "adapter_spec_revision": SPEC_REVISION,
        },
    )


async def run_native(config, prompt, event, project, model, effort, session_dir, approve):
    if effort not in EFFORTS:
        raise ToolError("deepseek_effort_unavailable")
    runtime = runtime_options(config, project.get("permissions", {}))
    workspace = prepare_workspace(project, prompt, session_dir)
    # "configured" means this provider's documented default, not the OpenAI profile.
    effective_effort = "high" if effort == "configured" else effort
    result = await run_turn(
        config,
        event,
        project,
        model,
        effective_effort,
        session_dir,
        approve,
        workspace,
        runtime,
        "deepseek",
    )
    # thread_id identifies Codex's local history, not server-side DeepSeek state.
    return {
        **result,
        "effort": effective_effort,
        "context_strategy": "client_history_replay",
        "adapter_spec_revision": SPEC_REVISION,
    }
