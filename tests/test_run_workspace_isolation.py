"""Run folders live outside the harness state, and Read only reads only the authorized roots."""

import asyncio
import hashlib
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from adapters.shared.workspace import prepare_workspace
from agent_service.app import Service
from agent_service.private_storage import private_roots
from agent_service.reader_mcp import TOOLS, Reader, server_spec
from agent_service.tools import ToolError
from control import runtime_config


def runtime(state):
    settings = {"services": {}, "uploads_enabled": False, "projects": [], "port": 8095}
    return runtime_config.base_config(settings, state, 8094, "http://127.0.0.1:8095/", {})


def test_runtime_config_keeps_run_folders_outside_the_state(tmp_path):
    state = tmp_path / "keepharness"
    sessions = Path(runtime(state)["sessions_dir"])
    assert sessions.is_absolute()
    assert state != sessions and state not in sessions.parents
    assert sessions.parent == state.parent


def test_run_cwd_outside_state_and_reads_scoped(tmp_path):
    state = tmp_path / "keepharness"
    (state / "runs").mkdir(parents=True)
    (state / "vpn.key").write_text("fixture-secret")
    config = {
        **runtime(state),
        "projects": {"sem-projeto": {"label": "No project"}},
        "clients": {
            "local": {"sha256": hashlib.sha256(b"l").hexdigest(), "projects": ["sem-projeto"]}
        },
        "services": {
            "codex": {
                "enabled": True,
                "mode": "native",
                "models": ["gpt-6-astra"],
                "projects": ["sem-projeto"],
                "permissions": {"read": True},
            }
        },
        "codex": {"binary": "fixture"},
        "codex_models": {"gpt-6-astra": ["low"]},
        "origins": [],
    }
    service = Service(config)
    identity = ("local", config["clients"]["local"])
    data = {
        "project_id": "sem-projeto",
        "backend": "codex",
        "model": "gpt-6-astra",
        "effort": "low",
        "prompt": "fixture",
        "access_mode": "read_only",
    }
    seen = {}

    async def native(config, prompt, event, project, model, effort, session_dir, *rest):
        seen["cwd"] = prepare_workspace(project, prompt, session_dir).cwd
        seen["session"] = Path(session_dir)
        return {"answer": "fixture"}

    try:
        job = service.submit(identity, data)["job_id"]
        with (
            patch("adapters.run_native", side_effect=native),
            patch.object(service, "quota", AsyncMock(return_value={})),
        ):
            asyncio.run(service.infer(service.job(identity, job), data))
    finally:
        service.db.close()
    for folder in (seen["cwd"], seen["session"]):
        # No run folder has the key folder (or the run database folder) among its parents.
        assert state not in folder.parents and folder != state
        assert folder.is_relative_to(Path(config["sessions_dir"]))
    # The relocated run folders stay private storage: never registrable as a project.
    assert Path(config["sessions_dir"]) in private_roots(config, config["state_dir"])


@pytest.fixture
def roots(tmp_path):
    project = tmp_path / "project"
    (project / "src").mkdir(parents=True)
    (project / "src" / "main.py").write_text("print('needle')\nsecond line\n")
    (project / ".env").write_text("needle=hidden")
    (project / "server.key").write_text("needle")
    attachments = tmp_path / "attachments"
    attachments.mkdir()
    (attachments / "doc.txt").write_text("attachment needle\n")
    outside = tmp_path / "state"
    outside.mkdir()
    (outside / "vpn.key").write_text("needle")
    (outside / "notes.txt").write_text("needle")
    (project / "link.txt").symlink_to(outside / "notes.txt")
    (project / "linked").symlink_to(outside, target_is_directory=True)
    return project, attachments, outside


def test_reader_reads_inside_the_authorized_roots(roots):
    project, attachments, _ = roots
    reader = Reader([str(project), str(attachments)])
    assert reader.read_file("src/main.py")["lines"][0] == {"line": 1, "text": "print('needle')"}
    assert reader.read_file(str(project / "src" / "main.py"), 2, 2)["lines"] == [
        {"line": 2, "text": "second line"}
    ]
    assert reader.read_file(str(attachments / "doc.txt"))["lines"][0]["text"] == "attachment needle"
    listed = {entry["name"] for entry in reader.list_directory("")["entries"]}
    assert listed == {"src"}
    matches = {(Path(match["path"]).name, match["line"]) for match in reader.search_files("needle")}
    assert matches == {("main.py", 1), ("doc.txt", 1)}


@pytest.mark.parametrize(
    "path",
    [
        "../state/vpn.key",
        "src/../../state/notes.txt",
        "link.txt",
        "linked/notes.txt",
        ".env",
        "server.key",
        "/etc/hostname",
    ],
)
def test_reader_refuses_paths_outside_or_hidden(roots, path):
    project, attachments, _ = roots
    reader = Reader([str(project), str(attachments)])
    with pytest.raises(ToolError):
        reader.read_file(path)


def test_reader_never_reads_or_searches_the_key_folder(roots):
    project, attachments, outside = roots
    reader = Reader([str(project), str(attachments)])
    for path in (str(outside / "vpn.key"), str(outside)):
        with pytest.raises(ToolError):
            reader.read_file(path)
        with pytest.raises(ToolError):
            reader.list_directory(path)
        with pytest.raises(ToolError):
            reader.search_files("needle", path)


def test_reader_server_spec_names_only_read_tools_and_the_given_roots(roots):
    project, attachments, _ = roots
    spec = server_spec([str(project), str(attachments)])
    assert spec["enabled"] is True
    assert spec["default_tools_approval_mode"] == "approve"
    assert spec["enabled_tools"] == list(TOOLS) == ["read_file", "list_directory", "search_files"]
    assert spec["args"][-2:] == [str(project), str(attachments)]


def test_reader_runs_as_a_stdio_server_with_only_the_read_tools(roots):
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    project, attachments, outside = roots
    spec = server_spec([str(project), str(attachments)])

    async def scenario():
        params = StdioServerParameters(command=spec["command"], args=spec["args"], cwd=spec["cwd"])
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            tools = {tool.name: tool for tool in (await session.list_tools()).tools}
            inside = await session.call_tool("read_file", {"path": "src/main.py"})
            escape = await session.call_tool("read_file", {"path": str(outside / "vpn.key")})
            return tools, inside, escape

    tools, inside, escape = asyncio.run(asyncio.wait_for(scenario(), 60))
    assert sorted(tools) == sorted(TOOLS)
    assert all(tool.annotations.readOnlyHint for tool in tools.values())
    assert not inside.isError and "needle" in inside.content[0].text
    assert escape.isError and "needle" not in escape.content[0].text
