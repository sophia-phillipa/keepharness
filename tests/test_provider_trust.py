# ruff: noqa: F401, F811
"""Owner trust and project MCP approval: isolated homes and executed marker fixtures."""

import asyncio
import json

import pytest

from tests.test_admin_provider_state import (
    HEADERS,
    app,
    claude_dir,
    client,
    codex_home,
    get,
    project,
)
from tests.test_provider_state_codex import seed


def setup_claude(app, claude_dir, project):
    adapter = app.state.manager.provider_state.adapters["claude"]
    adapter.environment = {"HOME": str(claude_dir.parent), "CLAUDE_CONFIG_DIR": str(claude_dir)}
    app.state.manager.provider_state.adapters["codex"].environment = {
        **adapter.environment,
        "CODEX_HOME": str(claude_dir.parent / ".codex"),
    }
    (claude_dir / "settings.json").write_text("{}")
    adapter._claude_json().write_text(
        json.dumps(
            {"other": {"keep": 1}, "projects": {str(project): {"hasTrustDialogAccepted": False}}}
        )
    )
    (project / ".claude").mkdir(exist_ok=True)
    (project / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"marker": {"command": "true"}, "denied": {"command": "true"}}})
    )
    return adapter


def test_trust_accepts_both_cli_states_exact_project(client, app, codex_home, claude_dir, project):
    seed(codex_home)
    claude = setup_claude(app, claude_dir, project)
    assert get(client, "claude", "p").json()["trust"] == {"required": True, "trusted": False}
    response = client.post(
        "/api/provider-state/trust", headers=HEADERS, json={"provider": "claude", "project_id": "p"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["trust"] == {"required": False, "trusted": True}
    assert claude.is_project_trusted(project)
    assert app.state.manager.provider_state.adapters["codex"].is_project_trusted(project)
    assert not claude.is_project_trusted(project.parent)
    assert json.loads(claude._claude_json().read_text())["other"] == {"keep": 1}
    assert get(client, "codex", "p").json()["external_changes"] == []


def test_either_cli_trust_and_denial_wins(client, app, codex_home, claude_dir, project):
    seed(codex_home)
    claude = setup_claude(app, claude_dir, project)
    claude.trust_project(project)
    (project / ".claude/settings.json").write_text(json.dumps({"enableAllProjectMcpServers": True}))
    (claude_dir / "settings.json").write_text(json.dumps({"disabledMcpjsonServers": ["denied"]}))
    assert get(client, "codex", "p").json()["trust"]["trusted"] is True
    assert claude.approved_project_servers(project) == frozenset({"marker"})
    for server in ("marker", "denied"):
        response = client.post(
            "/api/provider-state/mcp-approvals",
            headers=HEADERS,
            json={"provider": "claude", "project_id": "p", "server": server, "approved": True},
        )
        assert response.status_code == 200, response.text
    assert claude.approved_project_servers(project) == frozenset({"marker"})
    assert dict((x["server"], x["approved"]) for x in response.json()["mcp_approvals"]) == {
        "marker": True,
        "denied": False,
    }
    assert (project / ".claude/settings.local.json").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "https://evil.example"},
        {"Sec-Fetch-Site": "cross-site"},
        {"Tailscale-User-Login": "owner@example.com"},
        {"Tailscale-Funnel-Request": "1"},
    ],
)
def test_trust_control_gate_refuses_foreign_requests(client, headers):
    response = client.post(
        "/api/provider-state/trust",
        headers={**HEADERS, **headers},
        json={"provider": "claude", "project_id": "p"},
    )
    assert response.status_code == 403


def test_native_run_executes_hook_env_and_mcp_only_after_acceptance(
    app, codex_home, claude_dir, project, tmp_path
):
    from adapters.claude.native import run

    seed(codex_home)
    claude = setup_claude(app, claude_dir, project)
    binary = tmp_path / "marker-claude"
    binary.write_text("""#!/usr/bin/env python3
import json, os, subprocess, sys
from pathlib import Path
args = sys.argv[1:]
settings = json.loads(args[args.index('--settings') + 1])
sources = args[args.index('--setting-sources') + 1].split(',')
env = dict(os.environ)
project_settings = {}
if 'project' in sources:
    project_settings = json.loads(Path('.claude/settings.json').read_text())
    env.update(project_settings.get('env', {}))
if env.get('TRUST_ENV_MARKER'):
    Path(env['TRUST_ENV_MARKER']).write_text('env')
if not settings.get('disableAllHooks'):
    for group in project_settings.get('hooks', {}).get('SessionStart', []):
        for hook in group['hooks']:
            subprocess.run(hook['command'], shell=True, check=True, env=env)
mcp = json.loads(Path(args[args.index('--mcp-config') + 1]).read_text())
for name, server in mcp['mcpServers'].items():
    if name not in settings.get('disabledMcpjsonServers', []):
        subprocess.run([server['command'], *server.get('args', [])], check=True, env=env)
sys.stdin.readline()
print(json.dumps({'type':'result', 'subtype':'success', 'result':'ok', 'usage':{}, 'session_id':'fixture'}), flush=True)
""")
    binary.chmod(0o700)
    hook, env, mcp = [project / name for name in ("hook.marker", "env.marker", "mcp.marker")]
    (project / ".claude/settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionStart": [{"hooks": [{"type": "command", "command": f"touch {hook}"}]}]
                },
                "env": {"TRUST_ENV_MARKER": str(env)},
            }
        )
    )
    (project / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"marker": {"command": "touch", "args": [str(mcp)]}}})
    )
    home = tmp_path / "session"
    home.mkdir()

    async def approve(*args):
        return {"approved": True}

    def execute():
        asyncio.run(
            run(
                {"binary": str(binary), "control_state_dir": str(app.state.manager.state)},
                "test",
                lambda *args: None,
                project,
                "fixture",
                home,
                {"hooks": True},
                [],
                approve,
            )
        )

    execute()
    assert not any(path.exists() for path in (hook, env, mcp))
    claude.trust_project(project)
    execute()
    assert hook.exists() and env.exists() and not mcp.exists()
    claude.set_project_server_approval(project, "marker", True)
    execute()
    assert mcp.exists()


def test_remote_service_receipt_preserves_control_single_writer_and_external_revert(
    app, codex_home, claude_dir, project
):
    from control.provider_state import ProviderStateService

    seed(codex_home)
    setup_claude(app, claude_dir, project)
    (project / ".codex").mkdir()
    (project / ".codex/config.toml").write_text('[mcp_servers.project_marker]\ncommand="true"\n')
    control = app.state.manager.provider_state
    asyncio.run(control.read("codex", "p"))
    seen = (control.state / "provider-state-seen.json").read_bytes()
    remote = ProviderStateService(
        control.state, control.projects, control.adapters, track_notices=False
    )
    asyncio.run(remote.read("codex", "p"))
    result = asyncio.run(remote.security_write("codex", "p"))
    assert isinstance(result, dict), result
    assert (control.state / "provider-state-seen.json").read_bytes() == seen
    control.cache.clear()
    assert asyncio.run(control.read("codex", "p"))["external_changes"] == []
    config = codex_home / "config.toml"
    config.write_text(
        config.read_text().replace('trust_level = "trusted"', 'trust_level = "untrusted"')
    )
    control.cache.clear()
    assert any(
        n["item_id"] == "mcp:project_marker"
        for n in asyncio.run(control.read("codex", "p"))["external_changes"]
    )


def test_partial_trust_write_keeps_receipt_and_reports_failure(
    app, codex_home, claude_dir, project, monkeypatch
):
    from adapters.shared.provider_state import ProviderStateVersionError
    from control.provider_state import ProviderStateService

    seed(codex_home)
    claude = setup_claude(app, claude_dir, project)
    (project / ".codex").mkdir()
    (project / ".codex/config.toml").write_text('[mcp_servers.project_marker]\ncommand="true"\n')
    control = app.state.manager.provider_state
    asyncio.run(control.read("codex", "p"))

    def refuse(root):
        raise ProviderStateVersionError("Unsupported version")

    monkeypatch.setattr(claude, "trust_project", refuse)
    remote = ProviderStateService(
        control.state, control.projects, control.adapters, track_notices=False
    )
    assert asyncio.run(remote.security_write("codex", "p")).status_code == 422
    assert control.adapters["codex"].is_project_trusted(project)
    assert not claude._is_project_trusted(project)
    control.cache.clear()
    assert asyncio.run(control.read("codex", "p"))["external_changes"] == []


@pytest.mark.parametrize(
    "headers, expected",
    [
        ({"Origin": "https://evil.test"}, 403),
        ({"Sec-Fetch-Site": "cross-site"}, 403),
        ({"Tailscale-Funnel-Request": "1"}, 403),
        ({"Tailscale-User-Login": "unknown@example.test"}, 401),
    ],
)
def test_harness_security_routes_use_owner_gate(tmp_path, headers, expected):
    import httpx

    from agent_service.app import create_app
    from tests.test_api_security import owner_config, owner_cookie

    cfg = owner_config(tmp_path, projects={"p": {"root": str(tmp_path)}, "sem-projeto": {}})
    app = create_app(cfg)

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 42000)),
            base_url="http://127.0.0.1:8095",
        ) as client:
            client.cookies.update(owner_cookie(cfg))
            return await client.post(
                "/v1/provider-state/trust",
                json={"provider": "claude", "project_id": "p"},
                headers=headers,
            )

    try:
        assert asyncio.run(scenario()).status_code == expected
    finally:
        app.state.service.db.close()


def test_both_adapters_union_trust_and_codex_only_approvals(app, codex_home, claude_dir, project):
    seed(codex_home)
    claude = setup_claude(app, claude_dir, project)
    codex = app.state.manager.provider_state.adapters["codex"]
    (project / ".claude/settings.json").write_text('{"enabledMcpjsonServers":["marker"]}')
    codex.trust_project(project)
    assert not claude._is_project_trusted(project)
    assert claude.is_project_trusted(project)
    assert claude.approved_project_servers(project) == frozenset({"marker"})
    (codex_home / "config.toml").write_text("")
    claude.trust_project(project)
    assert not codex._is_project_trusted(project)
    assert codex.is_project_trusted(project)


def test_malformed_approval_settings_fail_closed(app, codex_home, claude_dir, project):
    from adapters.shared.provider_state import ProviderStateSchemaError

    seed(codex_home)
    claude = setup_claude(app, claude_dir, project)
    claude.trust_project(project)
    (project / ".claude/settings.json").write_text('{"enableAllProjectMcpServers":true}')
    (claude_dir / "settings.json").write_text("{broken")
    with pytest.raises(ProviderStateSchemaError):
        claude.approved_project_servers(project)


def test_harness_routes_accept_local_session_and_verified_remote_owner(
    app, codex_home, claude_dir, project, tmp_path
):
    import httpx

    from agent_service.app import create_app
    from control.provider_state import ProviderStateService
    from tests.test_api_security import REMOTE_LOGIN, SERVE_HEADERS, owner_config, owner_cookie

    seed(codex_home)
    setup_claude(app, claude_dir, project)
    cfg = owner_config(tmp_path, projects={"p": {"root": str(project)}, "sem-projeto": {}})
    cfg["clients"]["local"]["projects"] = ["p", "sem-projeto"]
    harness = create_app(cfg)
    harness.state.service.provider_state = ProviderStateService(
        app.state.manager.state,
        app.state.manager.provider_state.projects,
        app.state.manager.provider_state.adapters,
        track_notices=False,
    )
    harness.state.service.serve_peer_check = lambda *args: True

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=harness, client=("127.0.0.1", 42000)),
            base_url="http://127.0.0.1:8095",
        ) as client:
            client.cookies.update(owner_cookie(cfg))
            local = await client.get(
                "/v1/provider-state", params={"provider": "claude", "project_id": "p"}
            )
            assert local.status_code == 200, local.text
            assert local.json()["trust"]["required"]
            client.cookies.clear()
            remote = await client.post(
                "/v1/provider-state/trust",
                headers={**SERVE_HEADERS, "Tailscale-User-Login": REMOTE_LOGIN},
                json={"provider": "claude", "project_id": "p"},
            )
            assert remote.status_code == 200, remote.text
            assert remote.json()["trust"]["trusted"]
            harness.state.service.serve_peer_check = lambda *args: False
            denied = await client.post(
                "/v1/provider-state/trust",
                headers={**SERVE_HEADERS, "Tailscale-User-Login": REMOTE_LOGIN},
                json={"provider": "claude", "project_id": "p"},
            )
            assert denied.status_code == 401

    try:
        asyncio.run(scenario())
    finally:
        harness.state.service.db.close()


def test_successful_write_then_read_failure_invalidates_cache(
    app, codex_home, claude_dir, project, monkeypatch
):
    from adapters.shared.provider_state import ProviderStateSchemaError

    seed(codex_home)
    setup_claude(app, claude_dir, project)
    service = app.state.manager.provider_state
    (project / ".codex").mkdir()
    (project / ".codex/config.toml").write_text('[mcp_servers.project_marker]\ncommand="true"\n')
    asyncio.run(service.read("codex", "p"))
    adapter = service.adapters["codex"]
    read = adapter.read_state
    real = adapter.trust_project

    def write_then_fail(root, **kwargs):
        real(root, **kwargs)

        def unavailable(root):
            raise ProviderStateSchemaError("Temporarily unreadable")

        monkeypatch.setattr(adapter, "read_state", unavailable)

    monkeypatch.setattr(adapter, "trust_project", write_then_fail)
    assert asyncio.run(service.security_write("codex", "p")).status_code == 422
    assert not any(key[0] == "codex" for key in service.cache)

    monkeypatch.setattr(adapter, "read_state", read)
    config = codex_home / "config.toml"
    config.write_text(
        config.read_text().replace("enabled = false  # turned off", "enabled = true  # external")
    )
    result = asyncio.run(service.read("codex", "p"))
    assert [notice["item_id"] for notice in result["external_changes"]] == [
        "plugin:github@openai-curated"
    ]


def test_external_user_edit_during_trust_write_still_notifies(
    app, codex_home, claude_dir, project, monkeypatch
):
    seed(codex_home)
    setup_claude(app, claude_dir, project)
    service = app.state.manager.provider_state
    asyncio.run(service.read("codex", "p"))
    adapter = service.adapters["codex"]
    trust = adapter.trust_project

    def accept_and_edit(root, **kwargs):
        trust(root, **kwargs)
        path = codex_home / "config.toml"
        path.write_text(
            path.read_text().replace("enabled = false  # turned off", "enabled = true  # external")
        )

    monkeypatch.setattr(adapter, "trust_project", accept_and_edit)
    assert isinstance(asyncio.run(service.security_write("codex", "p")), dict)
    assert [
        notice["item_id"] for notice in asyncio.run(service.read("codex", "p"))["external_changes"]
    ] == ["plugin:github@openai-curated"]


def test_project_mcp_cannot_replace_harness_effects(tmp_path, monkeypatch):
    from adapters.claude.native import build_command
    from agent_service import effect_transport

    monkeypatch.setattr(
        effect_transport, "server_spec", lambda capability: {"command": "trusted-harness"}
    )
    config = {
        "binary": "fixture",
        "_effect_capability": "fixture",
        "_project_security": {
            "trusted": True,
            "project_servers": ["harness_effects"],
            "approved_servers": {"harness_effects": {"command": "project-command"}},
            "disabled_servers": [],
        },
    }
    build_command(config, "fixture", tmp_path, {}, [], "ask", [])
    assert (
        json.loads((tmp_path / "mcp.json").read_text())["mcpServers"]["harness_effects"]["command"]
        == "trusted-harness"
    )
