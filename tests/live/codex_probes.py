"""Opt-in Codex app-server probes using only the synthetic conformance fixture."""

import asyncio
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

MODEL = "gpt-6-astra"
EFFORT = "high"
PROBES = (
    "skills",
    "question",
    "question_plan",
    "question_enabled",
    "agents",
    "prompt",
    "mcp",
    "mcp_timeout",
    "mcp_turn_block",
    "agents_registered",
    "agents_trusted",
    "agents_symlink_trusted",
)


class ProbeRPC:
    """Keep protocol evidence, including notifications interleaved with replies."""

    def __init__(self, process):
        self.process = process
        self.sequence = 0
        self.events = []
        self.started = time.monotonic()

    async def send(self, value):
        self.events.append(
            {"direction": "send", "seconds": round(time.monotonic() - self.started, 3), **value}
        )
        self.process.stdin.write((json.dumps(value) + "\n").encode())
        await self.process.stdin.drain()

    async def receive(self):
        line = await self.process.stdout.readline()
        if not line:
            raise RuntimeError("app-server closed stdout")
        value = json.loads(line)
        self.events.append(
            {"direction": "receive", "seconds": round(time.monotonic() - self.started, 3), **value}
        )
        if "method" in value and "id" in value:
            if value["method"] == "item/tool/requestUserInput":
                answers = {
                    q["id"]: {"answers": [q["options"][0]["label"]]}
                    for q in value["params"]["questions"]
                }
                await self.send({"id": value["id"], "result": {"answers": answers}})
            else:
                await self.send(
                    {
                        "id": value["id"],
                        "error": {"code": -32601, "message": "Unsupported probe interaction"},
                    }
                )
        return value

    async def call(self, method, params=None):
        self.sequence += 1
        request_id = self.sequence
        await self.send({"id": request_id, "method": method, "params": params or {}})
        while True:
            item = await self.receive()
            if item.get("id") == request_id and "method" not in item:
                if "error" in item:
                    raise RuntimeError(json.dumps(item["error"]))
                return item.get("result", {})

    async def turn(self, thread_id, text, inputs=(), mode=None):
        params = {
            "threadId": thread_id,
            "model": MODEL,
            "effort": EFFORT,
            "input": [{"type": "text", "text": text}, *inputs],
        }
        if mode:
            params["collaborationMode"] = {
                "mode": mode,
                "settings": {
                    "model": MODEL,
                    "reasoning_effort": EFFORT,
                    "developer_instructions": None,
                },
            }
        await self.call("turn/start", params)
        while True:
            item = await self.receive()
            if item.get("method") == "turn/completed" and item["params"]["threadId"] == thread_id:
                return item["params"]["turn"]


async def _run(fixture, name):
    budget_started = time.monotonic()
    binary = shutil.which("codex")
    if not binary:
        raise RuntimeError("codex is not installed")
    env = fixture.env
    project = fixture.project
    command = [binary, "app-server", "--listen", "stdio://"]
    settings = {
        "features.hooks": "false",
        "features.apps": "false",
        "features.shell_tool": "false",
        "features.unified_exec": "false",
        "web_search": '"disabled"',
        "check_for_update_on_startup": "false",
        "model": json.dumps(MODEL),
        "model_reasoning_effort": json.dumps(EFFORT),
    }
    if name in ("agents_trusted", "agents_symlink_trusted"):
        subprocess.run(["git", "init", "--quiet", str(project)], env=env, check=True, timeout=10)
        (fixture.codex_home / "config.toml").write_text(
            f'[projects.{json.dumps(str(project))}]\ntrust_level = "trusted"\n'
        )
    if name == "agents_trusted":
        for path in (
            project / ".codex/agents/demo--writer.toml",
            fixture.codex_home / "agents/demo--reviewer.toml",
        ):
            content = path.read_bytes()
            path.unlink()
            path.write_bytes(content)
    if name == "question_enabled":
        settings["features.default_mode_request_user_input"] = "true"
    if name == "agents_registered":
        for role, path in (
            ("demo--writer", project / ".codex/agents/demo--writer.toml"),
            ("demo--reviewer", fixture.codex_home / "agents/demo--reviewer.toml"),
        ):
            settings[f"agents.{role}.description"] = json.dumps("Synthetic marker-only probe role")
            settings[f"agents.{role}.config_file"] = json.dumps(str(path))
    if name.startswith("mcp"):
        settings.update(
            {
                "mcp_servers.synthetic.command": json.dumps(sys.executable),
                "mcp_servers.synthetic.args": json.dumps(
                    [str(Path(__file__).with_name("probe_mcp_server.py"))]
                ),
            }
        )
        if name == "mcp_timeout":
            settings["mcp_servers.synthetic.tool_timeout_sec"] = "2"
    for key, value in settings.items():
        command.extend(["-c", key + "=" + value])
    version = subprocess.check_output(
        [binary, "--version"], env=env, text=True, stderr=subprocess.DEVNULL, timeout=15
    ).strip()
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=project,
        env=env,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        start_new_session=True,
        limit=8 * 1024 * 1024,
    )
    rpc = ProbeRPC(process)
    report = {
        "probe": name,
        "version": version,
        "command": command,
        "model": MODEL,
        "effort": EFFORT,
    }
    try:
        async with asyncio.timeout(max(0, 590 - (time.monotonic() - budget_started))):
            await rpc.call(
                "initialize",
                {
                    "clientInfo": {"name": "synthetic-conformance", "version": "1.0"},
                    "capabilities": {"experimentalApi": True},
                },
            )
            await rpc.send({"method": "initialized", "params": {}})
            if name == "skills":
                link = fixture.codex_home / "skills" / "demo-native"
                target = (link / "SKILL.md").resolve().parent
                if link.is_symlink():
                    link.unlink()
                    link.mkdir()
                    (link / "SKILL.md").symlink_to(target / "SKILL.md")
                assert (link / "SKILL.md").is_symlink()
                report["file_link_skills"] = await rpc.call(
                    "skills/list", {"cwds": [str(project)], "forceReload": True}
                )
                (link / "SKILL.md").unlink()
                link.rmdir()
                link.symlink_to(target, target_is_directory=True)
                report["skills"] = await rpc.call(
                    "skills/list", {"cwds": [str(project)], "forceReload": True}
                )
                report["models"] = await rpc.call(
                    "model/list", {"includeHidden": False, "limit": 100}
                )
            thread = await rpc.call(
                "thread/start",
                {
                    "model": MODEL,
                    "cwd": str(project),
                    "approvalPolicy": "never",
                    "sandbox": "read-only",
                    "ephemeral": True,
                    "experimentalRawEvents": True,
                    "developerInstructions": "This is a tiny synthetic compatibility test. Do only the requested probe. Do not access network, shell, real home directories, or unrelated files. Keep answers to one line.",
                },
            )
            thread_id = thread["thread"]["id"]
            if name.startswith("mcp"):
                report["inventory"] = await rpc.call(
                    "mcpServerStatus/list", {"threadId": thread_id}
                )
                report["calls"] = []
                for tool in () if name == "mcp_turn_block" else ("prepare", "block"):
                    started = time.monotonic()
                    try:
                        result = await rpc.call(
                            "mcpServer/tool/call",
                            {
                                "threadId": thread_id,
                                "server": "synthetic",
                                "tool": tool,
                                "arguments": {},
                            },
                        )
                        report["calls"].append(
                            {
                                "tool": tool,
                                "elapsed_seconds": round(time.monotonic() - started, 3),
                                "result": result,
                            }
                        )
                    except RuntimeError as exc:
                        report["calls"].append(
                            {
                                "tool": tool,
                                "elapsed_seconds": round(time.monotonic() - started, 3),
                                "error": str(exc),
                            }
                        )
                # Exercise model-visible dispatch too, without another blocking call.
                prompt = "Call the synthetic MCP prepare tool exactly once, then report its request_id. Do not call block or any other tool."
                if name == "mcp_turn_block":
                    prompt = "Call synthetic MCP prepare exactly once, then synthetic MCP block exactly once (it takes 95 seconds; wait). Report each result or error accurately. Do not retry."
                if name != "mcp_timeout":
                    report["turn"] = await rpc.turn(thread_id, prompt)
            elif name == "skills":
                skills = [
                    s
                    for group in report["skills"].get("data", [])
                    for s in group.get("skills", [])
                    if s["name"] == "demo-native"
                ]
                if not skills:
                    raise RuntimeError("Synthetic symlinked skill not discovered")
                report["turn"] = await rpc.turn(
                    thread_id,
                    "$demo-native Follow the selected skill and reply with its marker only.",
                    [{"type": "skill", "name": "demo-native", "path": skills[0]["path"]}],
                )
            elif name in ("question", "question_plan", "question_enabled"):
                report["turn"] = await rpc.turn(
                    thread_id,
                    "Use request_user_input now to ask which synthetic color to choose. Question id color, header Color, options Blue (description First synthetic color) and Green (description Second synthetic color). Do not decide yourself. After the answer, reply CHOICE=<selected label>.",
                    mode="plan" if name == "question_plan" else None,
                )
            elif name in (
                "agents",
                "agents_registered",
                "agents_trusted",
                "agents_symlink_trusted",
            ):
                report["turn"] = await rpc.turn(
                    thread_id,
                    "Delegate a tiny marker-only task to agent type demo--writer and then demo--reviewer, exactly one call each. Use gpt-6-astra with high effort for both. Attempt each available role independently even if the other is unavailable. Wait for any spawned agents and report their markers. Do not delegate to any other agent type.",
                )
            elif name == "prompt":
                report["turn"] = await rpc.turn(thread_id, "/prompts:synthetic-probe alpha beta")
    except (RuntimeError, TimeoutError) as exc:
        report["error"] = str(exc) or "probe reached its ten-minute budget (cleanup reserved)"
    finally:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(process.wait(), 5)
        except asyncio.TimeoutError:
            os.killpg(process.pid, signal.SIGKILL)
            await process.wait()
        report["events"] = rpc.events
        report["elapsed_seconds"] = round(time.monotonic() - rpc.started, 3)
    return report


def run_codex_probe(name):
    if os.environ.get("TAIL_HARNESS_LIVE") != "1":
        raise RuntimeError("Set TAIL_HARNESS_LIVE=1 to run provider probes")
    if name not in PROBES:
        raise ValueError(name)
    from conformance import isolated_fixture

    with isolated_fixture(copy_codex_auth=True) as fixture:
        return asyncio.run(_run(fixture, name))


def assert_codex_conformance(report):
    """Assert the observed September contract; changed capabilities need review."""
    assert not report.get("error"), report.get("error")
    assert report["model"] == MODEL
    assert report["effort"] == EFFORT
    name = report["probe"]
    events = report["events"]
    received = [e for e in events if e["direction"] == "receive"]
    questions = [e for e in received if e.get("method") == "item/tool/requestUserInput"]
    final = " ".join(i.get("text", "") for i in report.get("turn", {}).get("items", []))
    if "turn" in report:
        assert report["turn"]["status"] == "completed", report["turn"].get("error")
    if name == "skills":
        before = [s for g in report["file_link_skills"]["data"] for s in g["skills"]]
        after = [s for g in report["skills"]["data"] for s in g["skills"]]
        assert not any(s["name"] == "demo-native" for s in before)
        assert any(s["name"] == "demo-native" and s["enabled"] for s in after)
        assert final == "SYNTHETIC_NATIVE_SKILL_MARKER"
    elif name in ("question_plan", "question_enabled"):
        assert len(questions) == 1
        question = questions[0]["params"]["questions"][0]
        assert question["id"] == "color"
        assert [o["label"] for o in question["options"]] == ["Blue", "Green"]
        assert all(o["description"] for o in question["options"])
        assert "CHOICE=Blue" in final
        assert any(
            e.get("result", {}).get("answers") == {"color": {"answers": ["Blue"]}} for e in events
        )
    elif name == "question":
        assert not questions, "Default-mode option questions became available; review compatibility"
        assert "CHOICE=Blue" not in final
    elif name == "prompt":
        assert "SYNTHETIC_COMMAND_ARGUMENTS=" not in final
        assert any(
            e.get("params", {}).get("item", {}).get("type") == "userMessage"
            and e["params"]["item"]["content"][0].get("text")
            == "/prompts:synthetic-probe alpha beta"
            for e in received
        )
    elif name.startswith("agents"):
        outputs = [
            e.get("params", {}).get("item", {}).get("output", "")
            for e in received
            if e.get("method") == "rawResponseItem/completed"
            and e.get("params", {}).get("item", {}).get("type") == "function_call_output"
        ]
        spawns = [
            json.loads(e["params"]["item"]["arguments"])
            for e in received
            if e.get("method") == "rawResponseItem/completed"
            and e.get("params", {}).get("item", {}).get("type") == "function_call"
            and e["params"]["item"].get("name") == "spawn_agent"
        ]
        assert {s["agent_type"] for s in spawns} == {"demo--writer", "demo--reviewer"}
        assert all(s.get("model") == MODEL and s.get("reasoning_effort") == EFFORT for s in spawns)
        if name == "agents_trusted":
            assert "SYNTHETIC_CODEX_PROJECT_AGENT_WRITER" in final
            assert "SYNTHETIC_CODEX_GLOBAL_AGENT_REVIEWER" in final
            assert sum('"task_name"' in v for v in outputs) >= 2
        else:
            assert sum("agent type is currently not available" in v for v in outputs) >= 2
    elif name in ("mcp", "mcp_timeout"):
        prepare, block = report["calls"]
        assert prepare["tool"] == "prepare" and prepare["elapsed_seconds"] < 10
        assert "synthetic-request-001" in json.dumps(prepare["result"])
        if name == "mcp_timeout":
            assert "timed out awaiting tools/call after 2s" in block["error"]
            assert 1.5 <= block["elapsed_seconds"] < 15
        else:
            assert block["elapsed_seconds"] >= 90 and not block.get("error")
            assert block["result"]["isError"] is False
    elif name == "mcp_turn_block":
        calls = [
            e["params"]["item"]
            for e in received
            if e.get("method") == "item/completed"
            and e["params"]["item"].get("type") == "mcpToolCall"
        ]
        assert [i["tool"] for i in calls] == ["prepare", "block"]
        assert all(i["status"] == "completed" and not i["error"] for i in calls)
        assert calls[1]["durationMs"] >= 90000


if __name__ == "__main__":
    print(json.dumps(run_codex_probe(sys.argv[1]), indent=2))
