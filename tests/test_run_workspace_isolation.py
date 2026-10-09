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


def isolation_config(state):
    (state / "runs").mkdir(parents=True)
    (state / "vpn.key").write_text("fixture-secret")
    return {
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


def run_folders(service, identity):
    """The provider cwd and session folder of one fixture turn served by ``service``."""
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

    job = service.submit(identity, data)["job_id"]
    with (
        patch("adapters.run_native", side_effect=native),
        patch.object(service, "quota", AsyncMock(return_value={})),
    ):
        asyncio.run(service.infer(service.job(identity, job), data))
    return seen["cwd"], seen["session"]


def test_run_cwd_outside_state_and_reads_scoped(tmp_path):
    state = tmp_path / "keepharness"
    config = isolation_config(state)
    service = Service(config)
    identity = ("local", config["clients"]["local"])
    try:
        folders = run_folders(service, identity)
    finally:
        service.db.close()
    for folder in folders:
        # No run folder has the key folder (or the run database folder) among its parents.
        assert state not in folder.parents and folder != state
        assert folder.is_relative_to(Path(config["sessions_dir"]))
    # The relocated run folders stay private storage: never registrable as a project.
    assert Path(config["sessions_dir"]) in private_roots(config, config["state_dir"])


def test_temporary_run_folders_also_stay_outside_the_key_folder(tmp_path):
    state = tmp_path / "keepharness"
    config = isolation_config(state)
    service = Service(config)
    identity = ("local", config["clients"]["local"])

    async def scenario():
        sid = await service.temporary.open(identity)
        session = service.temporary.get(identity, sid)
        folders = await asyncio.to_thread(run_folders, session.service, identity)
        await service.temporary.close(identity, sid)
        return folders

    try:
        folders = asyncio.run(scenario())
    finally:
        service.db.close()
    for folder in folders:
        assert state not in folder.parents and folder != state
        assert folder.is_relative_to(Path(config["sessions_dir"]))
    assert not any((Path(config["sessions_dir"]) / "temporary-chats").iterdir())


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


CREDENTIAL_FILES = [
    ".netrc",
    ".git-credentials",
    ".pgpass",
    ".npmrc",
    ".pypirc",
    "id_ed25519",
    "id_rsa.pub",
    "bundle.p12",
    "vault.kdbx",
    ".docker/config.json",
    ".kube/config",
    ".gnupg/pubring.kbx",
    ".password-store/mail.gpg",
    ".local/share/keyrings/login.txt",
    "Cookies/session.txt",
]


def test_reader_refuses_the_bridge_credential_list(tmp_path):
    project = tmp_path / "project"
    for name in CREDENTIAL_FILES:
        (project / name).parent.mkdir(parents=True, exist_ok=True)
        (project / name).write_text("needle\n")
    (project / "notes.txt").write_text("needle\n")
    reader = Reader([str(project)])
    for name in CREDENTIAL_FILES:
        with pytest.raises(ToolError):
            reader.read_file(name)
    assert [Path(m["path"]).name for m in reader.search_files("needle")] == ["notes.txt"]
    assert [e["name"] for e in reader.list_directory("")["entries"]] == ["notes.txt"]


def test_reader_credential_list_matches_the_standalone_bridge_copy():
    from agent_service import mcp_bridge, workspaces

    assert workspaces.CREDENTIAL_NAMES == mcp_bridge.EXCLUDED_NAMES
    assert workspaces.CREDENTIAL_SUFFIXES == mcp_bridge.EXCLUDED_SUFFIXES
    assert workspaces.PRIVATE_KEY_NAME.pattern == mcp_bridge.PRIVATE_KEY_NAME.pattern


def test_reader_does_not_follow_a_folder_swapped_for_a_link_after_the_checks(roots, monkeypatch):
    """A folder replaced by a link between the name checks and the open must not be followed."""
    from agent_service import tools

    project, _, outside = roots
    (project / "docs").mkdir()
    (project / "docs" / "notes.txt").write_text("inside\n")
    reader = Reader([str(project)])
    original = tools.os.open

    def swap_then_open(path, flags, *args, **kwargs):
        if path == "docs" and not (project / "docs").is_symlink():
            (project / "docs").rename(project / "docs-moved")
            (project / "docs").symlink_to(outside, target_is_directory=True)
        return original(path, flags, *args, **kwargs)

    monkeypatch.setattr(tools.os, "open", swap_then_open)
    with pytest.raises(ToolError):
        reader.read_file("docs/notes.txt")
    monkeypatch.undo()
    with pytest.raises(ToolError):
        reader.read_file("docs/notes.txt")
    assert reader.read_file("docs-moved/notes.txt")["lines"][0]["text"] == "inside"


def test_reader_refuses_a_fifo_without_blocking(roots):
    import os

    project, _, _ = roots
    os.mkfifo(project / "pipe.txt")
    with pytest.raises(ToolError):
        Reader([str(project)]).read_file("pipe.txt")


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
