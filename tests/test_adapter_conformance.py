"""Offline P1 acceptance through discovery, Claude stdio and enrolled human gates.

The fake provider validates adapter requests and emits recorded protocol shapes;
this does not replace the explicitly opt-in live compatibility probes.
"""

import asyncio
import json
import sys
from types import SimpleNamespace

from live.conformance import FIXTURE_ROOT, isolated_fixture
from test_approval_authority import ORIGIN, client_for, pending_approval

from adapters.claude.backend import run_native
from agent_service import resources
from agent_service.app import create_app
from agent_service.approval_sessions import issue_enrollment


def test_unrelated_project_catalog_and_child_gate_stdio(make_harness_config, monkeypatch):
    with isolated_fixture() as fixture:
        for key in ("HOME", "CLAUDE_CONFIG_DIR", "CODEX_HOME", "SYNTHETIC_HOOK_LOG"):
            monkeypatch.setenv(key, fixture.env[key])
        monkeypatch.setattr("adapters.claude.native.configurations", lambda: {"claude": {}})
        monkeypatch.setattr("adapters.claude.native.inventory", lambda: {"claude": []})
        monkeypatch.setattr("adapters.claude.native.child_environment", lambda *_: fixture.env)
        # Generated synthetic payload keeps a 99 KB test fixture out of the repository.
        supplemental = fixture.root / "supplemental_catalog"
        commands = supplemental / "commands"
        commands.mkdir(parents=True)
        (commands / "install.md").write_text(
            "---\ndescription: Synthetic maintenance.\n---\nReport synthetic installation status."
        )
        (commands / "large.md").write_text(
            "---\ndescription: Synthetic large command.\n---\n" + "Synthetic context.\n" * 5500
        )
        original = FIXTURE_ROOT / "catalog"
        config = make_harness_config()
        config["projects"]["p"] = {
            "root": str(fixture.project),
            "catalogs": ["synthetic", "extra"],
            "permissions": {"read": True, "hooks": True, "delegate": True},
        }
        config["catalogs"] = [
            {"id": "synthetic", "root": str(original), "trusted": True, "namespace": "demo"},
            {"id": "extra", "root": str(supplemental), "trusted": True, "namespace": "extra"},
        ]
        config["services"]["claude"] = {
            "enabled": True,
            "mode": "native",
            "models": ["haiku"],
            "projects": ["p"],
        }
        assert not fixture.project.is_relative_to(original)
        global_settings = (fixture.claude_config_dir / "settings.json").read_bytes()
        assert "global_auto_update_hook.py" in global_settings.decode()
        catalog = resources.discover(config, "p", "claude", "haiku", private=True)
        indexed = {(item["kind"], item["name"]): item for item in catalog["items"]}
        command = indexed["command", "synthetic-probe"]
        assert command["selectable"]
        assert command["native_command"] is False
        assert '"$HOME"' in command["_body"]
        assert indexed["command", "large"]["selectable"]
        assert len(indexed["command", "large"]["_body"].encode()) >= 99000
        assert indexed["command", "install"]["group"] == "Maintenance"
        reviewer = indexed["agent", "demo--reviewer"]
        request = {
            "project_id": "p",
            "backend": "claude",
            "model": "haiku",
            "prompt": "/demo--reviewer inspect",
            "resource_selections": [
                {"id": reviewer["id"], "revision": reviewer["revision"], "token": "/demo--reviewer"}
            ],
        }
        selected = resources.resolve(config, request)
        prompt = resources.prepare_prompt(request["prompt"], selected)
        executable = fixture.root / "fake_claude.py"
        evidence = fixture.evidence_dir / "reply.json"
        executable.write_text(
            "#!"
            + sys.executable
            + "\n"
            + """import json, sys
from pathlib import Path
arguments = sys.argv[1:]
assert arguments[arguments.index("--setting-sources") + 1] == "project"
assert "Task" in arguments[arguments.index("--tools") + 1].split(",")
assert "TodoWrite" not in arguments[arguments.index("--tools") + 1].split(",")
assert not json.loads(arguments[arguments.index("--settings") + 1])["disableAllHooks"]
agents = json.loads(Path(arguments[arguments.index("--agents") + 1]).read_text())
assert "SYNTHETIC_GLOBAL_AGENT_REVIEWER" in agents["demo--reviewer"]["prompt"]
assert "tools" not in agents["demo--reviewer"]
message = json.loads(sys.stdin.readline())
assert "/demo--reviewer inspect" in message["message"]["content"][0]["text"]
def emit(value):
    print(json.dumps(value), flush=True)
emit({"type":"stream_event", "event":{"type":"content_block_start", "content_block":{"type":"tool_use", "id":"task-1", "name":"Agent", "input":{"subagent_type":"demo--reviewer"}}}})
emit({"type":"control_request", "parent_tool_use_id":"task-1", "request_id":"question-1", "request":{"subtype":"can_use_tool", "tool_name":"AskUserQuestion", "input":{"questions":[{"question":"Choose a synthetic option?", "options":[{"label":"Alpha"},{"label":"Beta"}], "multiSelect":False}]}}})
response = json.loads(sys.stdin.readline())
Path(EVIDENCE).write_text(json.dumps(response))
assert response["response"]["response"]["updatedInput"]["answers"] == {"Choose a synthetic option?":"Alpha"}
emit({"type":"user", "message":{"content":[{"type":"tool_result", "tool_use_id":"task-1", "content":"SYNTHETIC_GLOBAL_AGENT_REVIEWER"}]}})
emit({"type":"result", "subtype":"success", "result":"SYNTHETIC_GLOBAL_AGENT_REVIEWER CHOICE=Alpha"})
""".replace("EVIDENCE", repr(str(evidence)))
        )
        executable.chmod(0o700)

        async def scenario():
            app = create_app(config)
            service = app.state.service
            pending_approval(app, "local")
            events = []

            def progress(kind, data):
                events.append((kind, data))
                service.event("job", kind, data)

            plan = SimpleNamespace(
                row=service.conversation_repository.get("job"),
                data={"model": "haiku", "access_mode": "full"},
                backend="claude",
            )
            approve = service._approval_handler(plan, progress, {}, {"unrestricted": True})
            project = {**config["projects"]["p"], "_resources": selected}
            execution = asyncio.create_task(
                run_native(
                    {"binary": str(executable)},
                    prompt,
                    progress,
                    project,
                    "haiku",
                    "configured",
                    fixture.root / "session",
                    approve,
                )
            )
            try:
                async with asyncio.timeout(3):
                    while not any(kind == "gate_required" for kind, _ in events):
                        if execution.done():
                            await execution
                        await asyncio.sleep(0.005)
                gate = next(data for kind, data in events if kind == "gate_required")
                async with client_for(app) as client:
                    denied = await client.post(
                        "/v1/approvals/" + gate["gate_id"], json={"choice": "0"}
                    )
                    assert denied.status_code == 403
                    nonce = issue_enrollment(config, "local")
                    await client.post("/approve-device?nonce=" + nonce, headers={"Origin": ORIGIN})
                    resolved = await client.post(
                        "/v1/approvals/" + gate["gate_id"], json={"choice": "0"}
                    )
                    assert resolved.status_code == 200
                result = await asyncio.wait_for(execution, 3)
                assert result["answer"] == "SYNTHETIC_GLOBAL_AGENT_REVIEWER CHOICE=Alpha"
                assert any(
                    kind == "tool_start" and data["tool"] == "Agent" and data["tool_id"] == "task-1"
                    for kind, data in events
                )
                assert any(
                    kind == "hook_scope" and data["scope"] == "project" for kind, data in events
                )
                assert any(
                    kind == "gate_resolved" and data["resolved_by"] == "local"
                    for kind, data in events
                )
                assert json.loads(evidence.read_text())["response"]["request_id"] == "question-1"
                assert not list((fixture.root / "session").glob("claude-rules-*"))
            finally:
                execution.cancel()
                await asyncio.gather(execution, return_exceptions=True)
                service.db.close()

        asyncio.run(scenario())
        assert (fixture.claude_config_dir / "settings.json").read_bytes() == global_settings
        assert not (fixture.evidence_dir / "hooks.log").exists()
