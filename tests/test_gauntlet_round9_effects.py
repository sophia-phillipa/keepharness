import asyncio
import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from test_effect_executor import prepared

from agent_service.errors import APIError


@pytest.mark.parametrize(
    "event_kind",
    [
        "gate_expired",
        "effect_denied",
        "effect_approved",
        "effect_intent",
        "effect_execution",
        "effect_done",
    ],
)
def test_terminal_event_failure_does_not_orphan_effect(make_harness_config, event_kind):

    async def scenario():
        cfg = make_harness_config(
            approval_timeout_seconds=0.02 if event_kind == "gate_expired" else 60
        )
        app, effect = await prepared(lambda: cfg)
        service = app.state.service
        effect_id, gate_id = (effect["effect_id"], effect["gate_id"])
        original = service.message_repository.add_event
        failed = []

        def add_event(job, at, kind, data):
            if kind == event_kind and (not failed):
                failed.append(kind)
                raise OSError("synthetic one-shot event persistence failure")
            return original(job, at, kind, data)

        driver = AsyncMock(return_value=("done", {"issue_key": "TEST-1"}))
        task = service.effects.tasks[effect_id]
        try:
            with (
                patch.object(service.message_repository, "add_event", side_effect=add_event),
                patch.object(service.effects.driver, "create", driver),
            ):
                if event_kind != "gate_expired":
                    service.gates.resolve(
                        gate_id,
                        ("local", {}),
                        {"choice": "deny" if event_kind == "effect_denied" else "approve"},
                    )
                outcome = await asyncio.gather(task, return_exceptions=True)
                await asyncio.sleep(0)
            current = service.effects.get(effect_id)
            reconciliation = None
            if driver.await_count:
                try:
                    await service.effects.reconcile(effect_id, ("local", {}), "keep_unknown")
                except APIError as exc:
                    reconciliation = exc.code
            print(
                "A3-EFF1",
                json.dumps(
                    {
                        "failure_at": event_kind,
                        "status": current["status"],
                        "receipt": current["receipt"],
                        "gate": service.gates.repository.get(gate_id)["state"],
                        "tasks": len(service.effects.tasks),
                        "approval_live": gate_id in service.approvals,
                        "driver_calls": driver.await_count,
                        "wait_result": type(outcome[0]).__name__,
                        "reconciliation_error": reconciliation,
                    }
                ),
            )
            assert failed == [event_kind]
            assert gate_id not in service.approvals and effect_id not in service.effects.tasks
            assert current["status"] not in {"prepared", "executing"}, (
                "Finished publication task stranded a nonterminal durable effect"
            )
            if driver.await_count:
                assert current["status"] in {"done", "unknown"}
        finally:
            await service.effects.close()
            service.db.close()

    asyncio.run(scenario())





def test_successful_jira_receipt_remains_recoverable_after_event_failure(make_harness_config):
    async def scenario():
        app, effect = await prepared(make_harness_config)
        service = app.state.service
        posts = []
        failures = []
        real_client = httpx.AsyncClient
        real_add_event = service.message_repository.add_event

        def handler(req):
            posts.append(
                {"method": req.method, "path": req.url.path, "artifact": json.loads(req.content)}
            )
            return httpx.Response(201, json={"key": "TEST-1"})

        def add_event(job, at, kind, data):
            if kind == "effect_done" and not failures:
                failures.append(kind)
                raise OSError("synthetic one-shot event write failure")
            return real_add_event(job, at, kind, data)

        task = service.effects.tasks[effect["effect_id"]]
        try:
            with (
                patch(
                    "agent_service.jira_effects.httpx.AsyncClient",
                    side_effect=lambda **kw: real_client(
                        **kw, transport=httpx.MockTransport(handler)
                    ),
                ),
                patch.object(service.message_repository, "add_event", side_effect=add_event),
            ):
                service.gates.resolve(effect["gate_id"], ("local", {}), {"choice": "approve"})
                result = await asyncio.gather(task, return_exceptions=True)
                await asyncio.sleep(0)
            current = service.effects.get(effect["effect_id"])
            try:
                await service.effects.reconcile(effect["effect_id"], ("local", {}), "keep_unknown")
                reconcile = "accepted"
            except APIError as exc:
                reconcile = exc.code
            print(
                "A3-EFF1-HTTP",
                json.dumps(
                    {
                        "posts": len(posts),
                        "path": posts[0]["path"],
                        "jira_response": {"status": 201, "key": "TEST-1"},
                        "status": current["status"],
                        "receipt": current["receipt"],
                        "tasks": len(service.effects.tasks),
                        "wait_error": type(result[0]).__name__,
                        "reconciliation": reconcile,
                    }
                ),
            )
            assert len(posts) == 1
            assert posts[0]["artifact"]["fields"]["labels"] == [
                "harness-effect-" + effect["effect_id"]
            ]
            assert failures == ["effect_done"]
            assert current["status"] == "done"
            assert current["receipt"]["issue_key"] == "TEST-1"
        finally:
            await service.effects.close()
            service.db.close()

    asyncio.run(scenario())
