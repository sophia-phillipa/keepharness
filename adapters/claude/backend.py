"""Public entry points for Claude Code; effort is passed explicitly when selected."""

import tempfile

from adapters.shared.resources import copy_resource
from adapters.shared.workspace import prepare_workspace

from . import native
from .scoped import run as run_scoped

SPEC_REVISION = 5

__all__ = ["SPEC_REVISION", "run_native", "run_scoped"]


async def run_native(config, prompt, event, project, model, effort, session_dir, approve):
    workspace = prepare_workspace(project, prompt, session_dir)
    config = {
        **config,
        "resource_skills": [
            item["name"] for item in project.get("_resources", []) if item["kind"] == "skill"
        ],
    }
    if any(item.get("native_command") for item in project.get("_resources", [])):
        config["append_system_prompt"] = workspace.prompt.removesuffix(prompt)
        workspace.prompt = prompt
    with tempfile.TemporaryDirectory(prefix="claude-rules-", dir=workspace.home) as directory:
        if workspace.permissions.get("read"):
            for item in project.get("_rules", []):
                copy_resource(item, directory, ".md")
                context = (
                    "\nProject rule fallback (advisory path scoping; native scoped loading is not certified). "
                    "Apply this rule only to files matching its original paths frontmatter; "
                    "a rule without paths applies throughout the selected project.\n"
                    + item["_text"]
                    + "\n"
                )
                config["append_system_prompt"] = config.get("append_system_prompt", "") + context
                event(
                    "resource_fallback",
                    {
                        "kind": "rule",
                        "resource_id": item["resource_id"],
                        "revision": item["revision"],
                        "mode": "inline",
                        "scope": "advisory",
                    },
                )
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
            project.get("_conversation_title"),
            effort=effort,
        )
