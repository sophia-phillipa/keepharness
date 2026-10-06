"""Activity is live, owner-private and project-scoped when filtering references."""

import asyncio
import time

import pytest
from test_invocation_normalization import invocation_service
from test_work_item_reference import submit

from agent_service.errors import APIError


def test_prepared_effect_activity_uses_one_query_for_all_jobs(make_harness_config):
    from test_effect_executor import prepared

    async def scenario():
        app, effect = await prepared(make_harness_config)
        service = app.state.service
        service.db.execute("UPDATE jobs SET state='completed' WHERE id='job'")
        for index in range(12):
            service.conversation_repository.insert(
                f"finished-{index}", "sem-projeto", "local", "completed", index,
                "{}", None, None, None,
            )
        service.db.commit()
        queries = []
        service.db.set_trace_callback(queries.append)
        activity = service.activity(("local", service.config["clients"]["local"]))
        service.db.set_trace_callback(None)
        assert any(item["gate_id"] == effect["gate_id"] for item in activity["needs_you"])
        effect_queries = [query for query in queries if "FROM effects" in query]
        assert len(effect_queries) == 1
        await service.effects.close()
        service.db.close()

    asyncio.run(scenario())


def test_activity_scopes_owners_projects_and_references(tmp_path, monkeypatch):
    service, identity = invocation_service(tmp_path, monkeypatch)
    first = submit(service, identity, work_item="TASK-1234")
    second = submit(service, identity, work_item="TASK-5678")
    service.conversation_repository.set_running(first)
    service.db.commit()
    activity = service.activity(identity, "p", "TASK-1234")
    assert activity["counts"] == dict(running=1, queued=0, needs_you=0)
    assert [j["job_id"] for j in activity["jobs"]] == [first]
    service.config["clients"]["other"] = {"projects": ["p"]}
    assert service.activity(("other", service.config["clients"]["other"]), "p")["jobs"] == []
    assert len(service.activity(identity)["jobs"]) == 2
    with pytest.raises(APIError, match="work_item_project_required"):
        service.activity(identity, work_item="TASK-1234")
    with pytest.raises(APIError, match="project_denied"):
        service.activity(identity, "forbidden")
    assert second != first
    service.db.close()


def test_live_waiters_only_and_queue_wait_reason(tmp_path, monkeypatch):
    async def scenario():
        service, identity = invocation_service(tmp_path, monkeypatch)
        job = submit(service, identity)
        child = submit(service, identity, parent_job_id=job)
        service.conversation_repository.set_running(job)
        service.db.commit()
        future = asyncio.get_running_loop().create_future()
        service.approvals["pending"] = (job, future)
        service.event(
            job,
            "approval_required",
            {
                "approval_id": "pending",
                "expires_at": time.time() + 60,
                "request": {"command": "test"},
            },
        )
        service.event(
            job, "approval_required", {"approval_id": "stale", "expires_at": time.time() + 60}
        )
        activity = service.activity(identity, "p")
        assert activity["counts"]["needs_you"] == 1
        assert activity["needs_you"][0]["approval_id"] == "pending"
        assert (
            next(j for j in activity["jobs"] if j["job_id"] == child)["wait_reason"]
            == "conversation_parent"
        )
        future.set_result({"approved": True})
        assert service.activity(identity, "p")["counts"]["needs_you"] == 0
        service.db.close()

    asyncio.run(scenario())


def test_expired_gate_is_not_actionable_even_with_live_waiter(tmp_path, monkeypatch):
    async def scenario():
        service, identity = invocation_service(tmp_path, monkeypatch)
        job = submit(service, identity)
        service.conversation_repository.set_running(job)
        future = asyncio.get_running_loop().create_future()
        service.approvals["gate"] = (job, future)
        spec = dict(
            gate_id="gate",
            question="Choose?",
            options=[dict(id="ok", label="OK")],
            timeout_at=time.time() - 1,
        )
        service.gates.repository.create("gate", job, spec)
        service.event(job, "gate_required", spec)
        assert service.activity(identity, "p")["needs_you"] == []
        future.cancel()
        service.db.close()

    asyncio.run(scenario())


def test_activity_routes_auth_and_manual_tag(tmp_path, monkeypatch):
    import httpx
    from starlette.applications import Starlette

    from agent_service.routes.activity import ROUTES

    async def scenario():
        service, identity = invocation_service(tmp_path, monkeypatch)
        job = submit(service, identity)
        app = Starlette(routes=ROUTES)
        app.state.service = service
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            assert (await client.get("/v1/activity")).status_code == 401
            client.headers["Authorization"] = "Bearer a"
            assert (await client.get("/v1/activity?work_item=TASK-1")).status_code == 422
            response = await client.patch(
                "/v1/jobs/" + job + "/work-item", json={"work_item": "TASK-1"}
            )
            assert response.status_code == 200
            assert response.json()["project_id"] == "p"
            assert (await client.get("/v1/activity?project_id=p&work_item=TASK-1")).json()["jobs"][
                0
            ]["job_id"] == job
            client.headers["Authorization"] = "Bearer b"
            assert (await client.get("/v1/activity?project_id=p")).json()["jobs"] == []
            assert (
                await client.patch("/v1/jobs/" + job + "/work-item", json={"work_item": None})
            ).status_code == 404
        service.db.close()

    asyncio.run(scenario())


def test_reference_filter_has_no_cross_project_or_history_limit(tmp_path, monkeypatch):
    service, identity = invocation_service(tmp_path, monkeypatch)
    service.config["projects"]["q"] = {}
    identity[1]["projects"].append("q")
    for index in range(61):
        service.conversation_repository.insert(
            str(index),
            "p" if index < 60 else "q",
            "a",
            "completed",
            index,
            "{}",
            None,
            None,
            None,
            work_item="TASK-1",
        )
    service.db.commit()
    activity = service.activity(identity, "p", "TASK-1")
    assert len(activity["jobs"]) == 60
    assert all(j["project_id"] == "p" for j in activity["jobs"])
    assert len(service.activity(identity, "q", "TASK-1")["jobs"]) == 1
    service.db.close()


def test_provider_state_follows_live_executor_and_preserves_idle_models(tmp_path, monkeypatch):
    monkeypatch.setattr("agent_service.harness_agents.LOCAL_CLIENT", "a")  # quota is owner-only (D-032)
    service, identity = invocation_service(tmp_path, monkeypatch)
    job = submit(service, identity)
    service.conversation_repository.set_running(job)
    service.active_executors[job] = ("local", "installed-model")
    activity = service.activity(identity, "p")
    providers = {p["backend"]: p for p in activity["providers"]}
    assert providers["local"]["state"] == "busy"
    assert providers["codex"]["state"] == "idle"
    # Purposeful D-032 change: a provider without quota says why instead of None.
    assert providers["local"]["quota"] == {"available": False, "reason": "local_no_quota"}
    service.db.close()


def test_gate_requires_both_live_waiter_and_pending_durable_state(tmp_path, monkeypatch):
    async def scenario():
        service, identity = invocation_service(tmp_path, monkeypatch)
        job = submit(service, identity)
        service.conversation_repository.set_running(job)
        future = asyncio.get_running_loop().create_future()
        service.approvals["gate"] = (job, future)
        spec = dict(
            gate_id="gate",
            question="Choose?",
            options=[dict(id="ok", label="OK")],
            timeout_at=time.time() + 60,
        )
        service.gates.repository.create("gate", job, spec)
        service.event(job, "gate_required", spec)
        assert service.activity(identity, "p")["needs_you"][0]["kind"] == "gate"
        service.gates.repository.close("gate", "invalidated")
        assert service.activity(identity, "p")["needs_you"] == []
        future.cancel()
        service.db.close()

    asyncio.run(scenario())


def test_activity_reuses_reported_codex_quota_without_fetching(tmp_path, monkeypatch):
    monkeypatch.setattr("agent_service.harness_agents.LOCAL_CLIENT", "a")  # quota is owner-only (D-032)
    from unittest.mock import AsyncMock

    service, identity = invocation_service(tmp_path, monkeypatch)
    snapshot = {
        "available": True,
        "checked_at": 123456,
        "shared_account": True,
        "rateLimits": {"primary": {"usedPercent": 25, "resetsAt": 123999}},
        "rateLimitsByLimitId": None,
    }
    service.usage_cache = snapshot
    service.usage_at = time.monotonic()  # a quota read is fresh when it was just taken (CDX-R4-2)
    fetch = AsyncMock(side_effect=AssertionError("Activity must not fetch provider quota"))
    monkeypatch.setattr(service, "quota", fetch)
    activity = service.activity(identity, "p")
    provider = next(
        provider for provider in activity["providers"] if provider["backend"] == "codex"
    )
    assert provider["quota"] == snapshot
    assert provider["quota"]["rateLimitsByLimitId"] is None
    fetch.assert_not_called()
    service.config["projects"]["q"] = {}
    identity[1]["projects"].append("q")
    assert service.activity(identity, "q")["providers"] == []
    assert service.activity(("other", {"projects": []}))["providers"] == []
    service.usage_cache = None
    provider = next(
        provider
        for provider in service.activity(identity, "p")["providers"]
        if provider["backend"] == "codex"
    )
    # Purposeful D-032 change: an unread quota is an explicit n/a, no longer None.
    assert provider["quota"]["available"] is False
    assert provider["quota"]["reason"] == "quota_not_read"
    service.db.close()
