import asyncio
import json
import sys
from pathlib import Path

import pytest

from Adapters.gemini import backend
from Adapters.gemini import account
from Adapters.gemini.native import AcpStream
from agent_service.tools import ToolError


def test_gemini_acp_stream_maps_answer_thought_and_tool_events():
    events = []
    stream = AcpStream(lambda kind, data: events.append((kind, data)))
    stream.consume({"update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "answer"}}})
    stream.consume({"update": {"sessionUpdate": "agent_thought_chunk", "content": {"type": "text", "text": "thought"}}})
    stream.consume({"update": {"sessionUpdate": "tool_call", "toolCallId": "tool-1", "title": "Read", "status": "in_progress"}})
    assert stream.answer == "answer"
    assert [kind for kind, _ in events] == ["answer_delta", "reasoning_delta", "tool_start"]
    assert events[-1][1]["tool_id"] == "tool-1"


def test_gemini_native_uses_acp_persists_session_and_relays_approval(tmp_path):
    executable = tmp_path / "fake-gemini"
    executable.write_text(
        "#!" + sys.executable + "\n" + '''import json,sys
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
'''
    )
    executable.chmod(0o700)
    requests = []
    async def approve(kind, params):
        requests.append((kind, params["toolCall"]["kind"]))
        return {"approved": True}
    result = asyncio.run(backend.run_native(
        {"binary": str(executable)}, "hello", lambda *_: None,
        {"permissions": {"read": True}}, "auto-gemini-3", "configured", tmp_path / "session",
        approve,
    ))
    assert result["answer"] == "ok"
    assert result["thread_id"] == "gemini-session"
    assert requests == [("gemini/Read", "read")]
    marker = json.loads((tmp_path / "session" / "gemini-session.json").read_text())
    assert marker == {"id": "gemini-session"}


def test_gemini_rejects_unsupported_effort_and_scoped_mode(tmp_path):
    try:
        asyncio.run(backend.run_native({}, "", lambda *_: None, {"permissions": {}}, "auto-gemini-3", "low", tmp_path, None))
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
    (gemini_home / "settings.json").write_text(json.dumps({"security": {"auth": {"selectedType": "oauth-personal"}}}))
    (gemini_home / "oauth_creds.json").write_text("present-only")
    executable = tmp_path / "fake-gemini"
    executable.write_text("#!" + sys.executable + "\nAUTH_ERROR=" + repr(auth_error) + "\n" + '''import json,sys
for line in sys.stdin:
 item=json.loads(line)
 if item['method']=='initialize': print(json.dumps({'jsonrpc':'2.0','id':item['id'],'result':{'agentInfo':{'version':'0.60.0'},'authMethods':[{'id':'oauth-personal'}]}}),flush=True)
 elif item['method']=='authenticate':
  response={'jsonrpc':'2.0','id':item['id']}
  response['error' if AUTH_ERROR else 'result']={'code':-32000,'message':'expired'} if AUTH_ERROR else {}
  print(json.dumps(response),flush=True)
''')
    executable.chmod(0o700)
    monkeypatch.setenv("HOME", str(home))
    result = asyncio.run(account.check(str(executable)))
    assert result["authenticated"] is expected
    assert result["credential_present"] is True


def test_check_without_credentials_does_not_start_cli(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock, patch
    monkeypatch.setenv('HOME', str(tmp_path))
    with patch('Adapters.gemini.account.asyncio.create_subprocess_exec', AsyncMock()) as start:
        result = asyncio.run(account.check('/unused/gemini'))
    assert result['authenticated'] is False
    assert result['models'] == {}
    assert result['error'] == 'gemini_login_required'
    start.assert_not_awaited()


@pytest.mark.parametrize('output', ['', '[]', 'not-json'])
def test_check_broken_cli_returns_account_failure(tmp_path, monkeypatch, output):
    home = tmp_path / '.gemini'
    home.mkdir()
    (home / 'settings.json').write_text('{"security":{"auth":{"selectedType":"oauth-personal"}}}')
    (home / 'oauth_creds.json').write_text('presence-only')
    executable = tmp_path / 'gemini'
    executable.write_text('#!' + sys.executable + '\nprint(' + repr(output) + ')\n')
    executable.chmod(0o700)
    monkeypatch.setenv('HOME', str(tmp_path))
    result = asyncio.run(account.check(str(executable)))
    assert result['authenticated'] is False
    assert result['models'] == {}
    assert result['error'].startswith('gemini_')


def test_login_cli_failure_has_no_traceback(tmp_path):
    import subprocess
    executable = tmp_path / 'gemini'
    executable.write_text('#!/bin/sh\nexit 1\n')
    executable.chmod(0o700)
    result = subprocess.run([sys.executable, '-m', 'Adapters.gemini.account', '--binary', str(executable)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 1
    assert 'Could not complete the Google login' in result.stdout
    assert 'Traceback' not in result.stdout + result.stderr


def test_retired_client_login_gives_migration_steps(tmp_path):
    import subprocess
    executable = tmp_path / 'gemini'
    executable.write_text('#!' + sys.executable + '\n' + '''import sys,json
for line in sys.stdin:
 item=json.loads(line)
 response={'jsonrpc':'2.0','id':item['id']}
 if item['method']=='initialize':response['result']={'authMethods':[{'id':'oauth-personal'}]}
 else:response['error']={'code':-32000,'message':'This client is no longer supported. Migrate to Antigravity.'}
 print(json.dumps(response),flush=True)
''')
    executable.chmod(0o700)
    result = subprocess.run([sys.executable, '-m', 'Adapters.gemini.account', '--binary', str(executable)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 1
    assert '[gemini_client_retired]' in result.stdout
    assert 'https://antigravity.google/docs/cli/gcli-migration/' in result.stdout
    assert 'is not yet available here' in result.stdout
    assert 'Traceback' not in result.stdout + result.stderr
