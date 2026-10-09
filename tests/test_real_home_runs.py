"""Native runs share the owner's (fake in tests) CLI configuration in every preset."""

import json
import os
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

from adapters.claude.native import build_command as claude_command
from adapters.codex.native import RuntimeOptions, build_command, thread_parameters
from adapters.shared.process import child_environment
from adapters.shared.provider_setup import child_source, instructions, run_settings


@pytest.mark.parametrize("provider", ["codex", "claude"])
@pytest.mark.parametrize("scheduled", [False, True])
@pytest.mark.parametrize("mode", ["ask", "auto", "full", "read_only"])
def test_native_presets_keep_real_home_and_orchestration(tmp_path, provider, scheduled, mode):
    config = {
        "binary": provider,
        "unrestricted": True,
        "provider_homes": str(tmp_path / "obsolete"),
        "personal_setup": True,
        "personal_instructions": "DO NOT INJECT",
        "integrations": [],
    }
    config.update(run_settings(config, provider, data={"schedule_id": "s"} if scheduled else {}))
    env = child_environment(child_source(config, provider))
    for key in ("HOME", "CODEX_HOME", "CLAUDE_CONFIG_DIR"):
        assert env[key] == os.environ[key]
    assert not (tmp_path / "obsolete").exists()
    assert "DO NOT INJECT" not in instructions(config)
    permissions = dict.fromkeys(("read", "write", "shell", "hooks", "internet"), True)
    if mode == "read_only":
        permissions.update(write=False, shell=False, hooks=False)
    if provider == "codex":
        command = build_command(provider, permissions)
        assert not any("features.apps" in arg or "features.hooks" in arg for arg in command)
        params = thread_parameters(
            config,
            {"access_mode": mode},
            "model",
            SimpleNamespace(
                cwd=tmp_path, home=tmp_path, permissions=permissions, roots=[str(tmp_path)]
            ),
            RuntimeOptions(command),
            mode == "full",
        )
        assert "plugins" not in params["config"]
        assert set(params["config"]["mcp_servers"]) <= {"harness_reader", "keepharness_reader"}
        assert (
            params["sandbox"]
            == {
                "ask": "read-only",
                "auto": "workspace-write",
                "full": "danger-full-access",
                "read_only": "read-only",
            }[mode]
        )
        assert params["approvalPolicy"] == (
            "never" if mode in ("full", "read_only") else "on-request"
        )
    else:
        command = claude_command(config, "model", tmp_path, permissions, [], mode, [])
        assert "--strict-mcp-config" not in command
        assert "--setting-sources" not in command
        settings = json.loads(command[command.index("--settings") + 1])
        assert not {"enabledPlugins", "disableAllHooks", "hooks"} & settings.keys()
        assert (
            command[command.index("--permission-mode") + 1]
            == {
                "ask": "default",
                "auto": "acceptEdits",
                "full": "bypassPermissions",
                "read_only": "plan",
            }[mode]
        )


def test_run_settings_never_reads_personal_files(monkeypatch):
    def forbidden(*args):
        pytest.fail("personal instructions must be loaded by the CLI")

    monkeypatch.setattr("adapters.shared.provider_setup.owner_file", forbidden)
    for provider in ("codex", "claude"):
        assert run_settings({"personal_setup": True}, provider, data={}) == {}


@pytest.mark.parametrize("provider,version", [("codex", "0.158.0"), ("claude", "2.2.0")])
def test_run_version_drift_warns_and_continues(monkeypatch, provider, version):
    from adapters.shared.provider_setup import version_notice

    monkeypatch.setattr("adapters.codex.state._cli_version", lambda *args: version)
    events = []
    version_notice("fixture", provider, lambda *args: events.append(args))
    assert events[0][0] == "provider_warning"
    assert version in events[0][1]["message"]
    assert "tested range" in events[0][1]["message"]


@pytest.mark.parametrize("provider,version", [("codex", "0.157.1"), ("claude", "2.1.292")])
def test_tested_cli_has_no_version_warning(monkeypatch, provider, version):
    from adapters.shared.provider_setup import version_notice

    monkeypatch.setattr("adapters.codex.state._cli_version", lambda *args: version)
    events = []
    version_notice("fixture", provider, lambda *args: events.append(args))
    assert events == []


@pytest.mark.parametrize("provider", ["codex", "claude"])
@pytest.mark.parametrize("scheduled", [False, True])
@pytest.mark.parametrize("mode", ["ask", "auto", "full", "read_only"])
def test_spawned_native_cli_presets_and_schedule_use_owner_home(
    tmp_path, provider, scheduled, mode
):
    from tests.test_scoped_home_security import provider_turn

    if provider == "claude":
        # This fixture uses the native CLI's trusted project path for parity assertions.
        home = Path(os.environ["HOME"])
        (home / ".claude.json").write_text(
            json.dumps(
                {
                    "projects": {
                        str((tmp_path / "sessions/claude/workspace").resolve()): {
                            "hasTrustDialogAccepted": True
                        }
                    }
                }
            )
        )
    record = provider_turn(tmp_path, provider, mode=mode, scheduled=scheduled)
    for key in ("HOME", "CODEX_HOME", "CLAUDE_CONFIG_DIR"):
        assert record["env"][key] == os.environ[key]
    args = record["argv"]
    assert "--dangerously-bypass-hook-trust" not in args
    assert not any(arg.startswith(("features.", "web_search=")) for arg in args)
    if provider == "claude":
        assert "--strict-mcp-config" not in args
        assert "--tools" not in args
        assert "--setting-sources" not in args
        settings = json.loads(args[args.index("--settings") + 1])
        assert (
            not {"permissions", "sandbox", "enabledPlugins", "hooks", "disableAllHooks"}
            & settings.keys()
        )
        assert (
            args[args.index("--permission-mode") + 1]
            == {
                "ask": "default",
                "auto": "acceptEdits",
                "full": "bypassPermissions",
                "read_only": "plan",
            }[mode]
        )
    else:
        params = next(
            row["params"] for row in record["requests"] if row["method"] == "thread/start"
        )
        assert (
            params["sandbox"]
            == {
                "ask": "read-only",
                "auto": "workspace-write",
                "full": "danger-full-access",
                "read_only": "read-only",
            }[mode]
        )
        assert "plugins" not in params["config"]


def test_codex_pending_hooks_are_visible_without_bypassing_review():
    import asyncio
    import sys

    from adapters.codex.rpc import connection

    events = []
    program = """
import json, sys
request = json.loads(sys.stdin.readline())
sys.stderr.write('Hooks pending review: open Codex to review hook trust.\\n')
sys.stderr.flush()
print(json.dumps({'id': request['id'], 'result': {}}), flush=True)
sys.stdin.readline()
sys.stdin.readline()
"""

    async def scenario():
        async with connection(
            [sys.executable, "-c", program], event=lambda *args: events.append(args)
        ):
            pass

    asyncio.run(scenario())
    warnings = [data for kind, data in events if kind == "provider_warning"]
    assert warnings[0]["code"] == "hooks_pending_review"
    assert "hooks pending review" in warnings[0]["message"]
    assert "Codex CLI" in warnings[0]["message"]


@pytest.mark.parametrize("provider", ["codex", "claude"])
def test_state_run_environment_does_not_override_homes(tmp_path, provider):
    from adapters.claude.state import ClaudeStateAdapter
    from adapters.codex.state import CodexStateAdapter

    adapter = CodexStateAdapter() if provider == "codex" else ClaudeStateAdapter(tmp_path)
    setup = adapter.run_environment(tmp_path, False, ["permission-fixture"])
    assert setup.environment == {}
    assert setup.extra_args[0] == "permission-fixture"
    if provider == "claude":
        assert setup.extra_args[-2:] == ["--setting-sources", "user"]
        assert setup.settings_overrides["disableAllHooks"] is True


def test_deepseek_personal_instructions_remain_separate_from_native_homes():
    config = {"personal_setup": True, "personal_instructions": "DeepSeek owner instructions"}
    assert "DeepSeek owner instructions" in instructions(config, provider="deepseek")
    assert "DeepSeek owner instructions" not in instructions(config, provider="codex")
    assert "DeepSeek owner instructions" not in instructions(config, provider="claude")


@pytest.mark.parametrize("stage", ["initialize", "thread/start"])
def test_codex_hook_warning_before_rpc_reply_is_visible_once(stage):
    import asyncio
    import sys

    from adapters.codex.rpc import connection

    events = []
    program = """
import json, sys
for line in sys.stdin:
    request = json.loads(line)
    if request.get('method') == STAGE:
        print(json.dumps({'method':'configWarning','params':{'summary':'Hooks pending review token=fixture-secret'}}), flush=True)
    if 'id' in request:
        print(json.dumps({'id':request['id'], 'result':{}}), flush=True)
""".replace("STAGE", repr(stage))

    async def scenario():
        async with connection(
            [sys.executable, "-c", program], event=lambda *args: events.append(args)
        ) as rpc:
            await rpc.call("thread/start", {})

    asyncio.run(scenario())
    warnings = [data for kind, data in events if kind == "provider_warning"]
    assert len(warnings) == 1
    assert "Hooks pending review" in warnings[0]["message"]
    assert "fixture-secret" not in warnings[0]["message"]


@pytest.mark.parametrize("enabled", [False, True])
def test_native_reader_never_overwrites_owner_mcp_entry(tmp_path, enabled):
    home = Path(os.environ["CODEX_HOME"])
    (home / "config.toml").write_text(
        '[mcp_servers.harness_reader]\ncommand="owner-reader"\nenabled='
        + str(enabled).lower()
        + "\n"
    )
    params = thread_parameters(
        {},
        {"access_mode": "read_only"},
        "model",
        SimpleNamespace(
            cwd=tmp_path, home=tmp_path, roots=[str(tmp_path)], permissions={"read": True}
        ),
        RuntimeOptions(["codex"]),
        False,
        native_config=tomllib.loads((home / "config.toml").read_text()),
    )
    assert "harness_reader" not in params["config"]["mcp_servers"]


@pytest.mark.parametrize("provider", ["codex", "claude"])
def test_unset_native_home_overrides_stay_unset(tmp_path, monkeypatch, provider):
    monkeypatch.delenv("CODEX_HOME")
    monkeypatch.delenv("CLAUDE_CONFIG_DIR")
    source = child_environment(child_source({"provider_homes": str(tmp_path / "old")}, provider))
    assert source["HOME"] == os.environ["HOME"]
    assert "CODEX_HOME" not in source and "CLAUDE_CONFIG_DIR" not in source
    assert not (tmp_path / "old").exists()


@pytest.mark.parametrize("nested", [False, True])
def test_native_reader_respects_disabled_trusted_repository_layer(tmp_path, nested):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".codex").mkdir()
    (repo / ".codex/config.toml").write_text(
        '[mcp_servers.harness_reader]\ncommand="owner-reader"\nenabled=false\n'
    )
    home = Path(os.environ["CODEX_HOME"])
    (home / "config.toml").write_text(
        "[projects." + json.dumps(str(repo)) + ']\ntrust_level="trusted"\n'
    )
    cwd = repo / "sub" if nested else repo
    cwd.mkdir(exist_ok=True)
    params = thread_parameters(
        {},
        {"access_mode": "read_only"},
        "model",
        SimpleNamespace(cwd=cwd, home=tmp_path, roots=[str(cwd)], permissions={"read": True}),
        RuntimeOptions(["fixture-never-executed"]),
        False,
        native_config=tomllib.loads((repo / ".codex/config.toml").read_text()),
    )
    assert "harness_reader" not in params["config"]["mcp_servers"]
