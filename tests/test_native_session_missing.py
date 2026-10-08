"""A native session the provider CLI can no longer resume falls back to the harness history.

Claude Code and Gemini key their sessions by working directory, so moving the state folder
(Tail Harness -> KeepHarness, 0.15.0) leaves every stored "No project" session unresumable.
"""

import asyncio
import json
import sys
from unittest.mock import patch

import pytest

import adapters
from adapters.claude import native
from adapters.gemini import backend as gemini
from agent_service.app import Service
from agent_service.services.queue_worker import provider_condition
from agent_service.tools import ToolError
from tests.deepseek_fixtures import SAFE_CONFIG


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
        assert all(
            text in prompts[1] for text in ("earlier-prompt", "earlier-answer", "new-prompt")
        )
    finally:
        service.db.close()


def test_the_session_retry_repeats_only_the_adapter_call(tmp_path):
    """Hooks are user scripts and the attachment notice is streamed text: both happen once."""
    from unittest.mock import AsyncMock

    from agent_service.errors import APIError

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
                "permissions": {"upload": True},
            }
        },
        "gemini": {"binary": "fixture"},
        "gemini_models": ["auto-gemini-3"],
    }
    service = Service(cfg)
    try:
        service.db.execute(
            "INSERT INTO files(id,project,name,size,hash,pages,owner) VALUES(?,?,?,?,?,?,?)",
            (
                "picture",
                "p",
                "picture",
                1,
                "hash",
                '[{"media_type": "image/png", "text": ""}]',
                "a",
            ),
        )
        payload = {
            "backend": "gemini",
            "model": "auto-gemini-3",
            "effort": "configured",
            "project_id": "p",
            "prompt": "Look at this",
            "file_ids": ["picture"],
        }
        service.db.execute(
            "INSERT INTO jobs(id,project,owner,state,created,payload) VALUES(?,?,?,?,?,?)",
            ("job", "p", "a", "running", 1, json.dumps(payload)),
        )
        service.db.commit()
        row = dict(service.db.execute("SELECT * FROM jobs WHERE id='job'").fetchone())
        calls = []

        async def run(config, prompt, event, project, model, effort, folder, provider, approve):
            calls.append(prompt)
            if len(calls) == 1:
                raise ToolError("native_session_missing")
            event("answer_delta", {"text": "Seen."})
            return {"answer": "Seen.", "backend": "gemini"}

        hooks = AsyncMock()
        with (
            patch.object(
                service,
                "validate_images",
                AsyncMock(side_effect=APIError("model_images_unavailable")),
            ),
            patch("agent_service.catalog_hooks.run_hooks", hooks),
            patch("adapters.run_native", side_effect=run),
        ):
            result = asyncio.run(service.infer(row, payload))
        assert len(calls) == 2
        assert hooks.await_count == 1
        assert "ignored" in result["answer"] and result["answer"].endswith("Seen.")
        streamed = "".join(
            json.loads(event[0])["text"]
            for event in service.db.execute(
                "SELECT data FROM events WHERE job='job' AND type='answer_delta' ORDER BY id"
            )
        )
        assert streamed == result["answer"]
    finally:
        service.db.close()


def fake_codex(tmp_path, resume_error):
    """An app-server whose thread lost its rollout: codex-cli 0.157.1 answers -32600."""
    log = tmp_path / "codex-requests.jsonl"
    executable = tmp_path / "fake-codex"
    executable.write_text(
        "#!" + sys.executable + "\n"
        "import json, sys\n"
        f"log = {str(log)!r}\n"
        "def emit(value): print(json.dumps(value), flush=True)\n"
        "for line in sys.stdin:\n"
        "    request = json.loads(line)\n"
        "    with open(log, 'a') as stream: stream.write(line)\n"
        "    method, ident = request.get('method'), request.get('id')\n"
        "    if method == 'thread/resume':\n"
        "        emit({'id': ident, 'error': {'code': -32600,\n"
        f"            'message': {resume_error!r} + request['params']['threadId']}}}})\n"
        "    elif method == 'config/read':\n"
        f"        emit({{'id': ident, 'result': {SAFE_CONFIG!r}}})\n"
        "    elif method == 'thread/start':\n"
        "        emit({'id': ident, 'result': {'thread': {'id': 'new-thread'}}})\n"
        "    elif method == 'turn/start':\n"
        "        emit({'method': 'turn/started', 'params': {'turn': {'id': 'turn-1'}}})\n"
        "        emit({'method': 'item/agentMessage/delta', 'params': {'delta': 'fresh'}})\n"
        "        emit({'method': 'turn/completed', 'params': {'turn': {'status': 'completed'}}})\n"
        "    elif ident is not None:\n"
        "        emit({'id': ident, 'result': {}})\n"
    )
    executable.chmod(0o700)
    return executable, log


THREAD_OPENING = ("thread/resume", "thread/start")


def requests(log):
    return [json.loads(line) for line in log.read_text().splitlines()]


@pytest.fixture
def codex_session(tmp_path, monkeypatch):
    """A conversation folder that still points at a Codex thread the CLI no longer has."""
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    monkeypatch.setattr("adapters.codex.native.configurations", lambda: {"codex": {}})
    monkeypatch.setattr("adapters.codex.native.inventory", lambda: {"codex": []})
    session = tmp_path / "session"
    session.mkdir()
    (session / "native-thread.json").write_text('{"id": "old-thread"}')
    return session


def run_codex(executable, session, provider, tmp_path):
    config = {"binary": str(executable)}
    if provider == "deepseek":  # DeepSeek runs on the same Codex app-server transport.
        key = tmp_path / "deepseek.key"
        key.write_text("fixture-key")
        (key.parent / "providers" / "deepseek").mkdir(mode=0o700, parents=True, exist_ok=True)
        (key.parent / "providers" / "home").mkdir(mode=0o700, parents=True, exist_ok=True)
        config["api_provider"] = {"url": "http://127.0.0.1:9/v1", "key_file": str(key)}
    return asyncio.run(
        adapters.run_native(
            config, "hello", lambda *_: None, {"permissions": {}}, "fixture", "low", session,
            provider, approve,
        )
    )


@pytest.mark.parametrize("provider", ["codex", "deepseek"])
def test_codex_thread_without_rollout_is_dropped_for_a_fresh_one(tmp_path, codex_session, provider):
    original_marker = {"id": "old-thread"}
    if provider == "deepseek":
        original_marker["adapter"] = "deepseek"
        (codex_session / "native-thread.json").write_text(json.dumps(original_marker))
    executable, log = fake_codex(tmp_path, "no rollout found for thread id ")
    with pytest.raises(ToolError, match="^native_session_missing$"):
        run_codex(executable, codex_session, provider, tmp_path)
    assert not (codex_session / "native-thread.json").exists()
    kept = codex_session / "native-thread.json.before-session-missing"
    assert json.loads(kept.read_text()) == original_marker
    assert run_codex(executable, codex_session, provider, tmp_path)["thread_id"] == "new-thread"
    assert json.loads((codex_session / "native-thread.json").read_text())["id"] == "new-thread"
    methods = [item.get("method") for item in requests(log)]
    assert methods.count("thread/resume") == 1 and methods.count("thread/start") == 1


def test_another_codex_resume_failure_keeps_the_thread_and_carries_its_message(
    tmp_path, codex_session
):
    executable, _ = fake_codex(tmp_path, "unexpected status 401 Unauthorized for ")
    with pytest.raises(ToolError) as caught:
        run_codex(executable, codex_session, "codex", tmp_path)
    assert str(caught.value) == (
        "codex_execution_failed: unexpected status 401 Unauthorized for old-thread"
    )
    assert provider_condition(str(caught.value)) == "provider_authentication_required"
    assert json.loads((codex_session / "native-thread.json").read_text()) == {"id": "old-thread"}


def test_a_lost_codex_thread_replays_the_harness_history_once(tmp_path, codex_session):
    executable, log = fake_codex(tmp_path, "no rollout found for thread id ")
    cfg = {
        "state_dir": str(tmp_path / "state"),
        "projects": {"p": {}},
        "clients": {"a": {"projects": ["p"]}},
        "services": {
            "codex": {
                "enabled": True,
                "mode": "native",
                "models": ["gpt-6-astra"],
                "projects": ["p"],
                "permissions": {},
            }
        },
        "codex": {"binary": str(executable)},
        "codex_models": {"gpt-6-astra": ["low"]},
    }
    service = Service(cfg)
    try:
        old = {
            "backend": "codex",
            "execution_mode": "native",
            "model": "gpt-6-astra",
            "effort": "low",
            "project_id": "p",
            "prompt": "earlier-prompt",
        }
        current = {**old, "prompt": "new-prompt", "parent_job_id": "first"}
        for ident, payload, result in [
            ("first", old, {"answer": "earlier-answer", "thread_id": "old-thread"}),
            ("second", current, {}),
        ]:
            service.db.execute(
                "INSERT INTO jobs(id,project,owner,state,created,payload,result) VALUES(?,?,?,?,?,?,?)",
                (ident, "p", "a", "completed", 1, json.dumps(payload), json.dumps(result)),
            )
        service.db.commit()
        session = tmp_path / "state" / "sessions" / "first" / "codex"
        session.mkdir(parents=True)
        (session / "native-thread.json").write_text('{"id": "old-thread"}')
        from agent_service.conversation_context import save_cursor

        save_cursor(session, "first", {"thread_id": "old-thread"}, "native")
        row = dict(service.db.execute("SELECT * FROM jobs WHERE id='second'").fetchone())
        assert asyncio.run(service.infer(row, current))["answer"] == "fresh"
    finally:
        service.db.close()
    sent = requests(log)
    assert [item["method"] for item in sent if item.get("method") in THREAD_OPENING] == [
        "thread/resume",
        "thread/start",
    ]
    turns = [
        item["params"]["input"][0]["text"] for item in sent if item.get("method") == "turn/start"
    ]
    assert len(turns) == 1
    assert all(text in turns[0] for text in ("earlier-prompt", "earlier-answer", "new-prompt"))
