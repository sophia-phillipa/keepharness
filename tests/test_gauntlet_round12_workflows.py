import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from test_workflow_resume_rerun import setup_run

from agent_service import maestro, workflows


@pytest.mark.parametrize("value,expected", [(None, None), (None, "ready"), ("ready", "ready")])
def test_explicit_null_condition_uses_value_not_missing(tmp_path, value, expected):
    s, ident, row, data, plan = setup_run(tmp_path)
    plan["steps"][0]["id"] = "inspect"
    plan["steps"][1]["condition"] = {"from": "inspect.status", "equals": expected}
    s.config["approval_timeout_seconds"] = 0.03
    text = "```harness-result\n" + json.dumps({"status": value}) + "\n```"
    infer = AsyncMock(side_effect=[{"answer": text}, {"answer": "second ran"}])
    error = None
    try:
        with patch.object(s, "infer", infer):
            try:
                asyncio.run(maestro.execute_plan(s, row, data, plan))
            except Exception as exc:
                error = str(exc)
        gates = [dict(g) for g in s.gates.repository.for_job(row["id"])]
        print(
            "NULL_CONDITION",
            json.dumps(
                {
                    "value": value,
                    "expected": expected,
                    "gates": len(gates),
                    "inferences": infer.await_count,
                    "error": error,
                }
            ),
        )
        assert error is None
        assert len(gates) == 0, "Explicit JSON null is present valid data, not a missing result"
        assert infer.await_count == (2 if value == expected else 1)
    finally:
        s.db.close()


@pytest.mark.parametrize("wrapper", ["list", "plain"])
def test_result_example_is_not_executable_condition(tmp_path, wrapper):
    s, ident, row, data, plan = setup_run(tmp_path)
    plan["steps"][0]["id"] = "inspect"
    plan["steps"][1]["condition"] = {"from": "inspect.ok", "is": True}
    s.config["approval_timeout_seconds"] = 0.03
    text = (
        '- ````markdown\n  ```harness-result\n  {"ok":true}\n  ```\n  ````'
        if wrapper == "list"
        else '````markdown\n```harness-result\n{"ok":true}\n```\n````'
    )
    infer = AsyncMock(side_effect=[{"answer": text}, {"answer": "second ran from example"}])
    error = None
    try:
        with patch.object(s, "infer", infer):
            try:
                asyncio.run(maestro.execute_plan(s, row, data, plan))
            except Exception as exc:
                error = str(exc)
        gates = [dict(g) for g in s.gates.repository.for_job(row["id"])]
        print(
            "EXAMPLE_CONDITION",
            json.dumps(
                {
                    "wrapper": wrapper,
                    "parsed": workflows.parse_result(text),
                    "inferences": infer.await_count,
                    "gates": len(gates),
                    "error": error,
                }
            ),
        )
        assert infer.await_count == 1, (
            "Code example must require review, never start a conditional step"
        )
        assert len(gates) == 1
    finally:
        s.db.close()


@pytest.mark.parametrize("schema", [{"enum": [1]}, {"type": "integer"}])
def test_integral_decimal_result_matches_json_schema(tmp_path, schema):
    s, ident, row, data, plan = setup_run(tmp_path)
    plan["steps"] = plan["steps"][:1]
    plan["steps"][0]["outputs"] = {
        "type": "object",
        "properties": {"count": schema},
        "required": ["count"],
    }
    s.config["approval_timeout_seconds"] = 0.03
    error = None
    try:
        with patch.object(
            s, "infer", AsyncMock(return_value={"answer": '```harness-result\n{"count":1.0}\n```'})
        ):
            try:
                asyncio.run(maestro.execute_plan(s, row, data, plan))
            except Exception as exc:
                error = str(exc)
        gates = s.gates.repository.for_job(row["id"])
        print("DECIMAL_SCHEMA", json.dumps({"schema": schema, "gates": len(gates), "error": error}))
        assert error is None
        assert len(gates) == 0, "JSON Schema integral decimal must satisfy integer and numeric enum"
    finally:
        s.db.close()


def test_condition_numeric_representation_does_not_change_branch(tmp_path):
    s, ident, row, data, plan = setup_run(tmp_path)
    plan["steps"][0]["id"] = "inspect"
    plan["steps"][1]["condition"] = {"from": "inspect.count", "equals": 1}
    infer = AsyncMock(
        side_effect=[{"answer": '```harness-result\n{"count":1.0}\n```'}, {"answer": "second ran"}]
    )
    try:
        with patch.object(s, "infer", infer):
            result = asyncio.run(maestro.execute_plan(s, row, data, plan))
        print(
            "DECIMAL_CONDITION",
            json.dumps(
                {
                    "inferences": infer.await_count,
                    "last_outcome": result["orchestration"]["steps"][-1]["outcome"],
                }
            ),
        )
        assert infer.await_count == 2, "Equivalent JSON numbers must choose same condition branch"
    finally:
        s.db.close()


@pytest.mark.parametrize(
    "value, expected, equal", [(1.0, 1, True), (1e0, 1, True), (True, 1, False), (1.5, 1, False)]
)
def test_nested_numeric_equality(value, expected, equal):
    assert workflows.json_equal({"items": [value]}, {"items": [expected]}) is equal
    assert workflows.validate_result({"items": [value]}, {"enum": [{"items": [expected]}]}) is equal
    assert workflows.validate_result(value, {"type": "integer"}) is (
        type(value) is not bool and float(value).is_integer()
    )


@pytest.mark.parametrize(
    "output, expected", [({"nested": {"leaf": None}}, True), ({"nested": {}}, None), ({}, None)]
)
def test_null_leaf_and_missing_path_are_distinct(output, expected):
    text = "```harness-result\n" + json.dumps(output) + "\n```"
    assert (
        workflows.evaluate_condition(
            {"from": "inspect.nested.leaf", "equals": None}, {"inspect": text}
        )
        is expected
    )


@pytest.mark.parametrize("prefix, continuation", [("- ", "  "), ("- > ", "  > "), ("> ", "> ")])
def test_result_container_examples_are_literal(prefix, continuation):
    valid = '```harness-result\n{"ok":true}\n```'
    example = (
        prefix
        + "````markdown\n"
        + "\n".join(continuation + line for line in valid.splitlines())
        + "\n"
        + continuation
        + "````"
    )
    assert workflows.parse_result(example) is None
    assert workflows.parse_result(example + '\n```harness-result\n{"ok":false}\n```') == {
        "ok": False
    }


@pytest.mark.parametrize(
    "text", [None, "not structured JSON", "```harness-result\n{}\n```\n```harness-result\n{}\n```"]
)
def test_null_root_condition_requires_a_valid_result(text):
    assert (
        workflows.evaluate_condition({"from": "inspect", "equals": None}, {"inspect": text}) is None
    )


@pytest.mark.parametrize("lines, accepted", [(7000, True), (9000, False)])
def test_result_limit_counts_original_crlf_bytes(lines, accepted):
    content = "\r\n" * lines + '{"ok":true}\r\n'
    result = workflows.parse_result("```harness-result\r\n" + content + "```\r\n")
    assert result == ({"ok": True} if accepted else None)
