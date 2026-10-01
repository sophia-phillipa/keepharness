import asyncio
import json
import sqlite3
from unittest.mock import AsyncMock, patch

import pytest
from test_execution_modes import service as make_service
from test_workflow_resume_rerun import setup_run

from agent_service import maestro, workflows


def assert_declaration_admitted(service, row, data, plan):
    available = maestro.candidates(
        service.config, row["project"], execution_mode=data.get("execution_mode")
    )
    normalized = workflows.validate_workflow(plan, available)
    admitted = maestro.validate_plan(json.dumps(normalized), available, declared=True)
    assert len(admitted["steps"]) == len(plan["steps"])
    print(
        "DECLARATION_ADMITTED",
        json.dumps(
            {
                "steps": len(admitted["steps"]),
                "outputs": admitted["steps"][0].get("outputs"),
                "condition": admitted["steps"][-1].get("condition"),
            }
        ),
    )


def test_completion_lock_does_not_strand_parent_and_continuation(tmp_path):
    async def scenario():
        service, identity = make_service(tmp_path)
        job = service.submit(
            identity, dict(project_id="p", backend="codex", model="gpt-6-astra", prompt="first")
        )["job_id"]
        child = service.submit(
            identity,
            dict(
                project_id="p",
                backend="codex",
                model="gpt-6-astra",
                prompt="continue",
                parent_job_id=job,
            ),
        )["job_id"]
        service.db.execute("PRAGMA busy_timeout=5")
        blocker = sqlite3.connect(service.db.execute("PRAGMA database_list").fetchone()[2])
        called = []
        acquired = asyncio.Event()

        async def execute(row):
            called.append(row["id"])
            if row["id"] == job:
                blocker.execute("BEGIN IMMEDIATE")
                acquired.set()
            return {"answer": "synthetic successful result"}

        service.execute = execute
        service.quota = AsyncMock(return_value={})
        worker = asyncio.create_task(service.worker())
        try:
            await asyncio.wait_for(acquired.wait(), 1)
            await asyncio.sleep(0.05)
            blocker.rollback()
            service.wake.set()
            await asyncio.sleep(0.20)
            observed = {
                "parent_state": service.conversation_repository.state(job)[0],
                "child_state": service.conversation_repository.state(child)[0],
                "result": service.job(identity, job)["result"],
                "active_tasks": len(service.job_tasks),
                "leases": len(service.write_ownership.leases),
                "worker_done": worker.done(),
                "inference_calls": len(called),
            }
            print("COMPLETION_LOCK", json.dumps(observed))
            assert observed["parent_state"] == "completed", observed
            assert json.loads(observed["result"])["answer"] == "synthetic successful result"
            assert called == [job, child]
            assert observed["active_tasks"] == observed["leases"] == 0
            assert observed["child_state"] == "completed", observed
        finally:
            blocker.rollback()
            blocker.close()
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            service.db.close()

    asyncio.run(scenario())


def test_nullable_enum_property_requires_output_review(tmp_path):
    service, identity, row, data, plan = setup_run(tmp_path)
    plan["steps"] = plan["steps"][:1]
    # A valid JSON Schema: the only valid verdict is the string "pass".
    plan["steps"][0]["outputs"] = {
        "type": "object",
        "properties": {"verdict": {"enum": ["pass"]}},
        "required": ["verdict"],
    }
    assert_declaration_admitted(service, row, data, plan)
    result = {"answer": '```harness-result\n{"verdict":"pass"}\n```'}
    assert_declaration_admitted(service, row, data, plan)
    try:
        with (
            patch.object(service, "infer", AsyncMock(return_value=result)),
            patch.object(
                service.gates, "ask", AsyncMock(return_value={"approved": False, "choice": "deny"})
            ) as gate,
        ):
            asyncio.run(maestro.execute_plan(service, row, data, plan))
            assert gate.await_count == 0
        print("ENUM_VALID_CONTROL", True)
        result["answer"] = '```harness-result\n{"verdict":null}\n```'
        # This must go to review, so returning deny must terminate the step.
        with (
            patch.object(service, "infer", AsyncMock(return_value=result)),
            patch.object(
                service.gates, "ask", AsyncMock(return_value={"approved": False, "choice": "deny"})
            ) as gate,
        ):
            try:
                asyncio.run(maestro.execute_plan(service, row, data, plan))
            except Exception as error:
                print("ENUM_NULL_REVIEW", type(error).__name__, str(error), gate.await_count)
            assert gate.await_count == 1
    finally:
        service.db.close()


def test_nested_enum_boolean_is_not_numeric(tmp_path):
    service, identity, row, data, plan = setup_run(tmp_path)
    plan["steps"] = plan["steps"][:1]
    plan["steps"][0]["outputs"] = {
        "type": "object",
        "properties": {"verdict": {"enum": [{"ok": True}]}},
        "required": ["verdict"],
    }
    assert_declaration_admitted(service, row, data, plan)
    try:
        with (
            patch.object(
                service,
                "infer",
                AsyncMock(return_value={"answer": '```harness-result\n{"verdict":{"ok":1}}\n```'}),
            ),
            patch.object(
                service.gates, "ask", AsyncMock(return_value={"approved": False, "choice": "deny"})
            ) as gate,
        ):
            try:
                result = asyncio.run(maestro.execute_plan(service, row, data, plan))
                print(
                    "NESTED_ENUM",
                    json.dumps({"gate_calls": gate.await_count, "result": result["answer"]}),
                )
            except Exception as error:
                print("NESTED_ENUM_REVIEW", type(error).__name__, str(error), gate.await_count)
            assert gate.await_count == 1, (
                "Invalid nested boolean enum was accepted without required output review"
            )
    finally:
        service.db.close()


def test_nested_condition_boolean_is_not_numeric(tmp_path):
    service, identity, row, data, plan = setup_run(tmp_path)
    plan["steps"][0]["id"] = "inspect"
    plan["steps"][1]["condition"] = {"from": "inspect.decision", "equals": {"ok": True}}
    assert_declaration_admitted(service, row, data, plan)
    try:
        with patch.object(
            service,
            "infer",
            AsyncMock(return_value={"answer": '```harness-result\n{"decision":{"ok":1}}\n```'}),
        ) as infer:
            result = asyncio.run(maestro.execute_plan(service, row, data, plan))
        observed = {
            "infer_count": infer.await_count,
            "step_two_outcome": result["orchestration"]["steps"][1]["outcome"],
        }
        print("NESTED_CONDITION", json.dumps(observed))
        assert infer.await_count == 1, (
            "Boolean true and numeric 1 are distinct JSON values; condition should skip step 2"
        )
        assert observed["step_two_outcome"] == "skipped"
    finally:
        service.db.close()


def test_valid_null_enum_is_accepted_without_review(tmp_path):
    service, identity, row, data, plan = setup_run(tmp_path)
    plan["steps"] = plan["steps"][:1]
    plan["steps"][0]["outputs"] = {
        "type": "object",
        "properties": {"verdict": {"enum": ["pass", None]}},
        "required": ["verdict"],
    }
    assert_declaration_admitted(service, row, data, plan)
    try:
        with (
            patch.object(
                service,
                "infer",
                AsyncMock(return_value={"answer": '```harness-result\n{"verdict":null}\n```'}),
            ),
            patch.object(
                service.gates,
                "ask",
                AsyncMock(return_value={"approved": True, "choice": "approve"}),
            ) as gate,
        ):
            asyncio.run(maestro.execute_plan(service, row, data, plan))
        print("VALID_NULL_ENUM", json.dumps({"gate_calls": gate.await_count}))
        assert gate.await_count == 0, "Explicitly enumerated null is valid without a type keyword"
    finally:
        service.db.close()


@pytest.mark.parametrize(
    "value,expected,equal",
    [
        ({"ok": True}, {"ok": 1}, False),
        ([{"ok": False}], [{"ok": 0}], False),
        ({"ok": [True, False]}, {"ok": [True, False]}, True),
        ([1, 2], [1], False),
        ({"ok": None}, {"ok": None}, True),
    ],
)
def test_nested_json_contract(value, expected, equal):
    assert workflows.validate_result(value, {"enum": [expected]}) is equal
    assert (
        workflows.evaluate_condition(
            {"from": "inspect.value", "equals": expected},
            {"inspect": "```harness-result\n" + json.dumps({"value": value}) + "\n```"},
        )
        is equal
    )


@pytest.mark.parametrize(
    "schema,valid",
    [
        ({"enum": ["pass", None]}, True),
        ({"enum": ["pass"]}, False),
        ({"type": "null"}, True),
        ({"type": "string", "enum": [None]}, False),
        ({"type": "null", "enum": ["pass"]}, False),
    ],
)
def test_null_enum_array_items(schema, valid):
    assert workflows.validate_result([None], {"type": "array", "items": schema}) is valid


def test_completion_lock_shutdown_does_not_relabel_success(tmp_path):
    async def scenario():
        service, identity = make_service(tmp_path)
        job = service.submit(
            identity, dict(project_id="p", backend="codex", model="gpt-6-astra", prompt="first")
        )["job_id"]
        service.db.execute("PRAGMA busy_timeout=5")
        blocker = sqlite3.connect(service.db.execute("PRAGMA database_list").fetchone()[2])
        completed = asyncio.Event()

        async def execute(row):
            blocker.execute("BEGIN IMMEDIATE")
            completed.set()
            return {"answer": "Known success"}

        service.execute = execute
        service.quota = AsyncMock(return_value={})
        worker = asyncio.create_task(service.worker())
        try:
            await asyncio.wait_for(completed.wait(), 1)
            await asyncio.sleep(0.02)
            worker.cancel()
            await asyncio.wait_for(asyncio.gather(worker, return_exceptions=True), 1)
            blocker.rollback()
            assert service.conversation_repository.state(job)[0] == "running"
            assert not service.job_tasks
            assert not service.write_ownership.leases
        finally:
            blocker.close()
            service.db.close()

    asyncio.run(scenario())
