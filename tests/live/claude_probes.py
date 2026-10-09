"""Opt-in Claude Code probes using only the synthetic conformance fixture."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import signal
import sys
import time
from pathlib import Path
from typing import Any, Callable

try:
    from .conformance import IsolatedFixture, isolated_fixture, sanitized_argv
except ImportError:  # Direct execution from tests/live.
    from conformance import IsolatedFixture, isolated_fixture, sanitized_argv


MODEL = "haiku"
PROBE_TIMEOUT_SECONDS = 600
PROBES = (
    "command",
    "ask_user_question",
    "delegation",
    "todowrite",
    "connectors",
    "path_rule",
    "hooks",
    "mcp_prepare",
    "mcp_block",
)
_SECRET_KEY = re.compile(r"(?i)(api[_-]?key|oauth|authorization|password|secret|credential)")


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if _SECRET_KEY.search(str(key)) else _sanitize(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if isinstance(value, str) and len(value) > 8_000:
        return value[:8_000] + "[TRUNCATED]"
    return value


def _tool_uses(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    uses: list[dict[str, Any]] = []
    for event in events:
        if event.get("type") == "assistant":
            for block in event.get("message", {}).get("content", []):
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    uses.append(block)
        nested = event.get("event")
        if (
            event.get("type") == "stream_event"
            and isinstance(nested, dict)
            and nested.get("type") == "content_block_start"
            and nested.get("content_block", {}).get("type") == "tool_use"
        ):
            uses.append(nested["content_block"])
    return uses


def _has_parent_tool_id(events: list[dict[str, Any]]) -> bool:
    return any(
        isinstance(event.get("parent_tool_use_id"), str) and event["parent_tool_use_id"]
        for event in events
    )


def _final_text(events: list[dict[str, Any]]) -> str:
    for event in reversed(events):
        if event.get("type") == "result" and isinstance(event.get("result"), str):
            return event["result"]
    return ""


def _assistant_models(events: list[dict[str, Any]]) -> list[str]:
    return sorted(
        {
            event.get("message", {}).get("model")
            for event in events
            if event.get("type") == "assistant"
            and isinstance(event.get("message", {}).get("model"), str)
        }
    )


def _initialized_tools(events: list[dict[str, Any]]) -> list[str]:
    init = next(
        (
            event
            for event in events
            if event.get("type") == "system" and event.get("subtype") == "init"
        ),
        {},
    )
    return [tool for tool in init.get("tools", []) if isinstance(tool, str)]


def _base_command(
    fixture: IsolatedFixture,
    *,
    tools: str = "",
    setting_sources: str = "user,project",
    settings: dict[str, Any] | None = None,
    strict_mcp: bool = True,
    mcp_config: Path | None = None,
    forward_subagents: bool = False,
    model: str = MODEL,
) -> list[str]:
    binary = shutil.which("claude", path=fixture.env.get("PATH"))
    if not binary:
        raise RuntimeError("Claude Code is not installed")
    command = [
        binary,
        "--print",
        "--verbose",
        "--output-format",
        "stream-json",
        "--input-format",
        "stream-json",
        "--include-partial-messages",
        "--permission-prompts",
        "host",
        "--permission-prompt-tool",
        "stdio",
        "--permission-mode",
        "default",
        "--no-session-persistence",
        "--model",
        model,
        "--tools",
        tools,
        "--setting-sources",
        setting_sources,
        "--settings",
        json.dumps(settings or {"disableAllHooks": True}),
    ]
    if strict_mcp:
        command.append("--strict-mcp-config")
    if mcp_config is not None:
        command.extend(["--mcp-config", str(mcp_config)])
    if forward_subagents:
        command.append("--forward-subagent-text")
    return command


async def _read_stderr(stream: asyncio.StreamReader) -> str:
    chunks: list[bytes] = []
    length = 0
    while True:
        chunk = await stream.read(8_192)
        if not chunk:
            break
        chunks.append(chunk)
        length += len(chunk)
        while length > 65_536 and chunks:
            length -= len(chunks.pop(0))
    return b"".join(chunks).decode(errors="replace")


async def _turn(
    fixture: IsolatedFixture,
    command: list[str],
    prompt: str,
    control_input: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=fixture.project,
        env=fixture.env,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
        limit=8 * 1024 * 1024,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    assert process.stderr is not None
    stderr_task = asyncio.create_task(_read_stderr(process.stderr))
    events: list[dict[str, Any]] = []
    controls: list[dict[str, Any]] = []

    async def send(value: dict[str, Any]) -> None:
        process.stdin.write((json.dumps(value) + "\n").encode())
        await process.stdin.drain()

    try:
        await send(
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": prompt}],
                },
            }
        )
        async with asyncio.timeout(PROBE_TIMEOUT_SECONDS):
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                event = json.loads(line)
                events.append(event)
                if event.get("type") == "control_request":
                    request = event.get("request", {})
                    updated = (
                        control_input(request)
                        if control_input is not None
                        else request.get("input", {})
                    )
                    controls.append(
                        {
                            "request_id": event.get("request_id"),
                            "request": request,
                            "updatedInput": updated,
                        }
                    )
                    await send(
                        {
                            "type": "control_response",
                            "response": {
                                "subtype": "success",
                                "request_id": event["request_id"],
                                "response": {
                                    "behavior": "allow",
                                    "updatedInput": updated,
                                },
                            },
                        }
                    )
                    continue
                if event.get("type") == "result":
                    break
    finally:
        if process.returncode is None:
            process.stdin.close()
            try:
                await asyncio.wait_for(process.wait(), 5)
            except asyncio.TimeoutError:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(process.wait(), 5)
                except asyncio.TimeoutError:
                    os.killpg(process.pid, signal.SIGKILL)
                    await process.wait()
        stderr = await stderr_task
    return {
        "command": sanitized_argv(command),
        "events": _sanitize(events),
        "controls": _sanitize(controls),
        "stderr": _sanitize(stderr[-8_000:]),
        "returncode": process.returncode,
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }


def _report(name: str, version: str, runs: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "probe": name,
        "version": version,
        "model": MODEL,
        "runs": runs,
        "verdict": "unsupported",
        "evidence": {},
    }


async def _run(fixture: IsolatedFixture, name: str) -> dict[str, Any]:
    binary = shutil.which("claude", path=fixture.env.get("PATH"))
    if not binary:
        raise RuntimeError("Claude Code is not installed")
    version_process = await asyncio.create_subprocess_exec(
        binary,
        "--version",
        env=fixture.env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    version_bytes, _ = await asyncio.wait_for(version_process.communicate(), 15)
    version = version_bytes.decode(errors="replace").strip()
    report = _report(name, version, [])

    if name == "command":
        run = await _turn(
            fixture,
            _base_command(fixture, model="sonnet"),
            "/synthetic-probe alpha beta",
        )
        report["runs"].append(run)
        text = _final_text(run["events"])
        command_models = _assistant_models(run["events"])
        report["evidence"] = {
            "requested_model": "sonnet",
            "command_models": command_models,
            "result": text,
        }
        if "SYNTHETIC_COMMAND_ARGUMENTS=alpha beta" in text and any(
            "haiku" in model for model in command_models
        ):
            report["verdict"] = "supported"

    elif name == "ask_user_question":

        def answer(request: dict[str, Any]) -> dict[str, Any]:
            updated = dict(request.get("input", {}))
            questions = updated.get("questions", [])
            if questions:
                updated["answers"] = {questions[0]["question"]: "Alpha"}
            return updated

        run = await _turn(
            fixture,
            _base_command(fixture, tools="AskUserQuestion"),
            "Use AskUserQuestion exactly once. Ask 'Choose the synthetic option?' "
            "with header 'Choice', options Alpha and Beta with short descriptions, "
            "and multiSelect false. After the answer, reply CHOICE=<answer>.",
            answer,
        )
        report["runs"].append(run)
        controls = [
            item
            for item in run["controls"]
            if item.get("request", {}).get("tool_name") == "AskUserQuestion"
        ]
        options = []
        if controls:
            questions = controls[0]["request"].get("input", {}).get("questions", [])
            if questions:
                options = [option.get("label") for option in questions[0].get("options", [])]
        report["evidence"] = {
            "control_request": bool(controls),
            "options": options,
            "updatedInput": controls[0].get("updatedInput") if controls else None,
            "result": _final_text(run["events"]),
        }
        if controls and {"Alpha", "Beta"}.issubset(options):
            report["verdict"] = "supported"

    elif name == "delegation":
        run = await _turn(
            fixture,
            _base_command(fixture, tools="Task,Read", forward_subagents=True),
            "Delegate exactly one tiny task to demo--writer. Ask it for its configured "
            "marker, wait for it, then report that marker. Do nothing else.",
        )
        report["runs"].append(run)
        names = [item.get("name") for item in _tool_uses(run["events"])]
        parent = _has_parent_tool_id(run["events"])
        report["evidence"] = {
            "delegation_tools": names,
            "parent_tool_use_id": parent,
            "result": _final_text(run["events"]),
        }
        if (
            "Agent" in names
            and parent
            and "SYNTHETIC_PROJECT_AGENT_WRITER" in report["evidence"]["result"]
        ):
            report["verdict"] = "supported"
        elif "Task" in names:
            report["verdict"] = "fallback"

    elif name == "todowrite":
        run = await _turn(
            fixture,
            _base_command(fixture, tools="TodoWrite"),
            "Use TodoWrite exactly once to create one in_progress item named "
            "'Synthetic probe'. Then reply TODO_RECORDED. Do nothing else.",
        )
        report["runs"].append(run)
        names = [item.get("name") for item in _tool_uses(run["events"])]
        report["evidence"] = {
            "initialized_tools": _initialized_tools(run["events"]),
            "tools": names,
            "result": _final_text(run["events"]),
        }
        if "TodoWrite" in names:
            report["verdict"] = "supported"

    elif name == "connectors":
        empty_mcp = fixture.evidence_dir / "empty-mcp.json"
        empty_mcp.write_text('{"mcpServers": {}}', encoding="utf-8")
        strict = await _turn(
            fixture,
            _base_command(fixture, mcp_config=empty_mcp, strict_mcp=True),
            "Reply exactly CONNECTOR_INVENTORY_PROBE. Do not call any tool.",
        )
        loose = await _turn(
            fixture,
            _base_command(fixture, mcp_config=empty_mcp, strict_mcp=False),
            "Reply exactly CONNECTOR_INVENTORY_PROBE. Do not call any tool.",
        )
        report["runs"].extend([strict, loose])

        def inventory(run: dict[str, Any]) -> dict[str, Any]:
            init = next((item for item in run["events"] if item.get("type") == "system"), {})
            return {
                "mcp_servers": init.get("mcp_servers", []),
                "tools": [
                    tool
                    for tool in init.get("tools", [])
                    if isinstance(tool, str) and tool.startswith("mcp__")
                ],
            }

        strict_inventory = inventory(strict)
        loose_inventory = inventory(loose)
        report["evidence"] = {
            "strict": strict_inventory,
            "without_strict": loose_inventory,
            "connector_tool_calls": [
                item.get("name")
                for run in (strict, loose)
                for item in _tool_uses(run["events"])
                if str(item.get("name", "")).startswith("mcp__")
            ],
        }
        if report["evidence"]["connector_tool_calls"]:
            report["verdict"] = "unsupported"
            report["blocker"] = "A connector tool was called despite the probe instruction"
        elif strict_inventory != loose_inventory:
            report["verdict"] = "supported"
        else:
            report["verdict"] = "fallback"
            report["blocker"] = "No account connector was present to distinguish strict mode"

    elif name == "path_rule":
        symlink_run = await _turn(
            fixture,
            _base_command(fixture, tools="Read", setting_sources="project"),
            "Read @probe-target.txt, follow every applicable path rule, and reply "
            "with only the required marker.",
        )
        rule = fixture.project / ".claude" / "rules" / "synthetic-path-rule.md"
        rule_source = rule.resolve()
        rule.unlink()
        shutil.copy2(rule_source, rule)
        copied_run = await _turn(
            fixture,
            _base_command(fixture, tools="Read", setting_sources="project"),
            "Read @probe-target.txt, follow every applicable path rule, and reply "
            "with only the required marker.",
        )
        report["runs"].extend([symlink_run, copied_run])
        symlink_text = _final_text(symlink_run["events"])
        copied_text = _final_text(copied_run["events"])
        report["evidence"] = {
            "symlink_result": symlink_text,
            "regular_file_control": copied_text,
        }
        if "SYNTHETIC_PATH_RULE_MARKER" in symlink_text:
            report["verdict"] = "supported"
        elif "SYNTHETIC_PATH_RULE_MARKER" in copied_text:
            report["verdict"] = "unsupported"
            report["blocker"] = "Claude ignored the externally symlinked path rule"
        else:
            report["verdict"] = "blocked"
            report["blocker"] = "The regular-file control did not trigger path-rule loading"

    elif name == "hooks":
        hook_log = Path(fixture.env["SYNTHETIC_HOOK_LOG"])
        project_run = await _turn(
            fixture,
            _base_command(
                fixture,
                setting_sources="project",
                settings={"disableAllHooks": False},
            ),
            "Reply exactly PROJECT_HOOK_PROBE. Do not call tools.",
        )
        project_markers = hook_log.read_text().splitlines() if hook_log.exists() else []
        if hook_log.exists():
            hook_log.unlink()
        merged_run = await _turn(
            fixture,
            _base_command(
                fixture,
                setting_sources="user,project",
                settings={"disableAllHooks": False},
            ),
            "Reply exactly MERGED_HOOK_PROBE. Do not call tools.",
        )
        merged_markers = hook_log.read_text().splitlines() if hook_log.exists() else []
        report["runs"].extend([project_run, merged_run])
        report["evidence"] = {
            "project_sources": project_markers,
            "user_and_project_sources": merged_markers,
        }
        if (
            "project-hook" in project_markers
            and "global-auto-update-hook" not in project_markers
            and "project-hook" in merged_markers
            and "global-auto-update-hook" in merged_markers
        ):
            report["verdict"] = "fallback"
            report["blocker"] = (
                "disableAllHooks:false does not isolate explicit project hooks; "
                "setting_sources=project is required"
            )

    elif name in ("mcp_prepare", "mcp_block"):
        mcp_config = fixture.evidence_dir / "synthetic-mcp.json"
        mcp_config.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "synthetic": {
                            "command": sys.executable,
                            "args": [str(Path(__file__).with_name("probe_mcp_server.py"))],
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        tool = "prepare" if name == "mcp_prepare" else "block"
        run = await _turn(
            fixture,
            _base_command(
                fixture,
                tools=f"mcp__synthetic__{tool}",
                mcp_config=mcp_config,
            ),
            f"Call mcp__synthetic__{tool} exactly once, wait for it to finish, and "
            "reply with its request_id and status. Do not call another tool.",
        )
        report["runs"].append(run)
        names = [item.get("name") for item in _tool_uses(run["events"])]
        text = _final_text(run["events"])
        report["evidence"] = {
            "tools": names,
            "result": text,
            "elapsed_seconds": run["elapsed_seconds"],
        }
        if f"mcp__synthetic__{tool}" in names and "synthetic-request-001" in text:
            if name == "mcp_prepare" or run["elapsed_seconds"] >= 90:
                report["verdict"] = "supported"

    return report


def assert_claude_conformance(report: dict[str, Any]) -> None:
    """Assert the observed Claude 2.1.284 compatibility contract."""

    name = report["probe"]
    assert name in PROBES
    assert "Claude Code" in report["version"]
    assert report["model"] == MODEL
    for run in report["runs"]:
        assert run["returncode"] == 0
        assert 0 < run["elapsed_seconds"] < PROBE_TIMEOUT_SECONDS
        assert run["stderr"] == ""
        serialized = json.dumps(run["command"])
        assert "api_key" not in serialized.lower()
        assert "oauth" not in serialized.lower()

    evidence = report["evidence"]
    if name == "command":
        assert report["verdict"] == "supported"
        assert evidence["requested_model"] == "sonnet"
        assert any("haiku" in model for model in evidence["command_models"])
        assert "SYNTHETIC_COMMAND_ARGUMENTS=alpha beta" in evidence["result"]
    elif name == "ask_user_question":
        assert report["verdict"] == "supported"
        assert evidence["control_request"] is True
        assert evidence["options"] == ["Alpha", "Beta"]
        assert evidence["updatedInput"]["answers"] == {"Choose the synthetic option?": "Alpha"}
        assert evidence["result"] == "CHOICE=Alpha"
    elif name == "delegation":
        assert report["verdict"] == "supported"
        assert "Agent" in evidence["delegation_tools"]
        assert evidence["parent_tool_use_id"] is True
        assert "SYNTHETIC_PROJECT_AGENT_WRITER" in evidence["result"]
    elif name == "todowrite":
        assert report["verdict"] == "unsupported"
        assert "TodoWrite" not in _initialized_tools(report["runs"][0]["events"])
        assert evidence["tools"] == []
    elif name == "connectors":
        assert report["verdict"] == "fallback"
        assert evidence["strict"] == evidence["without_strict"]
        assert evidence["connector_tool_calls"] == []
    elif name == "path_rule":
        assert report["verdict"] == "unsupported"
        assert "SYNTHETIC_PATH_RULE_MARKER" not in evidence["symlink_result"]
        assert "SYNTHETIC_PATH_RULE_MARKER" in evidence["regular_file_control"]
    elif name == "hooks":
        assert report["verdict"] == "fallback"
        assert evidence["project_sources"] == ["project-hook"]
        assert set(evidence["user_and_project_sources"]) == {
            "project-hook",
            "global-auto-update-hook",
        }
    elif name == "mcp_prepare":
        assert report["verdict"] == "supported"
        assert "mcp__synthetic__prepare" in evidence["tools"]
        assert "synthetic-request-001" in evidence["result"]
        assert evidence["elapsed_seconds"] < 90
    elif name == "mcp_block":
        assert report["verdict"] == "supported"
        assert "mcp__synthetic__block" in evidence["tools"]
        assert "synthetic-request-001" in evidence["result"]
        assert evidence["elapsed_seconds"] >= 90


def run_claude_probe(name: str) -> dict[str, Any]:
    """Run one named paid probe after the caller explicitly opts in."""

    if os.environ.get("KEEPHARNESS_LIVE") != "1":
        raise RuntimeError("Set KEEPHARNESS_LIVE=1 to run provider probes")
    if name not in PROBES:
        raise ValueError(name)
    with isolated_fixture(copy_claude_auth=True) as fixture:

        async def bounded() -> dict[str, Any]:
            try:
                async with asyncio.timeout(PROBE_TIMEOUT_SECONDS - 10):
                    return await _run(fixture, name)
            except TimeoutError:
                return {
                    "probe": name,
                    "version": "unknown (probe timed out)",
                    "model": MODEL,
                    "runs": [],
                    "verdict": "blocked",
                    "evidence": {},
                    "blocker": "Named probe exceeded its 590 second execution budget",
                }

        return asyncio.run(bounded())


if __name__ == "__main__":
    print(json.dumps(run_claude_probe(sys.argv[1]), indent=2))
