"""Deterministic event projection golden scenarios, including old event streams."""

import copy

import pytest

from agent_service.spans import events_to_spans


def event(number, kind, **data):
    return {"id": number, "time": number, "type": kind, "data": data}


def job(state="running", backend="codex"):
    return {"id": "run", "state": state, "created": 0, "payload": {"backend": backend}}


def compact(spans):
    return [
        (s["kind"], s["start_ts"], s["end_ts"], s["status"], s["attrs"]["outcome"]) for s in spans
    ]


def test_single_backend_golden_and_purity():
    rows = [
        event(1, "queued"),
        event(2, "running"),
        event(3, "answer_delta", text="hello"),
        event(4, "completed"),
    ]
    before = copy.deepcopy(rows)
    assert compact(events_to_spans(job("completed"), rows)) == [
        ("invoke_agent", 2, 4, "ok", "completed"),
        ("queue_wait", 1, 2, "ok", "completed"),
        ("chat", 3, 4, "ok", "completed"),
    ]
    assert rows == before


def test_four_step_golden_explicit_terminals():
    rows = [
        event(1, "running"),
        event(2, "maestro_planning", execution_id="plan"),
        event(3, "maestro_plan"),
    ]
    for index in range(1, 5):
        rows += [
            event(
                index * 2 + 2,
                "maestro_step",
                execution_id=f"step-{index}",
                index=index,
                role="review",
            ),
            event(
                index * 2 + 3,
                "maestro_step_completed",
                execution_id=f"step-{index}",
                outcome="completed",
            ),
        ]
    rows.append(event(12, "completed"))
    spans = events_to_spans(job("completed", "maestro"), rows)
    assert compact(spans) == [
        ("invoke_workflow", 1, 12, "ok", "completed"),
        ("plan", 2, 3, "ok", "completed"),
    ] + [("invoke_agent", index * 2 + 2, index * 2 + 3, "ok", "completed") for index in range(1, 5)]
    assert all(s["parent_id"] == "run" for s in spans[1:])


def test_overlapping_tools_and_nested_claude_parent():
    rows = [
        event(1, "running"),
        event(2, "tool_start", tool="Agent", tool_call_id="a"),
        event(3, "tool_start", tool="Read", tool_call_id="b", parent_tool_use_id="a"),
        event(4, "tool_end", tool="Agent", tool_call_id="a", status="completed"),
        event(5, "tool_end", tool="Read", tool_call_id="b", status="failed"),
    ]
    spans = events_to_spans(job(), rows)
    assert compact(spans)[1:] == [
        ("execute_tool", 2, 4, "ok", "completed"),
        ("execute_tool", 3, 5, "error", "failed"),
    ]
    assert spans[2]["parent_id"] == spans[1]["span_id"]


def test_repeated_step_attempts_and_tool_ids_do_not_collide():
    rows = [event(1, "running")]
    for offset, attempt in ((2, 1), (5, 2)):
        execution = f"execution-{attempt}"
        rows += [
            event(offset, "maestro_step", index=1, execution_id=execution, attempt=attempt),
            event(
                offset + 1, "tool_start", tool="Read", tool_call_id="same", execution_id=execution
            ),
            event(offset + 2, "maestro_step_completed", execution_id=execution, outcome="failed"),
        ]
    spans = events_to_spans(job(), rows)
    tools = [s for s in spans if s["kind"] == "execute_tool"]
    assert len(tools) == 2 and tools[0]["parent_id"] != tools[1]["parent_id"]
    assert len({s["span_id"] for s in spans}) == len(spans)


@pytest.mark.parametrize("terminal,status", [("failed", "error"), ("cancelled", "cancelled")])
def test_terminal_closes_open_children(terminal, status):
    spans = events_to_spans(
        job(terminal),
        [event(1, "running"), event(2, "tool_start", tool="Read"), event(3, terminal)],
    )
    assert [(s["end_ts"], s["status"]) for s in spans] == [(3, status), (3, status)]


def test_reconnect_deduplicates_event_ids_and_pending_end_stays_null():
    rows = [event(1, "running"), event(2, "tool_start", tool="Read", tool_call_id="x")]
    assert events_to_spans(job(), rows + rows) == events_to_spans(job(), rows)
    assert all(s["end_ts"] is None for s in events_to_spans(job(), rows))


def test_gate_and_legacy_unknown_outcomes():
    rows = [
        event(1, "running"),
        event(2, "maestro_step", index=1),
        event(3, "approval_required", approval_id="gate"),
        event(4, "approval_resolved", approval_id="gate"),
        event(5, "maestro_step", index=2),
        event(6, "completed"),
    ]
    spans = events_to_spans(job("completed", "maestro"), rows)
    assert [s["attrs"]["outcome"] for s in spans if s["kind"] == "invoke_agent"] == [
        "unknown",
        "unknown",
    ]
    gate = next(s for s in spans if s["kind"] == "harness.gate")
    assert (gate["start_ts"], gate["end_ts"], gate["attrs"]["outcome"]) == (3, 4, "unknown")


def test_content_is_separate_and_unknown_nested_fields_never_become_attrs():
    rows = [
        event(1, "running"),
        event(
            2, "tool_start", tool="Read", input={"private": "secret"}, metadata={"prompt": "hidden"}
        ),
        event(
            3, "tool_end", tool="Read", result={"nested": {"text": "private"}}, status="completed"
        ),
    ]
    spans = events_to_spans(job(), rows)
    tool = spans[1]
    assert "secret" in str(tool["content"])
    assert "secret" not in str(tool["attrs"]) and "private" not in str(tool["attrs"])


def test_native_gate_lifecycle_and_terminal_nested_pending_child():
    rows = [
        event(1, "running"),
        event(2, "tool_start", tool="Agent", tool_call_id="a"),
        event(3, "tool_start", tool="Read", tool_call_id="b", parent_tool_use_id="a"),
        event(4, "tool_end", tool_call_id="a", status="completed"),
        event(5, "gate_required", gate_id="g"),
        event(6, "gate_resolved", gate_id="g"),
        event(7, "cancelled"),
    ]
    spans = events_to_spans(job("cancelled"), rows)
    assert (spans[2]["end_ts"], spans[2]["status"]) == (7, "cancelled")
    assert (spans[3]["end_ts"], spans[3]["status"]) == (6, "ok")


def test_repeated_execution_id_attempts_remain_distinct_and_metrics_map_safely():
    rows = [
        event(1, "maestro_step", execution_id="step", index=1, attempt=1),
        event(2, "maestro_step_completed", execution_id="step", outcome="failed"),
        event(3, "maestro_step", execution_id="step", index=1, attempt=2),
        event(4, "maestro_step_completed", execution_id="step", outcome="completed"),
    ]
    source = {
        **job(),
        "conversation_id": "conversation-root",
        "result": {
            "orchestration": {
                "steps": [
                    {"execution_id": "step", "metrics": {"input_tokens": 12, "prompt": "private"}}
                ]
            }
        },
    }
    spans = events_to_spans(source, rows)
    assert len({s["span_id"] for s in spans}) == 3
    assert spans[0]["attrs"]["gen_ai.conversation.id"] == "conversation-root"
    assert spans[-1]["attrs"]["gen_ai.usage.input_tokens"] == 12
    assert "private" not in str(spans[-1]["attrs"])
