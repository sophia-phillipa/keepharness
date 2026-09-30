"""Synthetic Jira protocol tests; uncertain sends are never repeated."""

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from test_approval_authority import client_for, pending_approval
from test_effect_executor import configure_effects, enroll, request

from agent_service.app import create_app
from agent_service.errors import APIError


@pytest.fixture
def jira_fixture():
    state = SimpleNamespace(posts=[], searches=[], visible=False, lose_response=False)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path == "/rest/api/3/issue":
                state.posts.append(payload)
                if state.lose_response:
                    self.close_connection = True
                    return
                status, result = 201, {"key": "TEST-1", "id": "1", "secret": "must-never-persist"}
            elif self.path == "/rest/api/3/search/jql":
                state.searches.append(payload)
                issues = (
                    [{"key": "TEST-1", "fields": state.posts[0]["fields"]}]
                    if state.visible and state.posts
                    else []
                )
                status, result = 200, {"issues": issues, "isLast": True}
            else:
                status, result = 404, {}
            raw = json.dumps(result).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state.endpoint = "http://127.0.0.1:" + str(server.server_port)
    yield state
    server.shutdown()
    server.server_close()
    thread.join()


async def setup_effect(make_harness_config, fixture):
    config = configure_effects(make_harness_config(), fixture.endpoint)
    app = create_app(config)
    pending_approval(app, "local")
    service = app.state.service
    service.effects.credentials.set(
        "synthetic", {"email": "fixture@example.invalid", "token": "fixture-secret"}
    )
    result = await service.effects.prepare("job", request())
    return app, result


async def approve(app, effect):
    async with client_for(app) as client:
        await enroll(app, client)
        response = await client.post(
            "/v1/approvals/" + effect["gate_id"], json={"choice": "approve"}
        )
        assert response.status_code == 200
    await app.state.service.effects.tasks[effect["effect_id"]]


def test_single_use_create_persists_intent_and_receipt(make_harness_config, jira_fixture):
    async def scenario():
        app, effect = await setup_effect(make_harness_config, jira_fixture)
        service = app.state.service
        original = service.effects.driver.create

        async def checked_create(*args):
            types = [event["type"] for event in service.message_repository.all_events("job")]
            assert "effect_intent" in types
            assert service.effects.get(effect["effect_id"])["status"] == "executing"
            return await original(*args)

        service.effects.driver.create = checked_create
        await approve(app, effect)
        done = service.effects.get(effect["effect_id"])
        assert done["status"] == "done"
        assert done["receipt"] == {"issue_key": "TEST-1"}
        assert done["approved_by"] == "local"
        assert jira_fixture.posts[0]["fields"]["labels"] == [
            "harness-effect-" + effect["effect_id"]
        ]
        with pytest.raises(APIError, match="effect_already_used"):
            await service.effects.execute(effect["effect_id"])
        assert len(jira_fixture.posts) == 1
        assert "must-never-persist" not in str(
            [dict(e) for e in service.message_repository.all_events("job")]
        )
        await service.effects.close()
        service.db.close()

    asyncio.run(scenario())


def test_lost_response_empty_search_stays_unknown_and_backoff(make_harness_config, jira_fixture):
    async def scenario():
        jira_fixture.lose_response = True
        app, effect = await setup_effect(make_harness_config, jira_fixture)
        service = app.state.service
        await approve(app, effect)
        assert service.effects.get(effect["effect_id"])["status"] == "unknown"
        async with client_for(app) as client:
            url = "/v1/effects/" + effect["effect_id"] + "/reconcile"
            assert (await client.post(url, json={"decision": "check"})).status_code == 403
            await enroll(app, client)
            result = await client.post(url, json={"decision": "check"})
            assert result.json()["status"] == "unknown"
            assert (await client.post(url, json={"decision": "check"})).status_code == 409
            assert (await client.post(url, json={"decision": "keep_unknown"})).json()[
                "status"
            ] == "unknown"
            jira_fixture.visible = True
            with service.db:
                service.db.execute("UPDATE effects SET next_reconcile_at=0")
            result = await client.post(url, json={"decision": "check"})
            assert result.json()["status"] == "done"
            assert result.json()["receipt"] == {"issue_key": "TEST-1"}
        assert len(jira_fixture.posts) == 1
        assert len(jira_fixture.searches) == 2
        await service.effects.close()
        service.db.close()

    asyncio.run(scenario())


def test_crash_after_send_before_checkpoint_recovers_unknown(make_harness_config, jira_fixture):
    class SimulatedCrash(BaseException):
        pass

    async def scenario():
        app, effect = await setup_effect(make_harness_config, jira_fixture)
        service = app.state.service
        original = service.effects.driver.create

        async def crash(*args):
            await original(*args)
            raise SimulatedCrash()

        service.effects.driver.create = crash
        with pytest.raises(SimulatedCrash):
            await approve(app, effect)
        assert service.effects.get(effect["effect_id"])["status"] == "executing"
        restarted = create_app(service.config).state.service
        assert restarted.effects.get(effect["effect_id"])["status"] == "unknown"
        with pytest.raises(APIError, match="effect_already_used"):
            await restarted.effects.execute(effect["effect_id"])
        assert len(jira_fixture.posts) == 1
        await service.effects.close()
        await restarted.effects.close()
        service.db.close()
        restarted.db.close()

    asyncio.run(scenario())
