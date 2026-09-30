"""Only execution-owned MCP capabilities can prepare effects; they cannot approve."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent_service.effect_mcp import prepare_request
from agent_service.effect_transport import effect_transport, transport_support


@pytest.mark.parametrize(
    "backend,mode,supported",
    [
        ("codex", "native", True),
        ("claude", "native", True),
        ("codex", "scoped", True),
        ("claude", "scoped", True),
        ("gemini", "native", False),
        ("local", "native", False),
        ("deepseek", "native", False),
        ("codex", "other", False),
    ],
)
def test_supported_modes(backend, mode, supported):
    assert transport_support(backend, mode)["supported"] is supported


def test_prepare_returns_without_human_wait_and_capability_expires(tmp_path):
    async def scenario():
        prepare = AsyncMock(return_value={"request_id": "effect-1", "status": "prepared"})
        service = SimpleNamespace(
            effects=SimpleNamespace(prepare=prepare),
            config={"effect_integrations": [{}]},
            root=tmp_path,
        )
        async with effect_transport(service, "job-1", "codex", "native") as config:
            result = await asyncio.wait_for(
                prepare_request(config, {"integration": "synthetic"}), 0.5
            )
            assert result["request_id"] == "effect-1"
            assert prepare.call_args.args == ("job-1", {"integration": "synthetic"})
            assert prepare.call_args.kwargs["enforcement"] == "unenforced"
            assert Path(config["socket"]).stat().st_mode & 0o777 == 0o600
            bad = {**config, "token": "different-run"}
            assert (await prepare_request(bad, {}))["error"] == "effect_capability_denied"
            reader, writer = await asyncio.open_unix_connection(config["socket"])
            writer.write(
                json.dumps({"token": config["token"], "method": "approve", "request": {}}).encode()
                + b"\n"
            )
            await writer.drain()
            assert json.loads(await reader.readline())["error"] == "effect_method_denied"
            writer.close()
            await writer.wait_closed()
        assert not Path(config["socket"]).exists()
        with pytest.raises(OSError):
            await prepare_request(config, {})

    asyncio.run(scenario())


def test_unsupported_mode_has_no_capability(tmp_path):
    async def scenario():
        service = SimpleNamespace(config={"effect_integrations": [{}]}, root=tmp_path)
        async with effect_transport(service, "job-1", "gemini", "native") as config:
            assert config is None

    asyncio.run(scenario())


def test_native_configs_use_owned_server_and_disable_host_impersonation(tmp_path, monkeypatch):
    from adapters.claude.native import build_command
    from adapters.codex.native import RuntimeOptions, thread_parameters
    from agent_service.effect_transport import server_spec

    capability = {"socket": "/synthetic/prepare.sock", "token": "synthetic-prepare-only"}
    owned = server_spec(capability)
    host = {
        "harness_effects": {"command": "host-should-not-run"},
        "external": {"command": "fixture"},
    }
    monkeypatch.setattr("adapters.claude.native.configurations", lambda: {"claude": host})
    monkeypatch.setattr("adapters.claude.native.inventory", lambda: {"claude": []})
    config = {"binary": "fixture", "_effect_capability": capability}
    build_command(config, "fixture", tmp_path, {}, ["mcp:harness_effects"], "full", [])
    assert json.loads((tmp_path / "mcp.json").read_text())["mcpServers"]["harness_effects"] == owned
    build_command(
        {"binary": "fixture"}, "fixture", tmp_path, {}, ["mcp:harness_effects"], "full", []
    )
    assert "harness_effects" not in json.loads((tmp_path / "mcp.json").read_text())["mcpServers"]
    monkeypatch.setattr("adapters.codex.native.configurations", lambda: {"codex": host})
    workspace = SimpleNamespace(cwd=tmp_path, permissions={})
    params = thread_parameters(
        {**config, "plugin_inventory": []}, {}, "fixture", workspace, RuntimeOptions([]), False
    )
    assert params["config"]["mcp_servers"]["harness_effects"] == {**owned, "enabled": True}
    params = thread_parameters(
        {"plugin_inventory": [], "integrations": ["mcp:harness_effects"]},
        {},
        "fixture",
        workspace,
        RuntimeOptions([]),
        False,
    )
    assert params["config"]["mcp_servers"]["harness_effects"]["enabled"] is False


def test_scoped_bind_cannot_expose_private_store(tmp_path):
    from agent_service.effect_transport import scoped_enforcement

    state = tmp_path / "state"
    service = SimpleNamespace(root=state, config={})
    config = {"python": "/synthetic/venv/bin/python", "binary": "/synthetic/codex"}
    assert (
        scoped_enforcement(
            service, {"root": str(tmp_path / "project")}, config, state / "sessions" / "job"
        )
        == "mediated"
    )
    for root in [tmp_path, state, Path("/")]:
        assert (
            scoped_enforcement(service, {"root": str(root)}, config, state / "sessions" / "job")
            == "unenforced"
        )
    alias = tmp_path / "alias"
    alias.symlink_to(state, target_is_directory=True)
    assert (
        scoped_enforcement(service, {"additional_roots": [str(alias)]}, config, None)
        == "unenforced"
    )


def test_real_stdio_mcp_exposes_prepare_only(tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    from agent_service.effect_transport import server_spec

    async def scenario():
        service = SimpleNamespace(
            effects=SimpleNamespace(
                prepare=AsyncMock(return_value={"request_id": "effect-stdio", "status": "prepared"})
            ),
            config={"effect_integrations": [{}]},
            root=tmp_path,
        )
        async with effect_transport(service, "job-stdio", "claude", "native") as capability:
            spec = server_spec(capability)
            async with stdio_client(StdioServerParameters(**spec)) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    assert [tool.name for tool in tools.tools] == ["prepare"]
                    result = await asyncio.wait_for(
                        session.call_tool(
                            "prepare",
                            {
                                "integration": "fixture",
                                "operation": "jira.create_issue",
                                "destination": "TEST",
                                "arguments": {},
                                "artifact": {"fields": {}},
                            },
                        ),
                        2,
                    )
                    assert not result.isError
                    assert "effect-stdio" in result.content[0].text

    asyncio.run(scenario())


def test_scoped_mcp_is_registered_in_temporary_config_only(tmp_path):
    from adapters.shared.scoped import prepare_scoped

    binary = tmp_path / "fixture"
    binary.write_text("synthetic")
    auth = tmp_path / "auth.json"
    auth.write_text("{}")
    socket = tmp_path / "effect.sock"
    socket.touch()
    config = {
        "binary": str(binary),
        "auth_file": str(auth),
        "python": "/synthetic/venv/bin/python",
        "_effect_capability": {"socket": str(socket), "token": "prepare-capability"},
    }
    with prepare_scoped(config, {}, {}, None, "codex", "auth.json") as workspace:
        import tomllib

        temporary = tomllib.loads((workspace.home / "config.toml").read_text())
        server = temporary["mcp_servers"]["harness_effects"]
        assert server["command"] == "/venv/bin/python"
        assert server["args"][0] == "/bridge/effect_mcp.py"
        assert json.loads(server["args"][1])["socket"] == "/bridge/effect.sock"
        index = workspace.command.index(str(socket))
        assert workspace.command[index - 1 : index + 2] == [
            "--ro-bind",
            str(socket),
            "/bridge/effect.sock",
        ]
        assert "--clearenv" in workspace.command
        assert (workspace.bridge / "effect_mcp.py").is_file()
    assert auth.read_text() == "{}"


@pytest.mark.host_tools("bwrap")
def test_scoped_process_can_prepare_without_reading_harness_store(tmp_path):
    """Exercise the actual filesystem boundary with synthetic credentials only."""
    from adapters.shared.scoped import prepare_scoped

    state = tmp_path / "state"
    state.mkdir()
    private = state / "harness.effect_credentials.json"
    private.write_text("synthetic-private-value")
    auth = tmp_path / "provider-auth.json"
    auth.write_text("{}")

    async def scenario():
        service = SimpleNamespace(
            effects=SimpleNamespace(
                prepare=AsyncMock(return_value={"request_id": "sandbox-effect"})
            ),
            config={"effect_integrations": [{}]},
            root=state,
        )
        async with effect_transport(service, "job-scoped", "codex", "scoped") as capability:
            config = {
                "binary": "/usr/bin/python3",
                "auth_file": str(auth),
                "python": "/usr/bin/python3",
                "_effect_capability": capability,
            }
            with prepare_scoped(config, {}, {}, None, "codex", "auth.json") as workspace:
                script = (
                    "import socket,json,pathlib; assert not pathlib.Path("
                    + repr(str(private))
                    + ").exists(); s=socket.socket(socket.AF_UNIX); s.connect('/bridge/effect.sock'); s.sendall((json.dumps({'token':"
                    + repr(capability["token"])
                    + ",'method':'prepare','request':{}})+'\\n').encode()); print(s.recv(8192).decode())"
                )
                process = await asyncio.create_subprocess_exec(
                    *workspace.command,
                    "--",
                    "/codex-cli",
                    "-c",
                    script,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                out, err = await asyncio.wait_for(process.communicate(), 5)
                assert process.returncode == 0, err.decode()
                assert json.loads(out)["request_id"] == "sandbox-effect"

    asyncio.run(scenario())


def test_execution_end_closes_incomplete_capability_clients(tmp_path):
    async def scenario():
        service = SimpleNamespace(config={"effect_integrations": [{}]}, root=tmp_path)
        async with effect_transport(service, "job", "codex", "native") as capability:
            reader, writer = await asyncio.open_unix_connection(capability["socket"])
            writer.write(b"{")
            await writer.drain()
            await asyncio.sleep(0.01)
        try:
            assert await asyncio.wait_for(reader.read(), 0.1) == b""
        finally:
            writer.close()
            await writer.wait_closed()

    asyncio.run(scenario())


def test_scoped_isolation_checks_actual_venv_bind_and_auth_copy(tmp_path):
    from agent_service.effect_transport import scoped_enforcement

    runtime = tmp_path / "runtime"
    (runtime / "bin").mkdir(parents=True)
    (runtime / "bin" / "python").symlink_to("/usr/bin/python3")
    service = SimpleNamespace(
        root=tmp_path / "state",
        config={"effect_credentials_path": str(runtime / "credentials.json")},
    )
    config = {
        "python": str(runtime / "bin" / "python"),
        "binary": "/usr/bin/false",
        "auth_file": str(tmp_path / "provider.json"),
    }
    assert scoped_enforcement(service, {}, config, None) == "unenforced"
    service.config = {}
    config["auth_file"] = str(service.root / "harness.effect_credentials.json")
    assert scoped_enforcement(service, {}, config, None) == "unenforced"


def test_valid_large_prepare_response_is_not_lost(tmp_path):
    async def scenario():
        result = {"request_id": "large", "artifact_preview": "x" * 90000}
        service = SimpleNamespace(
            effects=SimpleNamespace(prepare=AsyncMock(return_value=result)),
            config={"effect_integrations": [{}]},
            root=tmp_path,
        )
        async with effect_transport(service, "large-job", "codex", "native") as capability:
            assert await prepare_request(capability, {}) == result

    asyncio.run(scenario())
