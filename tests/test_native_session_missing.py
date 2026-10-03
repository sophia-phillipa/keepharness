"""A native session the provider CLI can no longer resume falls back to the harness history.

Claude Code and Gemini key their sessions by working directory, so moving the state folder
(Tail Harness -> KeepHarness, 0.15.0) leaves every stored "No project" session unresumable.
"""

import asyncio
import json
import sys
from unittest.mock import patch

import pytest

from adapters.claude import native
from adapters.gemini import backend as gemini
from agent_service.app import Service
from agent_service.tools import ToolError


async def approve(*_args):
    return {"approved": True}


def fake_claude(tmp_path, resume_error):
    """Fails --resume the way Claude Code 2.1 does: stderr, a stream-json error, exit 1."""
    executable = tmp_path / "fake-claude"
    executable.write_text(
        "#!" + sys.executable + "\n"
        "import json, sys\n"
        "if '--resume' in sys.argv:\n"
        f"    print({resume_error!r} + sys.argv[sys.argv.index('--resume') + 1], file=sys.stderr, flush=True)\n"
        "    print(json.dumps({'type':'result','subtype':'error_during_execution','is_error':True}), flush=True)\n"
        "    sys.exit(1)\n"
        "json.loads(sys.stdin.readline())\n"
        "print(json.dumps({'type':'result','subtype':'success','result':'fresh','session_id':'new-session'}), flush=True)\n"
    )
    executable.chmod(0o700)
    return executable


def run_claude(executable, home, cwd):
    return asyncio.run(
        native.run(
            {"binary": str(executable)},
            "hello",
            lambda *_: None,
            cwd,
            "sonnet",
            home,
            {"read": True},
            [],
            approve,
        )
    )


@pytest.fixture
def claude_home(tmp_path, monkeypatch):
    monkeypatch.setattr(native, "configurations", lambda: {"claude": {}})
    monkeypatch.setattr(native, "inventory", lambda: {"claude": []})
    home = tmp_path / "home"
    home.mkdir()
    (home / "claude-session.json").write_text('{"id": "old-session"}')
    return home


def test_claude_session_missing_after_a_move_is_dropped_for_a_fresh_one(tmp_path, claude_home):
    executable = fake_claude(tmp_path, "No conversation found with session ID: ")
    with pytest.raises(ToolError, match="^native_session_missing$"):
        run_claude(executable, claude_home, tmp_path)
    assert not (claude_home / "claude-session.json").exists()
    kept = claude_home / "claude-session.json.before-session-missing"
    assert json.loads(kept.read_text()) == {"id": "old-session"}
    assert run_claude(executable, claude_home, tmp_path)["thread_id"] == "new-session"
    assert json.loads((claude_home / "claude-session.json").read_text()) == {"id": "new-session"}


def test_another_claude_resume_failure_keeps_the_session(tmp_path, claude_home):
    executable = fake_claude(tmp_path, "API Error: overloaded ")
    with pytest.raises(ToolError, match="^claude_execution_failed$"):
        run_claude(executable, claude_home, tmp_path)
    assert json.loads((claude_home / "claude-session.json").read_text()) == {"id": "old-session"}


def fake_gemini(tmp_path):
    executable = tmp_path / "fake-gemini"
    executable.write_text(
        "#!"
        + sys.executable
        + "\n"
        + """import json,sys
def send(value): print(json.dumps(value), flush=True)
for line in sys.stdin:
 item=json.loads(line); method=item.get('method'); ident=item.get('id')
 if method=='initialize': send({'jsonrpc':'2.0','id':ident,'result':{'agentCapabilities':{'loadSession':True}}})
 elif method=='session/load': send({'jsonrpc':'2.0','id':ident,'error':{'code':-32002,'message':'Session not found'}})
 elif method=='session/new': send({'jsonrpc':'2.0','id':ident,'result':{'sessionId':'new-session'}})
 elif method=='session/set_model': send({'jsonrpc':'2.0','id':ident,'result':{}})
 elif method=='session/prompt': send({'jsonrpc':'2.0','id':ident,'result':{'stopReason':'end_turn'}})
"""
    )
    executable.chmod(0o700)
    return executable


def test_gemini_session_it_cannot_load_is_dropped_for_a_fresh_one(tmp_path):
    executable = fake_gemini(tmp_path)
    session = tmp_path / "session"
    session.mkdir()
    (session / "gemini-session.json").write_text('{"id": "old-session"}')

    def run():
        return asyncio.run(
            gemini.run_native(
                {"binary": str(executable)},
                "hello",
                lambda *_: None,
                {"permissions": {"read": True}},
                "auto-gemini-3",
                "configured",
                session,
                approve,
            )
        )

    with pytest.raises(ToolError, match="^native_session_missing$"):
        run()
    kept = session / "gemini-session.json.before-session-missing"
    assert json.loads(kept.read_text()) == {"id": "old-session"}
    assert run()["thread_id"] == "new-session"


def test_a_lost_native_session_continues_from_the_harness_history(tmp_path):
    cfg = {
        "state_dir": str(tmp_path),
        "projects": {"p": {}},
        "clients": {"a": {"projects": ["p"]}},
        "services": {
            "gemini": {
                "enabled": True,
                "mode": "native",
                "models": ["auto-gemini-3"],
                "projects": ["p"],
                "permissions": {},
            }
        },
        "gemini": {"binary": "fixture"},
        "gemini_models": ["auto-gemini-3"],
    }
    service = Service(cfg)
    try:
        old = {
            "backend": "gemini",
            "model": "auto-gemini-3",
            "effort": "configured",
            "project_id": "p",
            "prompt": "earlier-prompt",
        }
        current = {**old, "prompt": "new-prompt", "parent_job_id": "first"}
        for ident, payload, result in [
            ("first", old, {"answer": "earlier-answer", "thread_id": "gemini-thread"}),
            ("second", current, {}),
        ]:
            service.db.execute(
                "INSERT INTO jobs(id,project,owner,state,created,payload,result) VALUES(?,?,?,?,?,?,?)",
                (ident, "p", "a", "completed", 1, json.dumps(payload), json.dumps(result)),
            )
        service.db.commit()
        session = tmp_path / "sessions" / "first" / "gemini"
        session.mkdir(parents=True)
        (session / "gemini-session.json").write_text('{"id":"gemini-thread"}')
        from agent_service.conversation_context import save_cursor

        save_cursor(session, "first", {"thread_id": "gemini-thread"}, "native")
        row = dict(service.db.execute("SELECT * FROM jobs WHERE id='second'").fetchone())
        prompts = []

        async def run(config, prompt, event, project, model, effort, folder, provider, approve):
            prompts.append(prompt)
            marker = folder / "gemini-session.json"
            if marker.exists():  # what the adapter does when the CLI cannot resume it
                marker.replace(folder / "gemini-session.json.before-session-missing")
                raise ToolError("native_session_missing")
            return {"answer": "new-answer", "backend": "gemini"}

        with patch("adapters.run_native", side_effect=run):
            assert asyncio.run(service.infer(row, current))["answer"] == "new-answer"
        assert len(prompts) == 2
        assert "earlier-answer" not in prompts[0]
        assert all(text in prompts[1] for text in ("earlier-prompt", "earlier-answer", "new-prompt"))
    finally:
        service.db.close()
