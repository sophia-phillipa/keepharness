"""Claude stream-json sessions, tool approvals and process lifetime."""

import asyncio
import contextlib
import json
from pathlib import Path

from adapters.shared.process import child_environment, process_diagnostics
from adapters.shared.provider_setup import (
    child_source,
    claude_access_settings,
    instructions,
    version_notice,
)
from agent_service.tools import ToolError

from .stream import Stream

MISSING_SESSION = "No conversation found with session ID:"


def build_command(config, model, home, permissions, selected, access_mode, additional_roots):
    """Select a native permission mode and add only KeepHarness-owned servers."""
    selected_servers = {}
    policy = config.get("_project_security", {})
    if config.get("_effect_capability"):
        from agent_service.effect_transport import server_spec

        selected_servers["harness_effects"] = server_spec(config["_effect_capability"])
    mcp = home / "mcp.json"
    mcp.write_text(json.dumps({"mcpServers": selected_servers}))
    mcp.chmod(0o600)
    settings = {}
    if policy:
        settings["disabledMcpjsonServers"] = policy["disabled_servers"]
        if not policy["trusted"]:
            settings["disableAllHooks"] = True
    command = [
        config["binary"],
        "--print",
        "--verbose",
        "--output-format",
        "stream-json",
        "--input-format",
        "stream-json",
        "--include-partial-messages",
        "--permission-prompt-tool",
        "stdio",
        "--permission-prompts",
        "host",
        "--model",
        model,
        "--mcp-config",
        str(mcp),
        "--settings",
        json.dumps(settings),
    ]
    if policy and not policy["trusted"]:
        command += ["--setting-sources", "user"]
    command += [
        "--permission-mode",
        claude_access_settings(access_mode, permissions, config.get("unrestricted"))[
            "permissionMode"
        ],
    ]
    if additional_roots:
        command += ["--add-dir", *additional_roots]
    if permissions.get("delegate"):
        command += ["--forward-subagent-text"]
        if config.get("agents_file"):
            command += ["--agents", config["agents_file"]]
    return command


def project_security(config, root):
    """Read owner CLI trust at the execution boundary; read failures never launch a process."""
    from adapters.shared.provider_state import project_trusted
    from control.provider_state import ProviderStateService

    state = Path(config.get("control_state_dir") or config.get("state_dir") or root)
    service = ProviderStateService(state, lambda: [], track_notices=False)
    trusted = project_trusted(
        root, codex=service._adapter("codex"), claude=service._adapter("claude")
    )
    claude = service._adapter("claude")
    servers = claude.project_servers(root)
    approved = claude.approved_project_servers(root, trusted=trusted)
    runnable = approved & claude.enabled_project_servers(root)
    return {
        "trusted": trusted,
        "project_servers": sorted(servers),
        "approved_servers": {name: servers[name] for name in runnable},
        "disabled_servers": sorted(servers.keys() - runnable),
    }


@contextlib.asynccontextmanager
async def resumable(marker):
    """Claude Code keys sessions by cwd: once the state folder moves, --resume finds nothing.

    The stored session is set aside and ``native_session_missing`` lets the caller start a
    fresh one seeded with the harness history. Wraps ``process_diagnostics`` for its stderr.
    """
    try:
        yield
    except (ToolError, OSError) as exc:
        if not marker.exists() or MISSING_SESSION not in getattr(exc, "error_detail", ""):
            raise
        marker.replace(marker.with_name(marker.name + ".before-session-missing"))
        raise ToolError("native_session_missing") from exc


async def answer_questions(inputs, approve):
    """Map Claude's question batch to typed gates, retaining provider input fields."""
    questions = inputs.get("questions") if isinstance(inputs, dict) else None
    if not isinstance(questions, list) or not 1 <= len(questions) <= 4:
        raise ToolError("claude_invalid_question")
    gates = []
    seen = set()
    for question in questions:
        if not isinstance(question, dict):
            raise ToolError("claude_invalid_question")
        text = question.get("question")
        options = question.get("options")
        multi_select = question.get("multiSelect", False)
        if (
            not isinstance(text, str)
            or not text.strip()
            or text in seen
            or not isinstance(options, list)
            or not 1 <= len(options) <= 100
            or type(multi_select) is not bool
        ):
            raise ToolError("claude_invalid_question")
        seen.add(text)
        labels = set()
        mapped = []
        for index, option in enumerate(options):
            label = option.get("label") if isinstance(option, dict) else None
            if not isinstance(label, str) or not label or label in labels:
                raise ToolError("claude_invalid_question")
            labels.add(label)
            mapped.append(
                {"id": str(index), "label": label, "description": option.get("description", "")}
            )
        gates.append({"question": text, "options": mapped, "multi_select": multi_select})
    answers = {}
    for gate in gates:
        reply = await approve("gate", gate)
        if not reply.get("approved"):
            return None
        choice = reply.get("choice")
        choices = choice if gate["multi_select"] else [choice]
        labels = {option["id"]: option["label"] for option in gate["options"]}
        if (
            not isinstance(choices, list)
            or not choices
            or any(not isinstance(item, str) or item not in labels for item in choices)
        ):
            raise ToolError("claude_invalid_question_answer")
        answers[gate["question"]] = ", ".join(labels[item] for item in choices)
    return {**inputs, "answers": answers}


async def run(
    config,
    prompt,
    event,
    cwd,
    model,
    home,
    permissions,
    selected,
    approve,
    images=None,
    access_mode="ask",
    additional_roots=None,
    title=None,
    effort="configured",
):
    config = {
        **config,
        "_project_security": await asyncio.to_thread(project_security, config, Path(cwd)),
    }
    await asyncio.to_thread(
        version_notice, config["binary"], "claude", event, child_source(config, "claude")
    )
    command = build_command(
        config, model, home, permissions, selected, access_mode, additional_roots
    )
    if effort != "configured":
        command += ["--effort", effort]
    command += [
        "--append-system-prompt",
        "\n".join(filter(None, [instructions(config), config.get("append_system_prompt")])),
    ]
    event(
        "hook_scope",
        {"scope": "global_and_project" if config["_project_security"]["trusted"] else "disabled"},
    )
    marker = home / "claude-session.json"
    if marker.exists():
        command += ["--resume", json.loads(marker.read_text())["id"]]
    if isinstance(title, str) and title.strip():
        command += ["--name", title]
    proc = await asyncio.create_subprocess_exec(
        *command,
        cwd=cwd,
        env=child_environment(child_source(config, "claude")),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
        limit=2 * 1024 * 1024,
    )
    state = Stream(event, config, root=cwd)
    session = None

    async def send(value):
        proc.stdin.write((json.dumps(value) + "\n").encode())
        await state.watchdog.wait(proc.stdin.drain())

    async with resumable(marker), process_diagnostics(proc, "claude", event):
        await send(
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": prompt}]
                    + [
                        {"type": "image", "source": {"type": "base64", **item}}
                        for item in (images or [])
                    ],
                },
            }
        )
        while True:
            line = await state.watchdog.wait(proc.stdout.readline())
            if not line:
                break
            item = json.loads(line)
            if item.get("session_id"):
                session = item["session_id"]
            if item.get("type") == "control_request":
                req = item.get("request", {})
                updated = req.get("input", {})
                if req.get("tool_name") == "AskUserQuestion":
                    updated = await answer_questions(updated, approve)
                    decision = updated is not None
                else:
                    reply = await approve("claude/" + req.get("subtype", "permission"), req)
                    decision = reply.get("approved", False)
                response = (
                    {"behavior": "allow", "updatedInput": updated}
                    if decision
                    else {"behavior": "deny", "message": "Denied by user"}
                )
                await send(
                    {
                        "type": "control_response",
                        "response": {
                            "subtype": "success",
                            "request_id": item["request_id"],
                            "response": response,
                        },
                    }
                )
                continue
            state.consume(item)
            if item.get("type") == "result":
                break
        result = state.finish(model, effort)
        if session:
            marker.write_text(json.dumps({"id": session}))
            result["thread_id"] = session
        result["context_strategy"] = "native_session"
        return result
