"""Synthetic in-process HTTP regression: access revoked while a lookup is pending."""

import asyncio
import hashlib
import json

import pytest
from test_approval_authority import ORIGIN, client_for
from test_effect_executor import enroll, prepared

from agent_service.approval_sessions import revoke_sessions, session_database


@pytest.fixture
def make_harness_config(tmp_path):
    def factory():
        return {
            "state_dir": str(tmp_path / "state"),
            "bind": "127.0.0.1",
            "port": 18215,
            "local_access": True,
            "origins": [ORIGIN],
            "clients": {
                "local": {
                    "sha256": hashlib.sha256(b"synthetic").hexdigest(),
                    "projects": ["sem-projeto"],
                }
            },
            "projects": {"sem-projeto": {}},
            "services": {},
        }

    return factory


@pytest.mark.parametrize("change", ["logout", "revoke", "expiry", "project", "owner", "valid"])
def test_reconcile_response_revalidates_authority(make_harness_config, change):
    async def run():
        app, effect = await prepared(make_harness_config)
        s = app.state.service
        eid = effect["effect_id"]
        # Repository transition models an already uncertain external result; no POST is sent.
        s.effects._status(eid, "unknown")
        entered, release = asyncio.Event(), asyncio.Event()

        async def synthetic_lookup(*_):
            entered.set()
            await release.wait()
            return {"issue_key": "PRIVATE-SYNTHETIC-12"}

        s.effects.driver.reconcile = synthetic_lookup
        try:
            async with client_for(app) as c:
                await enroll(app, c)
                pending = asyncio.create_task(
                    c.post("/v1/effects/" + eid + "/reconcile", json={"decision": "check"})
                )
                await asyncio.wait_for(entered.wait(), 2)
                if change == "logout":
                    assert (await c.post("/v1/logout")).status_code == 200
                if change == "revoke":
                    revoke_sessions(s.config, "local")
                if change == "expiry":
                    with session_database(s.config) as db:
                        db.execute("UPDATE sessions SET expires=0")
                if change == "project":
                    s.config["clients"]["local"]["projects"] = []
                if change == "owner":
                    with s.db:
                        s.db.execute("UPDATE jobs SET owner='other' WHERE id='job'")
                release.set()
                response = await pending
                fresh = await c.post(
                    "/v1/effects/" + eid + "/reconcile", json={"decision": "keep_unknown"}
                )
                print(
                    json.dumps(
                        {
                            "change": change,
                            "held_status": response.status_code,
                            "fresh_status": fresh.status_code,
                            "receipt_disclosed": "PRIVATE-SYNTHETIC-12" in response.text,
                            "retained_status": s.effects.get(eid)["status"],
                        }
                    )
                )
                assert (
                    s.effects.get(eid)["status"] == "done"
                )  # Truthful outcome must remain recorded.
                if change == "valid":
                    assert response.status_code == 200
                else:
                    assert fresh.status_code in (401, 403, 404)
                    assert response.status_code in (401, 403, 404), response.text
        finally:
            release.set()
            await s.effects.close()
            s.db.close()

    asyncio.run(run())


@pytest.mark.parametrize("route", ["login", "mutation"])
def test_deep_json_is_controlled(tmp_path, route):
    from starlette.testclient import TestClient
    from test_execution_modes import service

    from agent_service.app import create_app

    instance, identity = service(tmp_path)
    job = instance.submit(
        identity, dict(project_id="p", backend="codex", model="gpt-6-astra", prompt="synthetic")
    )["job_id"]
    app = create_app(instance.config)
    app.state.service.db.close()
    app.state.service = instance
    content = b'{"x":' + b"[" * 10000 + b"0" + b"]" * 10000 + b"}"
    try:
        client = TestClient(app, raise_server_exceptions=False)
        headers = {"Authorization": "Bearer a"}
        before = client.get("/v1/jobs/" + job, headers=headers)
        if route == "login":
            instance.config["origins"] = ["http://testserver"]
            response = client.post(
                "/v1/login", content=content, headers={"Origin": "http://testserver"}
            )
        else:
            response = client.patch(
                "/v1/jobs/" + job + "/work-item", content=content, headers=headers
            )
        after = client.get("/v1/jobs/" + job, headers=headers)
        assert before.status_code == after.status_code == 200
        assert before.json() == after.json()
        assert response.status_code == 422
        assert response.json()["code"] == "invalid_json"
    finally:
        instance.db.close()
