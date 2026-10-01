import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from test_workspaces import config

from agent_service.app import create_app
from agent_service.approval_sessions import consume_enrollment, issue_enrollment
from agent_service.spans import events_to_spans


@pytest.mark.parametrize("approved", [True, False])
def test_native_human_decision_has_durable_outcome(tmp_path, approved):
    async def scenario():
        cfg = config(tmp_path)
        cfg["origins"] = ["http://127.0.0.1:18217"]
        app = create_app(cfg)
        instance = app.state.service
        identity = ("a", cfg["clients"]["a"])
        session = consume_enrollment(cfg, issue_enrollment(cfg, "a"))
        jid = instance.submit(
            identity,
            dict(project_id="p", backend="codex", model="gpt-6-astra", prompt="synthetic audit"),
        )["job_id"]
        row = instance.job(identity, jid)
        handler = instance._approval_handler(
            SimpleNamespace(row=row, data={"model": "gpt-6-astra"}, backend="codex"),
            lambda k, d: instance.event(jid, k, d),
            {},
            {},
        )
        task = asyncio.create_task(handler("command", {"command": "echo synthetic"}))
        try:
            await asyncio.sleep(0)
            aid, future = next(iter(instance.approvals.items()))
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 42000)),
                base_url="http://127.0.0.1:18217",
                cookies={"harness_session": session},
            ) as client:
                response = await client.post(
                    "/v1/approvals/" + aid, json={"approved": approved, "scope": "once"}
                )
                assert response.status_code == 200, response.text
            reply = await task
            instance.finish(jid, "completed", {"answer": "synthetic"})
            # Reopen the persisted database to verify audit survives service restart.
            instance.db.close()
            instance = create_app(cfg).state.service
            rows = instance.message_repository.all_events(jid)
            terminal = next(json.loads(e["data"]) for e in rows if e["type"] == "approval_resolved")
            span = next(
                s
                for s in events_to_spans(instance.job(identity, jid), rows)
                if s["kind"] == "harness.gate"
            )
            print(
                "NATIVE_AUDIT",
                json.dumps(
                    {
                        "approved": approved,
                        "reply": reply,
                        "terminal": terminal,
                        "span_attrs": span["attrs"],
                    }
                ),
            )
            assert reply["approved"] is approved
            assert terminal["resolved_by"] == "a"
            assert isinstance(terminal["resolved_at"], float)
            assert span["attrs"]["outcome"] == ("completed" if approved else "cancelled")
            assert session not in json.dumps(terminal)
            assert terminal.get("approved") is approved
            assert terminal.get("decision") == ("approved" if approved else "denied"), (
                "Known human decision missing from current-schema durable event"
            )
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            instance.db.close()

    asyncio.run(scenario())
