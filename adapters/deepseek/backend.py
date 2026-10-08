"""DeepSeek BYOK policy over Codex's client-managed Responses history."""

import json

from adapters.codex.native import RuntimeOptions, build_command, run_turn
from adapters.shared.provider_setup import command_permissions
from adapters.shared.workspace import prepare_workspace
from agent_service.secret_vault import SecretRedactionScope, SecretStream, redact_secrets
from agent_service.tools import ToolError
from control.product import PRODUCT

from .credentials import check_shell_configuration, private_environment

SPEC_REVISION = 1
EFFORTS = ("configured", "none", "low", "high", "max")


def runtime_options(config, permissions):
    """Never fall back to the user's global OpenAI provider or credentials."""
    api = config.get("api_provider") or {}
    if not config.get("binary") or not api.get("url") or not api.get("key_file"):
        raise ToolError("deepseek_api_configuration_required")
    source, token = private_environment(config)
    environment = {
        **source,
        PRODUCT.env_prefix + "_API_KEY": token,
    }
    command = build_command(
        config["binary"],
        command_permissions(config, permissions),
        hosted_search=False,
        host_config=False,
    )
    command += [
        "-c",
        'cli_auth_credentials_store="file"',
        "-c",
        "allow_login_shell=false",
        "-c",
        'shell_environment_policy={inherit="all",ignore_default_excludes=false,filters={'
        + PRODUCT.env_prefix
        + '_API_KEY="exclude"},set={},experimental_use_profile=false}',
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
        "model_providers.tail_api.env_key=" + json.dumps(PRODUCT.env_prefix + "_API_KEY"),
        # The stateless HTTP API needs full client history, not websocket deltas.
        "-c",
        "model_providers.tail_api.supports_websockets=false",
        "-c",
        'model_reasoning_summary="none"',
    ]
    return RuntimeOptions(
        command,
        check_configuration=check_shell_configuration,
        environment=environment,
        model_provider="tail_api",
        session_metadata={
            "provider": "deepseek",
            "engine": "codex",
            "adapter": "deepseek",
            "adapter_spec_revision": SPEC_REVISION,
        },
    )


async def run_native(config, prompt, event, project, model, effort, session_dir, approve):
    if effort not in EFFORTS:
        raise ToolError("deepseek_effort_unavailable")
    runtime = runtime_options(config, project.get("permissions", {}))
    redaction = SecretRedactionScope(runtime.environment[PRODUCT.env_prefix + "_API_KEY"])
    stream = SecretStream()

    def safe_event(kind, data):
        if kind in ("answer_delta", "reasoning_delta", "reasoning_summary") and isinstance(
            data.get("text"), str
        ):
            data = {
                **data,
                "text": stream.feed((kind, data.get("parent_tool_use_id")), data["text"]),
            }
        event(kind, redact_secrets(data))

    workspace = prepare_workspace(project, prompt, session_dir)
    # "configured" means this provider's documented default, not the OpenAI profile.
    effective_effort = "high" if effort == "configured" else effort
    try:
        result = redact_secrets(
            await run_turn(
                config,
                safe_event,
                project,
                model,
                effective_effort,
                session_dir,
                approve,
                workspace,
                runtime,
                "deepseek",
            )
        )
    except Exception as exc:
        clean = redact_secrets(str(exc))
        if clean != str(exc):
            raise ToolError(clean) from None
        raise
    finally:
        # Keep the weakly registered credential alive through event/error sanitization.
        del redaction
    # thread_id identifies Codex's local history, not server-side DeepSeek state.
    return {
        **result,
        "effort": effective_effort,
        "context_strategy": "client_history_replay",
        "adapter_spec_revision": SPEC_REVISION,
    }
