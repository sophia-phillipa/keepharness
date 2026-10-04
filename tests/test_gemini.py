import asyncio
import json
import sys

import pytest

from adapters.gemini import account, backend
from adapters.gemini.native import AcpStream
from agent_service.tools import ToolError


def test_gemini_acp_stream_maps_answer_thought_and_tool_events():
    events = []
    stream = AcpStream(lambda kind, data: events.append((kind, data)))
    stream.consume(
        {
            "update": {
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "text", "text": "answer"},
            }
        }
    )
    stream.consume(
        {
            "update": {
                "sessionUpdate": "agent_thought_chunk",
                "content": {"type": "text", "text": "thought"},
            }
        }
    )
    stream.consume(
        {
            "update": {
                "sessionUpdate": "tool_call",
                "toolCallId": "tool-1",
                "title": "Read",
                "status": "in_progress",
            }
        }
    )
    assert stream.answer == "answer"
    assert [kind for kind, _ in events] == ["answer_delta", "reasoning_delta", "tool_start"]
    assert events[-1][1]["tool_id"] == "tool-1"


def test_gemini_native_uses_acp_persists_session_and_relays_approval(tmp_path):
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
 elif method=='session/new': send({'jsonrpc':'2.0','id':ident,'result':{'sessionId':'gemini-session'}})
 elif method=='session/set_model': send({'jsonrpc':'2.0','id':ident,'result':{}})
 elif method=='session/prompt':
  send({'jsonrpc':'2.0','id':'permission-1','method':'session/request_permission','params':{'options':[{'optionId':'once','kind':'allow_once','name':'Allow once'},{'optionId':'never','kind':'reject_once','name':'Reject'}],'toolCall':{'toolCallId':'tool-1','kind':'read','title':'Read'}}})
  json.loads(sys.stdin.readline())
  send({'jsonrpc':'2.0','method':'session/update','params':{'sessionId':'gemini-session','update':{'sessionUpdate':'agent_message_chunk','content':{'type':'text','text':'ok'}}}})
  send({'jsonrpc':'2.0','id':ident,'result':{'stopReason':'end_turn','_meta':{'quota':{'token_count':{'input_tokens':3,'output_tokens':2}}}}})
"""
    )
    executable.chmod(0o700)
    requests = []

    async def approve(kind, params):
        requests.append((kind, params["toolCall"]["kind"]))
        return {"approved": True}

    result = asyncio.run(
        backend.run_native(
            {"binary": str(executable)},
            "hello",
            lambda *_: None,
            {"permissions": {"read": True}},
            "auto-gemini-3",
            "configured",
            tmp_path / "session",
            approve,
        )
    )
    assert result["answer"] == "ok"
    assert result["thread_id"] == "gemini-session"
    assert requests == [("gemini/Read", "read")]
    marker = json.loads((tmp_path / "session" / "gemini-session.json").read_text())
    assert marker == {"id": "gemini-session"}


def test_gemini_rejects_unsupported_effort_and_scoped_mode(tmp_path):
    try:
        asyncio.run(
            backend.run_native(
                {}, "", lambda *_: None, {"permissions": {}}, "auto-gemini-3", "low", tmp_path, None
            )
        )
    except ToolError as error:
        assert str(error) == "gemini_effort_unavailable"
    else:
        raise AssertionError("unsupported effort was accepted")
    try:
        asyncio.run(backend.run_scoped({}, "", lambda *_: None))
    except ToolError as error:
        assert str(error) == "gemini_scoped_unsupported"
    else:
        raise AssertionError("scoped execution was accepted")


@pytest.mark.parametrize("auth_error, expected", [(False, True), (True, False)])
def test_gemini_check_refreshes_oauth_without_a_prompt(tmp_path, monkeypatch, auth_error, expected):
    home = tmp_path / "home"
    gemini_home = home / ".gemini"
    gemini_home.mkdir(parents=True)
    (gemini_home / "settings.json").write_text(
        json.dumps({"security": {"auth": {"selectedType": "oauth-personal"}}})
    )
    (gemini_home / "oauth_creds.json").write_text("present-only")
    executable = tmp_path / "fake-gemini"
    executable.write_text(
        "#!"
        + sys.executable
        + "\nAUTH_ERROR="
        + repr(auth_error)
        + "\n"
        + """import json,sys
for line in sys.stdin:
 item=json.loads(line)
 if item['method']=='initialize': print(json.dumps({'jsonrpc':'2.0','id':item['id'],'result':{'agentInfo':{'version':'0.60.0'},'authMethods':[{'id':'oauth-personal'}]}}),flush=True)
 elif item['method']=='authenticate':
  response={'jsonrpc':'2.0','id':item['id']}
  response['error' if AUTH_ERROR else 'result']={'code':-32000,'message':'expired'} if AUTH_ERROR else {}
  print(json.dumps(response),flush=True)
"""
    )
    executable.chmod(0o700)
    monkeypatch.setenv("HOME", str(home))
    result = asyncio.run(account.check(str(executable)))
    assert result["authenticated"] is expected
    assert result["credential_present"] is True


def test_check_without_credentials_does_not_start_cli(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock, patch

    monkeypatch.setenv("HOME", str(tmp_path))
    with patch("adapters.gemini.account.asyncio.create_subprocess_exec", AsyncMock()) as start:
        result = asyncio.run(account.check("/unused/gemini"))
    assert result["authenticated"] is False
    assert result["models"] == {}
    assert result["error"] == "gemini_login_required"
    start.assert_not_awaited()


@pytest.mark.parametrize("output", ["", "[]", "not-json"])
def test_check_broken_cli_returns_account_failure(tmp_path, monkeypatch, output):
    home = tmp_path / ".gemini"
    home.mkdir()
    (home / "settings.json").write_text('{"security":{"auth":{"selectedType":"oauth-personal"}}}')
    (home / "oauth_creds.json").write_text("presence-only")
    executable = tmp_path / "gemini"
    executable.write_text("#!" + sys.executable + "\nprint(" + repr(output) + ")\n")
    executable.chmod(0o700)
    monkeypatch.setenv("HOME", str(tmp_path))
    result = asyncio.run(account.check(str(executable)))
    assert result["authenticated"] is False
    assert result["models"] == {}
    assert result["error"].startswith("gemini_")


def test_login_cli_failure_has_no_traceback(tmp_path):
    import subprocess

    executable = tmp_path / "gemini"
    executable.write_text("#!/bin/sh\nexit 1\n")
    executable.chmod(0o700)
    result = subprocess.run(
        [sys.executable, "-m", "adapters.gemini.account", "--binary", str(executable)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 1
    assert "Could not complete the Google login" in result.stdout
    assert "Traceback" not in result.stdout + result.stderr


def test_retired_client_login_gives_migration_steps(tmp_path):
    import subprocess

    executable = tmp_path / "gemini"
    executable.write_text(
        "#!"
        + sys.executable
        + "\n"
        + """import sys,json
for line in sys.stdin:
 item=json.loads(line)
 response={'jsonrpc':'2.0','id':item['id']}
 if item['method']=='initialize':response['result']={'authMethods':[{'id':'oauth-personal'}]}
 else:response['error']={'code':-32000,'message':'This client is no longer supported. Migrate to Antigravity.'}
 print(json.dumps(response),flush=True)
"""
    )
    executable.chmod(0o700)
    result = subprocess.run(
        [sys.executable, "-m", "adapters.gemini.account", "--binary", str(executable)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 1
    assert "[gemini_client_retired]" in result.stdout
    assert "https://antigravity.google/docs/cli/gcli-migration/" in result.stdout
    assert "is not yet available here" in result.stdout
    assert "Traceback" not in result.stdout + result.stderr


# ------------------------------------------------------------------ errors and the handshake


def fake_acp(tmp_path, *, load_error=None, prompt_error=None, hang=None):
    """A Gemini ACP fake: the given methods answer with a JSON-RPC error or never answer."""
    executable = tmp_path / "fake-gemini"
    executable.write_text(
        "#!"
        + sys.executable
        + "\nLOAD_ERROR, PROMPT_ERROR, HANG = "
        + repr((load_error, prompt_error, hang))
        + "\n"
        + """import json,sys
def send(value): print(json.dumps(value), flush=True)
for line in sys.stdin:
 item=json.loads(line); method=item.get('method'); ident=item.get('id')
 if method==HANG: continue
 if method=='initialize': send({'jsonrpc':'2.0','id':ident,'result':{'agentCapabilities':{'loadSession':True}}})
 elif method=='session/load': send({'jsonrpc':'2.0','id':ident,'error':LOAD_ERROR})
 elif method=='session/new': send({'jsonrpc':'2.0','id':ident,'result':{'sessionId':'new-session'}})
 elif method=='session/set_model': send({'jsonrpc':'2.0','id':ident,'result':{}})
 elif method=='session/prompt':
  if PROMPT_ERROR: send({'jsonrpc':'2.0','id':ident,'error':PROMPT_ERROR})
  else: send({'jsonrpc':'2.0','id':ident,'result':{'stopReason':'end_turn'}})
"""
    )
    executable.chmod(0o700)
    return executable


def run_gemini(executable, session):
    async def approve(*_args):
        return {"approved": True}

    return asyncio.run(
        backend.run_native(
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


@pytest.mark.parametrize(
    "error,session_is_gone",
    [
        ({"code": -32002, "message": "Resource not found"}, True),
        ({"code": -32603, "message": "Session not found: old-session"}, True),
        (
            {"code": -32603, "message": "Internal error", "data": {"details": "Session not found"}},
            True,
        ),
        ({"code": -32000, "message": "Authentication required"}, False),
        ({"code": -32603, "message": "Quota exceeded for this account"}, False),
        ({"code": -32603, "message": "Internal error"}, False),
    ],
)
def test_only_a_missing_session_discards_the_stored_one(tmp_path, error, session_is_gone):
    session = tmp_path / "session"
    session.mkdir()
    (session / "gemini-session.json").write_text('{"id": "old-session"}')
    with pytest.raises(ToolError) as caught:
        run_gemini(fake_acp(tmp_path, load_error=error), session)
    kept = session / "gemini-session.json"
    if session_is_gone:
        assert str(caught.value) == "native_session_missing"
        assert not kept.exists()
        assert (session / "gemini-session.json.before-session-missing").exists()
    else:
        # An expired login or a quota error must not cost the provider-side context.
        assert str(caught.value).startswith("gemini_execution_failed: ")
        assert json.loads(kept.read_text()) == {"id": "old-session"}
        assert not (session / "gemini-session.json.before-session-missing").exists()


def test_a_gemini_failure_carries_the_providers_message_to_the_account_conditions(tmp_path):
    from agent_service.services.queue_worker import provider_condition

    for message, condition in [
        ("Authentication required", "provider_authentication_required"),
        ("Quota exceeded for quota metric 'Gemini requests'", "provider_quota_exhausted"),
        ("429 Too many requests", "provider_rate_limit"),
    ]:
        with pytest.raises(ToolError) as caught:
            run_gemini(
                fake_acp(tmp_path, prompt_error={"code": -32000, "message": message}),
                tmp_path / "session",
            )
        assert str(caught.value) == "gemini_execution_failed: " + message
        assert provider_condition(str(caught.value)) == condition


@pytest.mark.parametrize("stalled", ["initialize", "session/new", "session/set_model"])
def test_a_stalled_handshake_is_not_reported_as_the_active_time_limit(
    tmp_path, monkeypatch, stalled
):
    from adapters.gemini import native

    monkeypatch.setattr(native, "HANDSHAKE_SECONDS", 0.3)
    with pytest.raises(ToolError, match="^provider_idle_timeout$"):
        run_gemini(fake_acp(tmp_path, hang=stalled), tmp_path / "session")


def test_a_stalled_session_load_is_a_provider_stall_and_keeps_the_session(tmp_path, monkeypatch):
    from adapters.gemini import native

    monkeypatch.setattr(native, "HANDSHAKE_SECONDS", 0.3)
    session = tmp_path / "session"
    session.mkdir()
    (session / "gemini-session.json").write_text('{"id": "old-session"}')
    with pytest.raises(ToolError, match="^provider_idle_timeout$"):
        run_gemini(fake_acp(tmp_path, hang="session/load"), session)
    assert json.loads((session / "gemini-session.json").read_text()) == {"id": "old-session"}
