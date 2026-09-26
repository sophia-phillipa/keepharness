"""Admission and queue limits under concurrency: P5-04, P5-05, P5-06.

In-process ASGI against ``create_app``; no lifespan worker is started, so jobs stay
``queued`` and every assertion is about admission, not execution. Mirrors the
config recipe already used by ``test_multiuser_limits.py``: backend ``local`` only,
so no cloud account is touched.
"""

import asyncio
import hashlib

import httpx

from agent_service.app import create_app


def _config(tmp_path, client_names):
    return {
        "state_dir": str(tmp_path),
        "projects": {"p": {}},
        "clients": {
            name: {"sha256": hashlib.sha256(name.encode()).hexdigest(), "projects": ["p"]}
            for name in client_names
        },
        "services": {
            "local": {
                "enabled": True,
                "models": ["installed-model"],
                "projects": ["p"],
                "permissions": {"read": True, "upload": True},
            }
        },
        "origins": [],
    }


def _payload(**overrides):
    payload = {"project_id": "p", "backend": "local", "model": "installed-model", "prompt": "hi"}
    payload.update(overrides)
    return payload


def test_two_tabs_racing_the_same_continuation_admit_exactly_one(tmp_path):
    """Two tabs of the same identity post a continuation of the same parent at once."""
    cfg = _config(tmp_path, ["a", "b"])
    app = create_app(cfg)
    service = app.state.service

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers_a = {"Authorization": "Bearer a"}
            root = await client.post("/v1/jobs", json=_payload(), headers=headers_a)
            assert root.status_code == 202, root.text
            root_id = root.json()["job_id"]
            with service.db:
                service.db.execute("UPDATE jobs SET state='completed' WHERE id=?", (root_id,))

            async def continuation(sequence):
                return await client.post(
                    "/v1/jobs",
                    json=_payload(parent_job_id=root_id, prompt=f"continue-{sequence}"),
                    headers=headers_a,
                )

            first, second = await asyncio.gather(continuation(1), continuation(2))
            statuses = sorted([first.status_code, second.status_code])
            assert statuses == [202, 409], (first.text, second.text)
            loser = first if first.status_code == 409 else second
            assert loser.json()["code"] == "conversation_has_newer_turn"

            # A different identity reusing the same parent never gets to continue it.
            other = await client.post(
                "/v1/jobs",
                json=_payload(parent_job_id=root_id, prompt="not-mine"),
                headers={"Authorization": "Bearer b"},
            )
            assert 400 <= other.status_code < 500, other.text
            # ``self.job()`` enforces ownership before the continuation checks run, so
            # the code is the ownership-denial one rather than a generic parent error;
            # either way the cross-owner continuation is rejected.
            assert other.json()["code"] == "job_owner_denied"

    asyncio.run(scenario())


def test_owner_queue_full_then_global_queue_full(tmp_path):
    """A, B and C each submit 11 jobs; D then submits 3 on top of the shared queue."""
    cfg = _config(tmp_path, ["a", "b", "c", "d"])
    app = create_app(cfg)
    service = app.state.service

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:

            async def submit(owner, sequence):
                response = await client.post(
                    "/v1/jobs",
                    json=_payload(prompt=f"{owner}-{sequence}"),
                    headers={"Authorization": "Bearer " + owner},
                )
                return owner, response

            for owner in ("a", "b", "c"):
                responses = [await submit(owner, n) for n in range(11)]
                codes = [response.status_code for _, response in responses]
                assert codes[:10] == [202] * 10, codes
                assert codes[10] == 429, codes
                assert responses[10][1].json()["code"] == "owner_queue_full"
                retry_after = int(responses[10][1].headers["Retry-After"])
                assert retry_after >= 1

            assert service.conversation_repository.count_pending() == 30

            d_responses = [await submit("d", n) for n in range(3)]
            d_codes = [response.status_code for _, response in d_responses]
            assert d_codes == [202, 202, 429], d_codes
            assert d_responses[2][1].json()["code"] == "queue_full"
            assert int(d_responses[2][1].headers["Retry-After"]) >= 1

    asyncio.run(scenario())


def test_cancel_frees_queue_capacity_but_submission_rate_limit_persists(tmp_path):
    """A cancels 3 queued jobs, then submits 3 more; the rate limit outlives the cancellations."""
    cfg = _config(tmp_path, ["a"])
    app = create_app(cfg)
    service = app.state.service

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = {"Authorization": "Bearer a"}

            async def submit(sequence):
                return await client.post(
                    "/v1/jobs", json=_payload(prompt=f"first-{sequence}"), headers=headers
                )

            # Fill the owner queue to 10, stamping 10 submission-rate entries.
            first_batch = [await submit(n) for n in range(10)]
            assert [r.status_code for r in first_batch] == [202] * 10
            job_ids = [r.json()["job_id"] for r in first_batch]

            for jid in job_ids[:3]:
                cancelled = await client.post("/v1/jobs/" + jid + "/cancel", headers=headers)
                assert cancelled.status_code == 200, cancelled.text
            assert service.conversation_repository.count_pending_for_owner("a") == 7

            async def submit_more(sequence):
                return await client.post(
                    "/v1/jobs", json=_payload(prompt=f"second-{sequence}"), headers=headers
                )

            second_batch = [await submit_more(n) for n in range(3)]
            codes = [r.status_code for r in second_batch]
            assert codes == [202, 202, 429], codes
            assert second_batch[2].json()["code"] == "submission_rate_limit"
            assert int(second_batch[2].headers["Retry-After"]) >= 1

    asyncio.run(scenario())
