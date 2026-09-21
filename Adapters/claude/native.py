"""Claude stream-json sessions, tool approvals and process lifetime."""

import asyncio
import json
from control.integrations import configurations, inventory
from .stream import Stream
from .auth import cli_login_environment


def build_command(
    config, model, home, permissions, selected, access_mode, additional_roots
):
    """Configure only selected tools and connectors for this Claude process."""
    servers = configurations()["claude"]
    mcp = home / "mcp.json"
    mcp.write_text(
        json.dumps(
            {"mcpServers": {k: v for k, v in servers.items() if "mcp:" + k in selected}}
        )
    )
    mcp.chmod(0o600)
    plugins = {
        p["id"].split(":", 1)[1]: p["id"] in selected
        for p in inventory()["claude"]
        if p["kind"] == "plugin"
    }
    tools = ["Read", "Glob", "Grep"] if permissions.get("read") else []
    if permissions.get("read") and config.get("resource_skills"):
        tools += ["Skill"]
    if permissions.get("write"):
        tools += ["Edit", "Write", "NotebookEdit"]
    if permissions.get("shell"):
        tools += ["Bash"]
    if permissions.get("internet"):
        tools += ["WebFetch", "WebSearch"]
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
        "--model",
        model,
        "--tools",
        ",".join(tools),
        "--strict-mcp-config",
        "--mcp-config",
        str(mcp),
        "--settings",
        json.dumps(
            {
                "enabledPlugins": plugins,
                "disableAllHooks": not permissions.get("hooks", False),
                **(
                    {"sandbox": {"enabled": False}}
                    if config.get("unrestricted")
                    else {}
                ),
            }
        ),
    ]
    if (
        config.get("unrestricted")
        and access_mode != "read_only"
        and permissions.get("shell")
    ):
        command += ["--permission-mode", "bypassPermissions"]
    elif access_mode in ("auto", "full", "read_only"):
        command += ["--permission-mode", "dontAsk"]
    if additional_roots:
        command += ["--add-dir", *additional_roots]
    return command


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
):
    command = build_command(
        config, model, home, permissions, selected, access_mode, additional_roots
    )
    marker = home / "claude-session.json"
    if marker.exists():
        command += ["--resume", json.loads(marker.read_text())["id"]]
    if isinstance(title, str) and title.strip():
        command += ["--name", title]
    proc = await asyncio.create_subprocess_exec(
        *command,
        cwd=cwd,
        env=cli_login_environment() if config.get("use_cli_login") else None,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        limit=2 * 1024 * 1024,
    )
    state = Stream(event)
    session = None

    async def send(value):
        proc.stdin.write((json.dumps(value) + "\n").encode())
        await proc.stdin.drain()

    try:
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
            line = await proc.stdout.readline()
            if not line:
                break
            item = json.loads(line)
            if item.get("session_id"):
                session = item["session_id"]
            if item.get("type") == "control_request":
                req = item.get("request", {})
                reply = await approve("claude/" + req.get("subtype", "permission"), req)
                decision = reply.get("approved", False)
                response = (
                    {"behavior": "allow", "updatedInput": req.get("input", {})}
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
        result = state.finish(model)
        if session:
            marker.write_text(json.dumps({"id": session}))
            result["thread_id"] = session
        result["context_strategy"] = "native_session"
        return result
    finally:
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), 5)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
