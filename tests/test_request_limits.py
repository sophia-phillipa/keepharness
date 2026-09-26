"""Bursts use ASGI and a temporary database: no worker, CLI or GPU is started."""

import asyncio
from collections import Counter
from unittest.mock import patch

import httpx
import pytest
from test_workspaces import config

from agent_service.app import APIError, create_app


def test_same_identity_burst_keeps_control_and_other_identity_available(tmp_path):
    app = create_app(config(tmp_path))

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            payload = {
                "project_id": "p",
                "backend": "local",
                "model": "installed-model",
                "prompt": "bounded test",
            }
            responses = await asyncio.gather(
                *(
                    client.post(
                        "/v1/jobs",
                        json=payload,
                        headers={
                            "Authorization": "Bearer a",
                            "Idempotency-Key": str(i),
                            "X-Forwarded-For": f"192.0.2.{i % 255}",
                        },
                    )
                    for i in range(500)
                )
            )
            counts = Counter(r.status_code for r in responses)
            assert counts == {202: 10, 429: 490}, counts
            assert all(
                int(r.headers["Retry-After"]) >= 1 for r in responses if r.status_code == 429
            )
            assert app.state.service.db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 10
            job = next(r.json()["job_id"] for r in responses if r.status_code == 202)
            assert (
                await client.get("/v1/jobs/" + job, headers={"Authorization": "Bearer a"})
            ).status_code == 200
            assert (
                await client.post(
                    "/v1/jobs/" + job + "/cancel", headers={"Authorization": "Bearer a"}
                )
            ).status_code == 200
            assert (
                await client.post("/v1/jobs", json=payload, headers={"Authorization": "Bearer b"})
            ).status_code == 202
            print("Burst:", dict(counts), "status/cancel/second identity: available")

    try:
        asyncio.run(scenario())
    finally:
        app.state.service.db.close()


def test_limiter_recovery_and_bounded_keys(tmp_path):
    service = create_app(config(tmp_path)).state.service
    try:
        with patch("agent_service.app.time.monotonic", return_value=100):
            for _ in range(12):
                service.limit(("a", "submission"), 12, "submission_rate_limit")
            with pytest.raises(APIError) as exc:
                service.limit(("a", "submission"), 12, "submission_rate_limit")
            assert exc.value.retry_after == 60
        with patch("agent_service.app.time.monotonic", return_value=161):
            service.limit(("a", "submission"), 12)
        assert service.requests == {("a", "submission"): [161]}
    finally:
        service.db.close()


def test_login_burst_limited_before_body_and_forwarded_headers_cannot_evade(tmp_path):
    app = create_app(config(tmp_path))

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            results = await asyncio.gather(
                *(
                    client.post(
                        "/v1/login", json={"token": "bad"}, headers={"X-Forwarded-For": str(i)}
                    )
                    for i in range(300)
                )
            )
            assert Counter(r.status_code for r in results) == {401: 20, 429: 280}

    try:
        asyncio.run(scenario())
    finally:
        app.state.service.db.close()


def test_stream_reservation_released_even_when_send_fails():
    from agent_service.app import LimitedStream

    released = []

    async def events():
        yield "data: test\n\n"

    async def receive():
        return {"type": "http.disconnect"}

    async def send(message):
        raise OSError("disconnected")

    async def scenario():
        response = LimitedStream(events(), release=lambda: released.append(True))
        with pytest.raises(Exception):
            await response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)

    asyncio.run(scenario())
    assert released == [True]


def test_stream_slots_reject_and_recover(tmp_path):
    app = create_app(config(tmp_path))
    service = app.state.service
    identity = ("a", service.config["clients"]["a"])
    job = service.submit(
        identity,
        {"project_id": "p", "backend": "local", "model": "installed-model", "prompt": "test"},
    )["job_id"]
    service.finish(job, "completed", {"answer": "fixture"})

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": "Bearer a"},
        ) as client:
            service.streams["a"] = 4
            assert (await client.get("/v1/jobs/" + job + "/events")).status_code == 429
            service.streams["a"] = 0
            assert (await client.get("/v1/jobs/" + job + "/events")).status_code == 200
            assert service.streams["a"] == 0

    try:
        asyncio.run(scenario())
    finally:
        service.db.close()


def test_fair_queue_rotates_people_and_keeps_fifo(tmp_path):
    app = create_app(config(tmp_path))
    service = app.state.service
    try:
        ids = {}
        for owner in ("a", "b"):
            identity = (owner, service.config["clients"][owner])
            ids[owner] = [
                service.submit(
                    identity,
                    {
                        "project_id": "p",
                        "backend": "local",
                        "model": "installed-model",
                        "prompt": str(i),
                    },
                )["job_id"]
                for i in range(3)
            ]
        order = []
        for _ in range(6):
            row = service.next_job()
            order.append(row["id"])
            service.finish(row["id"], "completed", {})
        assert order == [ids[owner][i] for i in range(3) for owner in ("a", "b")]
    finally:
        service.db.close()
