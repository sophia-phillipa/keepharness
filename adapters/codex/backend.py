"""Public entry points for the Codex provider."""

import json
import tempfile

from adapters.shared.resources import copy_resource
from adapters.shared.workspace import prepare_workspace

from .native import RuntimeOptions, build_command, run_turn
from .scoped import run as run_scoped

SPEC_REVISION = 5

__all__ = ["SPEC_REVISION", "run_native", "run_scoped"]


async def run_native(config, prompt, event, project, model, effort, session_dir, approve):
    workspace = prepare_workspace(project, prompt, session_dir)
    command = build_command(config["binary"], workspace.permissions)
    if config.get("_effect_capability"):
        from agent_service.effect_transport import server_spec

        spec = server_spec(config["_effect_capability"])
        # Codex deep-merges config tables. A fresh, unguessable execution name
        # prevents inherited host URL/env/tool filters from entering this server.
        name = config["_effect_capability"]["server_name"]
        for key, value in {**spec, "enabled": True}.items():
            command += ["-c", "mcp_servers." + name + "." + key + "=" + json.dumps(value)]
    model_provider = config.get("local_provider")
    if model_provider:
        command += ["-c", "model_provider=" + json.dumps(model_provider)]
    runtime = RuntimeOptions(command, model_provider=model_provider)
    with tempfile.TemporaryDirectory(prefix="codex-agents-", dir=workspace.home) as directory:
        if workspace.permissions.get("read"):
            for item in project.get("_resources", []):
                if item["kind"] != "agent":
                    continue
                path = copy_resource(item, directory, ".toml")
                key = "agents." + json.dumps(item["name"])
                command += [
                    "-c",
                    key + ".config_file=" + json.dumps(str(path)),
                    "-c",
                    key + ".description=" + json.dumps(item.get("description", item["name"])),
                ]
                event(
                    "resource_materialized",
                    {
                        "resource_id": item["resource_id"],
                        "revision": item["revision"],
                        "kind": "agent",
                    },
                )
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
