import json

import pytest

from agent_service import workflows


def workflow():
    return {
        "version": 1,
        "id": "review",
        "steps": [
            {
                "id": "draft",
                "kind": "builtin",
                "resource_id": "builtin/roles/writer",
                "args": "  Keep\nbytes ",
                "backend": "codex",
                "model": "m",
                "effort": "low",
            }
        ],
    }


def test_canonical_schema_preserves_arguments_and_normalizes_execution():
    result = workflows.validate_workflow(workflow())
    step = result["steps"][0]
    assert step["invocation"]["args"] == "  Keep\nbytes "
    assert step["task"] == "  Keep\nbytes "
    assert step["invocation"]["order"] == 0
    assert len(result["digest"]) == 64


@pytest.mark.parametrize(
    "change", [{"parallel": []}, {"repeat": 2}, {"version": 2}, {"steps": []}, {"steps": [{}] * 13}]
)
def test_rejects_unsupported_workflow_shapes(change):
    with pytest.raises(workflows.WorkflowError):
        workflows.validate_workflow({**workflow(), **change})


def test_json_load_and_yaml_optional(tmp_path):
    path = tmp_path / "review.json"
    path.write_text(json.dumps(workflow()))
    assert workflows.load_workflow(path)["id"] == "review"
    path.write_text("{")
    with pytest.raises(workflows.WorkflowError):
        workflows.load_workflow(path)


@pytest.mark.parametrize(
    "text",
    [
        "no result",
        "```harness-result\n{bad}\n```",
        "```harness-result\n[]\n```",
        '```harness-result\n{"v":"' + "x" * 16384 + '"}\n```',
    ],
)
def test_missing_invalid_or_oversize_result_asks_gate(text):
    assert workflows.evaluate_condition({"from": "draft.ok", "is": True}, {"draft": text}) is None


def test_condition_reads_only_one_bounded_fenced_result():
    outputs = {"draft": 'untrusted prose\n```harness-result\n{"ok":true,"status":"ready"}\n```'}
    assert workflows.evaluate_condition({"from": "draft.ok", "is": True}, outputs) is True
    assert (
        workflows.evaluate_condition({"from": "draft.status", "equals": "other"}, outputs) is False
    )
    assert workflows.evaluate_condition({"from": "draft.missing", "is": True}, outputs) is None
    outputs["draft"] += '\n```harness-result\n{"ok":false}\n```'
    assert workflows.parse_result(outputs["draft"]) is None
