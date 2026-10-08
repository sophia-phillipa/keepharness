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
        ("codex", "scoped", False),
        ("claude", "scoped", False),
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

    capability = {
        "socket": "/synthetic/prepare.sock",
        "token": "synthetic-prepare-only",
        "config_file": "/synthetic/capability.json",
        "server_name": "harness_effects_fixture",
    }
    owned = server_spec(capability)
    host = {
        "harness_effects": {"command": "host-should-not-run"},
        "external": {"command": "fixture"},
    }
    monkeypatch.setattr("control.integrations.configurations", lambda: {"claude": host})
    monkeypatch.setattr("control.integrations.inventory", lambda: {"claude": []})
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
    assert params["config"]["mcp_servers"]["harness_effects_fixture"] == {**owned, "enabled": True}
    params = thread_parameters(
        {"plugin_inventory": [], "integrations": ["mcp:harness_effects"], "personal_setup": True},
        {},
        "fixture",
        workspace,
        RuntimeOptions([]),
        False,
    )
    assert "harness_effects" not in params["config"]["mcp_servers"]
    import os

    from adapters.codex.native import build_command as codex_command
    home = Path(os.environ["CODEX_HOME"])
    (home / "config.toml").write_text('[mcp_servers.harness_effects]\ncommand="host-should-not-run"\n')
    assert "mcp_servers.harness_effects.enabled=false" in codex_command("fixture", {})


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


def test_codex_startup_disables_reserved_host_entries_from_actual_home(tmp_path, monkeypatch):
    from adapters.codex.native import build_command

    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    assert not any("harness_effects" in arg for arg in build_command("fixture", {}))
    (tmp_path / "config.toml").write_text(
        '[mcp_servers.harness_effects]\nurl="http://127.0.0.1:9"\n'
    )
    assert "mcp_servers.harness_effects.enabled=false" in build_command("fixture", {})


def test_isolated_command_does_not_import_host_reserved_entries(tmp_path, monkeypatch):
    from adapters.codex.native import build_command

    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    (tmp_path / "config.toml").write_text('[mcp_servers.harness_effects]\ncommand="host"\n')
    assert not any(
        "harness_effects" in arg for arg in build_command("fixture", {}, host_config=False)
    )


def test_capability_token_is_file_only_and_expires(tmp_path):
    import stat

    from agent_service.effect_transport import server_spec

    async def scenario():
        service = SimpleNamespace(config={"effect_integrations": [{}]}, root=tmp_path)
        async with effect_transport(service, "file-only", "codex", "native") as capability:
            spec = server_spec(capability)
            assert bool(capability["token"] not in json.dumps(spec))
            path = Path(spec["args"][1])
            assert stat.S_IMODE(path.stat().st_mode) == 0o600
            assert json.loads(path.read_text()) == {
                "socket": capability["socket"],
                "token": capability["token"],
            }
        assert not path.exists()

    asyncio.run(scenario())


def test_codex_startup_argv_has_no_capability_token(tmp_path):
    from unittest.mock import patch

    from adapters.codex import backend

    async def scenario():
        service = SimpleNamespace(config={"effect_integrations": [{}]}, root=tmp_path)
        async with effect_transport(service, "argv", "codex", "native") as capability:
            config = {"binary": "fixture", "_effect_capability": capability}
            with patch.object(backend, "run_turn", new_callable=AsyncMock) as run_turn:
                await backend.run_native(
                    config, "fixture", lambda *_: None, {}, None, "low", tmp_path, None
                )
            command = run_turn.call_args.args[8].command
            assert bool(capability["token"] not in json.dumps(command))
            assert capability["config_file"] in json.dumps(command)

    asyncio.run(scenario())
