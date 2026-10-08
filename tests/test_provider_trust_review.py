# ruff: noqa: F401, F811
"""Security review regressions: exact paths, effective MCP opt-out and own-write provenance."""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from adapters.shared.provider_state import ProviderStateVersionError
from control.provider_state import ProviderStateService
from tests.test_admin_provider_state import app, claude_dir, codex_home, project
from tests.test_provider_state_codex import seed
from tests.test_provider_trust import setup_claude


@pytest.mark.parametrize("name", ["café", "rocket-🚀"])
def test_unicode_trust_uses_exact_native_key(app, codex_home, project, name):
    seed(codex_home)
    target = project / name
    target.mkdir()
    adapter = app.state.manager.provider_state.adapters["codex"]
    adapter.trust_project(target)
    assert adapter._is_project_trusted(target)
    wrong = target.with_name(name.encode("unicode_escape").decode().replace("\\", ""))
    assert not adapter._is_project_trusted(wrong)


def fixture_service(app, codex_home, claude_dir, project, *, trusted=False):
    seed(codex_home)
    claude = setup_claude(app, claude_dir, project)
    (project / ".codex").mkdir()
    (project / ".codex/config.toml").write_text(
        '[mcp_servers.original]\ncommand="true"\nenabled=false\n'
    )
    service = app.state.manager.provider_state
    if trusted:
        service.adapters["codex"].trust_project(project)
    asyncio.run(service.read("codex", "p"))
    return service, claude


def test_external_project_edit_during_trust_is_never_an_own_transition(
    app, codex_home, claude_dir, project, monkeypatch
):
    service, _ = fixture_service(app, codex_home, claude_dir, project, trusted=True)
    adapter = service.adapters["codex"]
    real = adapter.trust_project

    def write_and_edit(root, **kwargs):
        real(root, **kwargs)
        (project / ".codex/config.toml").write_text(
            '[mcp_servers.original]\ncommand="true"\nenabled=true\n[mcp_servers.external_added]\ncommand="true"\n'
        )

    monkeypatch.setattr(adapter, "trust_project", write_and_edit)
    assert isinstance(asyncio.run(service.security_write("codex", "p")), dict)
    notices = asyncio.run(service.read("codex", "p"))["external_changes"]
    assert {notice["item_id"] for notice in notices} == {"mcp:original", "mcp:external_added"}


def test_unconsumed_receipt_survives_partial_trust_retry(
    app, codex_home, claude_dir, project, monkeypatch
):
    control, claude = fixture_service(app, codex_home, claude_dir, project)
    remote = ProviderStateService(
        control.state, control.projects, control.adapters, track_notices=False
    )
    real = claude.trust_project

    def unavailable(root):
        raise ProviderStateVersionError("Fixture refusal")

    monkeypatch.setattr(claude, "trust_project", unavailable)
    assert asyncio.run(remote.security_write("codex", "p")).status_code == 422
    monkeypatch.setattr(claude, "trust_project", real)
    assert isinstance(asyncio.run(remote.security_write("codex", "p")), dict)
    control.cache.clear()
    assert asyncio.run(control.read("codex", "p"))["external_changes"] == []


@pytest.mark.parametrize("denial_layer", ["native", "user", "project", "local", "managed"])
def test_approved_disabled_project_mcp_never_executes(
    app, codex_home, claude_dir, project, tmp_path, monkeypatch, denial_layer
):
    from adapters.claude.native import run

    seed(codex_home)
    claude = setup_claude(app, claude_dir, project)
    claude.trust_project(project)
    owner = json.loads(claude._claude_json().read_text())
    (project / ".claude/settings.json").write_text('{"enabledMcpjsonServers":["marker"]}')
    if denial_layer == "native":
        owner["projects"][str(project)]["disabledMcpServers"] = ["marker"]
        claude._claude_json().write_text(json.dumps(owner))
    else:
        denied_path = {
            "user": claude_dir / "settings.json",
            "project": project / ".claude/settings.json",
            "local": project / ".claude/settings.local.json",
            "managed": claude.managed_dir / "managed-settings.json",
        }[denial_layer]
        denied_path.parent.mkdir(parents=True, exist_ok=True)
        denied = json.loads(denied_path.read_text()) if denied_path.exists() else {}
        denied["disabledMcpjsonServers"] = ["marker"]
        denied_path.write_text(json.dumps(denied))
    # Use the actual fixture adapters, including their synthetic managed-settings root.
    monkeypatch.setattr(
        "control.provider_state.ProviderStateService",
        lambda *args, **kwargs: app.state.manager.provider_state,
    )
    marker = project / "mcp.marker"
    (project / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"marker": {"command": "touch", "args": [str(marker)]}}})
    )
    binary = tmp_path / "marker-claude"
    binary.write_text("""#!/usr/bin/env python3
import json, subprocess, sys
from pathlib import Path
args=sys.argv
settings=json.loads(args[args.index('--settings')+1])
servers=json.loads(Path(args[args.index('--mcp-config')+1]).read_text())['mcpServers']
for name, server in servers.items():
    if name not in settings.get('disabledMcpjsonServers', []) and name not in settings.get('disabledMcpServers', []):
        subprocess.run([server['command'], *server.get('args', [])], check=True)
sys.stdin.readline()
print(json.dumps({'type':'result','subtype':'success','result':'ok'}), flush=True)
""")
    binary.chmod(0o700)
    session = tmp_path / "session"
    session.mkdir()

    async def approve(*args):
        return {"approved": True}

    def execute():
        asyncio.run(
            run(
                {"binary": str(binary), "provider_homes": str(tmp_path / "state/providers")},
                "test",
                lambda *args: None,
                project,
                "fixture",
                session,
                {},
                [],
                approve,
            )
        )

    execute()
    assert not marker.exists()
    if denial_layer == "native":
        owner["projects"][str(project)]["disabledMcpServers"] = []
        claude._claude_json().write_text(json.dumps(owner))
    else:
        denied["disabledMcpjsonServers"] = []
        denied_path.write_text(json.dumps(denied))
    execute()
    assert marker.exists()


REAL_PARSER_PROBE = r"""
import json, os, sys, tomllib
from pathlib import Path
from adapters.codex.state import CodexStateAdapter
root = Path.cwd()
home = Path(os.environ['HOME'])
adapter = CodexStateAdapter(environment={name:os.environ[name] for name in ('HOME','CODEX_HOME','CLAUDE_CONFIG_DIR')})
names = ['café', 'rocket-🚀', 'quoted-"path', 'back\\slash', 'with spaces', 'line\nbreak', 'with\ttab']
targets = [root / name for name in names]
errors=[]
for target in targets:
    target.mkdir()
    try:
        adapter.trust_project(target)
    except Exception as exc:
        errors.append(type(exc).__name__)
config = tomllib.loads((home/'.codex/config.toml').read_text())
expected = {str(target.resolve()) for target in targets}
assert set(config.get('projects', {})) == expected, 'Native parser wrote unintended trust keys'
assert not errors, 'Native trust confirmation failed: ' + ','.join(errors)
assert all(adapter._is_project_trusted(target) for target in targets)
assert not adapter._is_project_trusted(root/'cafu00e9')
assert not adapter._is_project_trusted(root/'rocket-ud83dude80')
print('Native parser: Unicode, emoji, quotes, backslashes and spaces preserve exact keys')
"""


def test_real_codex_parser_preserves_exact_project_paths(tmp_path):
    """No model turn: installed app-server parser only, every home and cwd temporary."""
    import shutil

    binary = shutil.which("codex")
    if binary is None:
        pytest.skip("Codex binary is not installed")
    home = tmp_path / "native-owner"
    for folder in (home / ".codex", home / ".claude"):
        folder.mkdir(parents=True)
    cwd = tmp_path / "native-projects"
    cwd.mkdir()
    env = {
        "HOME": str(home),
        "CODEX_HOME": str(home / ".codex"),
        "CLAUDE_CONFIG_DIR": str(home / ".claude"),
        "PATH": str(Path(binary).parent) + os.pathsep + os.defpath,
        "TMPDIR": str(tmp_path),
        "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    version = subprocess.run(
        [binary, "--version"],
        env=env,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )
    done = subprocess.run(
        [sys.executable, "-c", REAL_PARSER_PROBE],
        env=env,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert done.returncode == 0, version.stdout.strip() + ": " + done.stderr[-1200:]
    assert "preserve exact keys" in done.stdout


def test_consumed_receipt_is_not_revived_by_a_later_trust(app, codex_home, claude_dir, project):
    control, _ = fixture_service(app, codex_home, claude_dir, project)
    remote = ProviderStateService(
        control.state, control.projects, control.adapters, track_notices=False
    )
    assert isinstance(asyncio.run(remote.security_write("codex", "p")), dict)
    control.cache.clear()
    assert asyncio.run(control.read("codex", "p"))["external_changes"] == []
    user_config = codex_home / "config.toml"
    user_config.write_text(
        user_config.read_text().replace('trust_level = "trusted"', 'trust_level = "untrusted"')
    )
    config = project / ".codex/config.toml"
    config.write_text('[mcp_servers.second]\ncommand="true"\n')
    control.cache.clear()
    notices = asyncio.run(control.read("codex", "p"))["external_changes"]
    asyncio.run(control.ack("codex", "p", [notice["id"] for notice in notices]))
    assert isinstance(asyncio.run(remote.security_write("codex", "p")), dict)
    config.write_text(
        config.read_text() + '[mcp_servers.original]\ncommand="true"\nenabled=false\n'
    )
    control.cache.clear()
    assert {
        notice["item_id"] for notice in asyncio.run(control.read("codex", "p"))["external_changes"]
    } == {"mcp:original"}


def test_project_change_between_snapshot_and_layer_capture_conflicts(
    app, codex_home, claude_dir, project, monkeypatch
):
    service, _ = fixture_service(app, codex_home, claude_dir, project)
    adapter = service.adapters["codex"]
    real = adapter.read_trust_state

    def moved(root):
        config = project / ".codex/config.toml"
        config.write_text(config.read_text() + '[mcp_servers.external]\ncommand="true"\n')
        return real(root)

    monkeypatch.setattr(adapter, "read_trust_state", moved)
    assert asyncio.run(service.security_write("codex", "p")).status_code == 409
    assert not adapter._is_project_trusted(project)


def test_service_rechecks_root_after_waiting_for_writer_lock(
    app, codex_home, claude_dir, project, tmp_path
):
    service, _ = fixture_service(app, codex_home, claude_dir, project)
    replacement = tmp_path / "replacement"
    replacement.mkdir()

    async def scenario():
        lock = service.locks.setdefault("codex", asyncio.Lock())
        await lock.acquire()
        pending = asyncio.create_task(
            service.security_write("codex", "p", expected_project_root=str(project))
        )
        await asyncio.sleep(0)
        app.state.manager.settings["projects"][0]["root"] = str(replacement)
        lock.release()
        assert (await pending).status_code == 409

    asyncio.run(scenario())
    assert not service.adapters["codex"]._is_project_trusted(replacement)


def test_external_skill_inventory_change_is_not_proven_by_config_version(
    app, codex_home, claude_dir, project, monkeypatch
):
    service, _ = fixture_service(app, codex_home, claude_dir, project)
    adapter = service.adapters["codex"]
    real = adapter.trust_project
    external = str(project / ".agents/skills/external/SKILL.md")

    def trust_and_add_skill(root, **kwargs):
        real(root, **kwargs)
        path = codex_home / "fake-app-server.json"
        value = json.loads(path.read_text())
        value["skills"].append({"name": "external", "path": external, "scope": "repo"})
        path.write_text(json.dumps(value))

    monkeypatch.setattr(adapter, "trust_project", trust_and_add_skill)
    assert isinstance(asyncio.run(service.security_write("codex", "p")), dict)
    notices = asyncio.run(service.read("codex", "p"))["external_changes"]
    assert any(notice["item_id"] == "skill:" + external for notice in notices)
