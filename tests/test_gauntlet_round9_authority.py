"Synthetic in-process authority checks, no server sockets or inference."

import asyncio
import hashlib
import json

import httpx
import pytest

from agent_service.app import create_app
from agent_service.approval_sessions import (
    consume_enrollment,
    issue_enrollment,
    session_database,
    token_digest,
)

ORIGIN = "http://testserver"


@pytest.fixture
def app(tmp_path):
    config = {
        "state_dir": str(tmp_path / "state"),
        "local_access": False,
        "origins": [ORIGIN],
        "projects": {"p": {}},
        "services": {},
        "clients": {
            owner: {"sha256": hashlib.sha256(owner.encode()).hexdigest(), "projects": ["p"]}
            for owner in ("alice", "bob")
        },
    }
    instance = create_app(config)
    yield instance
    instance.state.service.db.close()


def client(app, **kw):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN, **kw)


def enroll(app, owner="alice"):
    cfg = app.state.service.config
    return consume_enrollment(cfg, issue_enrollment(cfg, owner))


def pending(app):
    s = app.state.service
    with s.db:
        s.conversation_repository.insert(
            "job", "p", "alice", "running", 1, "{}", None, "job", "job"
        )
    future = asyncio.get_running_loop().create_future()
    s.approvals["approval"] = ("job", future)
    return future


@pytest.mark.parametrize("target", ["deny", "logout"])
def test_worker_cannot_starve_human_control(app, target):

    async def run():
        s = app.state.service
        future = pending(app)
        token = enroll(app)
        async with client(app, headers={"Authorization": "Bearer alice"}) as worker:
            count = 120 if target == "deny" else 60
            statuses = [
                (
                    await worker.post(
                        "/v1/approvals/approval" if target == "deny" else "/v1/approval-rules",
                        json={"approved": True}
                        if target == "deny"
                        else {"conversation_id": "missing"},
                    )
                ).status_code
                for _ in range(count)
            ]
            assert set(statuses) == ({403} if target == "deny" else {404})
        async with client(app, cookies={"harness_session": token}) as human:
            response = await human.post(
                "/v1/approvals/approval" if target == "deny" else "/v1/logout",
                json={"approved": False},
                headers={"Origin": ORIGIN},
            )
        with session_database(s.config, readonly=True) as db:
            active = db.execute(
                "SELECT COUNT(*) FROM sessions WHERE digest=?", (token_digest(token),)
            ).fetchone()[0]
        print(
            json.dumps(
                {
                    "scenario": "A3-AUTH8",
                    "target": target,
                    "worker_statuses": sorted(set(statuses)),
                    "worker_count": count,
                    "human_status": response.status_code,
                    "response": response.json(),
                    "approval_pending": not future.done(),
                    "session_still_active": bool(active),
                }
            )
        )
        assert response.status_code == 200, (
            "Lower-authority rejected traffic blocks human safety control"
        )
        assert future.done() if target == "deny" else not active

    asyncio.run(run())


@pytest.mark.parametrize("target", ["deny", "logout"])
def test_read_burst_preserves_human_control(app, target):

    async def run():
        future = pending(app)
        token = enroll(app)
        async with client(app, cookies={"harness_session": token}) as human:
            statuses = [(await human.get("/v1/conversations")).status_code for _ in range(240)]
            assert set(statuses) == {200}
            response = await human.post(
                "/v1/approvals/approval" if target == "deny" else "/v1/logout",
                json={"approved": False},
            )
        print(
            json.dumps(
                {
                    "scenario": "A3-AUTH9",
                    "target": target,
                    "reads": 240,
                    "human_status": response.status_code,
                    "response": response.json(),
                    "approval_pending": not future.done(),
                }
            )
        )
        assert response.status_code == 200, (
            "Shared session lookup bucket defeats separate control rate lane"
        )

    asyncio.run(run())


def test_human_control_reserve_remains_bounded(app):
    async def run():
        token = enroll(app)
        async with client(app, cookies={"harness_session": token}) as human:
            responses = [
                await human.post("/v1/approvals/missing", json={"approved": False})
                for _ in range(121)
            ]
            assert all(r.status_code == 404 for r in responses[:120])
            assert responses[-1].status_code == 429

    asyncio.run(run())
