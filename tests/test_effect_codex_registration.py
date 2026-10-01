"""Installed Codex must replace reserved host MCP tables without model inference."""

import asyncio
import os
import shutil
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from adapters.codex import backend
from adapters.codex.native import build_command
from adapters.codex.rpc import connection
from agent_service.effect_transport import effect_transport


@asynccontextmanager
async def fixture_connection(command, environment):
    try:
        async with connection(command, env=environment) as rpc:
            yield rpc
    except RuntimeError as exc:
        raise AssertionError(getattr(exc, "error_detail", str(exc))) from exc


async def config_read(rpc):
    identifier = await rpc.send("config/read", {"includeLayers": False})
    while True:
        response = await rpc.receive()
        if response.get("id") == identifier:
            assert "error" not in response, response.get("error")
            return response["result"]


@pytest.fixture
def isolated_codex(tmp_path, monkeypatch):
    binary = shutil.which("codex")
    if not binary:
        pytest.skip("Installed Codex is required for the local registration contract")
    home = tmp_path / "home"
    home.mkdir()
    codex_home = tmp_path / "alternate-codex-home"
    codex_home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    environment = {
        "HOME": str(home),
        "CODEX_HOME": str(codex_home),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
    }
    base = """model = "fixture"
model_provider = "fixture"
check_for_update_on_startup = false
[model_providers.fixture]
name = "Synthetic local fixture"
base_url = "http://127.0.0.1:9/v1"
wire_api = "responses"
requires_openai_auth = false
"""
    return binary, codex_home / "config.toml", environment, base


HOST_TABLE = """
[mcp_servers.harness_effects]
url = "http://127.0.0.1:9/untrusted-fixture"
enabled = true
[mcp_servers.harness_effects.http_headers]
X-Fixture = "synthetic-host-marker"
"""


HOST_STDIO_TABLE = """
[mcp_servers.harness_effects]
command = "false"
enabled = true
[mcp_servers.harness_effects.env]
HARNESS_FIXTURE_INHERITED = "synthetic-host-marker"
"""


@pytest.mark.parametrize(
    "host_table",
    ["", HOST_TABLE, HOST_STDIO_TABLE],
    ids=["absent", "alternate-home-http", "alternate-home-stdio"],
)
def test_reserved_server_disabled_without_creating_invalid_transport(isolated_codex, host_table):
    binary, path, environment, base = isolated_codex
    path.write_text(base + host_table)

    async def scenario():
        async with asyncio.timeout(15):
            async with fixture_connection(build_command(binary, {}), environment) as rpc:
                response = await config_read(rpc)
                servers = response["config"].get("mcp_servers", {})
                if host_table:
                    assert servers["harness_effects"]["enabled"] is False
                else:
                    assert "harness_effects" not in servers

    asyncio.run(scenario())


@pytest.mark.parametrize("host_table", [HOST_TABLE, HOST_STDIO_TABLE], ids=["http", "stdio-env"])
def test_owned_server_avoids_host_transport_and_exposes_prepare(
    isolated_codex, tmp_path, host_table
):
    binary, path, environment, base = isolated_codex
    path.write_text(base + host_table)

    async def scenario():
        service = SimpleNamespace(
            config={"effect_integrations": [{}]},
            root=tmp_path / "state",
            effects=SimpleNamespace(
                prepare=AsyncMock(return_value={"request_id": "synthetic-registered"})
            ),
        )
        async with effect_transport(service, "job", "codex", "native") as capability:
            config = {"binary": binary, "_effect_capability": capability}
            with patch.object(backend, "run_turn", AsyncMock(return_value={})) as run:
                await backend.run_native(
                    config,
                    "fixture",
                    lambda *_: None,
                    {},
                    "fixture",
                    "low",
                    tmp_path / "session",
                    None,
                )
            command = run.call_args.args[8].command
            async with asyncio.timeout(20):
                async with fixture_connection(command, environment) as rpc:
                    response = await config_read(rpc)
                    servers = response["config"]["mcp_servers"]
                    assert servers["harness_effects"]["enabled"] is False
                    server = servers[capability["server_name"]]
                    assert server["enabled"] is True
                    assert server.get("url") is None
                    assert not server.get("env")
                    assert not server.get("http_headers")
                    await rpc.call(
                        "thread/start",
                        {
                            "model": "fixture",
                            "cwd": str(tmp_path),
                            "ephemeral": True,
                            "sandbox": "read-only",
                            "approvalPolicy": "never",
                        },
                    )
                    result = await rpc.call("mcpServerStatus/list", {})
                    owned = next(
                        item for item in result["data"] if item["name"] == capability["server_name"]
                    )
                    assert "prepare" in owned["tools"]
                    assert set(owned["tools"]) == {"prepare"}

    asyncio.run(scenario())
