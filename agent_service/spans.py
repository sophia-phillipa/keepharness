"""Pure, read-time projection of the append-only job event stream.

Operation names follow the OTel GenAI vocabulary (development conventions).
This is a local JSON contract, not an OTLP exporter. Content lives separately from
allowlisted attributes so callers can omit it without recursively guessing keys.
"""

import json

TERMINAL = frozenset({"completed", "failed", "cancelled", "interrupted"})
ATTRIBUTE_NAMES = {
    "backend": "gen_ai.provider.name",
    "model": "gen_ai.request.model",
    "role": "gen_ai.agent.name",
    "tool": "gen_ai.tool.name",
    "tool_call_id": "gen_ai.tool.call.id",
    "effort": "effort",
    "execution_id": "execution_id",
    "attempt": "attempt",
    "parent_execution_id": "parent_execution_id",
    "index": "index",
    "work_item": "work_item",
    "input_tokens": "gen_ai.usage.input_tokens",
    "output_tokens": "gen_ai.usage.output_tokens",
    "total_tokens": "gen_ai.usage.total_tokens",
    "command_name": "command_name",
    "gate_id": "gate_id",
    "approval_id": "approval_id",
    "inference_seconds": "inference_seconds",
    "cost_usd": "cost_usd",
    "error_type": "error.type",
}


def _object(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return {}
    return dict(value) if value is not None and hasattr(value, "keys") else {}


def _attrs(data):
    return {
        target: data[key]
        for key, target in ATTRIBUTE_NAMES.items()
        if key in data and (data[key] is None or type(data[key]) in (str, int, float, bool))
    }


def _outcome(data, fallback="unknown"):
    value = data.get("outcome") or data.get("status") or fallback
    return {
        "done": "completed",
        "success": "completed",
        "succeeded": "completed",
        "ok": "completed",
        "error": "failed",
        "canceled": "cancelled",
    }.get(value, value)


def _status(outcome):
    return {
        "completed": "ok",
        "failed": "error",
        "interrupted": "error",
        "cancelled": "cancelled",
    }.get(outcome, "unset")


def events_to_spans(job, events):
    """Return deterministic spans, retaining unavailable legacy outcomes as unknown.

    Accept repository rows or SSE envelopes. Replayed row IDs are deduplicated.
    A live span has no end timestamp; inferred legacy boundaries never imply success.
    Raw event data is available only under ``content``; ``attrs`` is an allowlist.
    """
    job = _object(job)
    payload = _object(job.get("payload"))
    trace_id = job["id"]
    rows, seen = [], set()
    for position, row in enumerate(events, 1):
        row = _object(row)
        identifier = row.get("id", position)
        if identifier in seen:
            continue
        seen.add(identifier)
        rows.append(
            (
                identifier,
                row.get("time", row.get("timestamp")),
                row.get("type"),
                _object(row.get("data")),
            )
        )
    spans, by_id, executions, stages, tools, gates, chats = [], {}, {}, {}, {}, {}, {}

    def create(identifier, kind, name, start, parent, data):
        if identifier in by_id:
            identifier = f"{identifier}:occurrence:{len(spans)}"
        span = {
            "span_id": identifier,
            "trace_id": trace_id,
            "parent_id": parent,
            "kind": kind,
            "name": name,
            "start_ts": start,
            "end_ts": None,
            "status": "unset",
            "attrs": {"gen_ai.operation.name": kind, "outcome": "unknown", **_attrs(data)},
            "events": [],
            "content": [],
        }
        spans.append(span)
        by_id[identifier] = span
        return span

    def close(span, timestamp, outcome="unknown", cascade=False):
        if span["end_ts"] is None:
            span["end_ts"] = timestamp
            span["attrs"]["outcome"] = outcome
            span["status"] = _status(outcome)
        if cascade:
            for child in spans:
                if child["parent_id"] == span["span_id"]:
                    # Missing tool/stage terminal events do not prove success.
                    inherited = (
                        outcome
                        if child["kind"] == "chat"
                        or outcome in {"failed", "cancelled", "interrupted"}
                        else "unknown"
                    )
                    close(child, timestamp, inherited, cascade=True)

    workflow = payload.get("backend") == "maestro" or any(
        kind.startswith("maestro_") for _, _, kind, _ in rows if kind
    )
    started = next((ts for _, ts, kind, _ in rows if kind == "running"), None)
    root = create(
        trace_id, "invoke_workflow" if workflow else "invoke_agent", "Run", started, None, payload
    )
    root["attrs"]["gen_ai.conversation.id"] = (
        job.get("conversation_id")
        or payload.get("conversation_id")
        or (trace_id if not payload.get("parent_job_id") else None)
    )
    root["content"].append({"request": payload})
    executions[trace_id] = root
    active_stage = root
    plan_span = None
    queue = None

    for identifier, timestamp, kind, data in rows:
        execution = executions.get(data.get("execution_id"))
        stage = stages.get(str(data.get("maestro_stage")))
        parent = (
            execution
            if execution is not None and execution is not root
            else stage or execution or active_stage
        )
        scope = parent["span_id"]
        tool_id = data.get("tool_call_id") or data.get("tool_id")
        parent_tool = tools.get((scope, data.get("parent_tool_use_id")))
        if parent_tool is not None:
            parent = parent_tool
        event_content = {"ts": timestamp, "name": kind, "data": data}
        if kind == "queued":
            queue = create(
                f"{trace_id}:queue:{identifier}", "queue_wait", "Queue", timestamp, trace_id, data
            )
        elif kind == "running":
            if queue is not None:
                close(queue, timestamp, "completed")
        elif kind == "maestro_planning":
            execution_id = data.get("execution_id")
            if not execution_id or execution_id == trace_id:
                execution_id = f"{trace_id}:plan:{identifier}"
            plan_span = create(execution_id, "plan", "Plan", timestamp, trace_id, data)
            executions[execution_id] = stages["plan"] = plan_span
            active_stage = plan_span
        elif kind in {"maestro_plan", "maestro_planning_completed"}:
            if plan_span is not None:
                close(plan_span, timestamp, _outcome(data, "completed"), cascade=True)
                plan_span["content"].append(event_content)
            active_stage = root
        elif kind == "maestro_step":
            if active_stage is not root and active_stage["end_ts"] is None:
                close(active_stage, timestamp, cascade=True)
            execution_id = data.get("execution_id")
            if not execution_id or execution_id == trace_id:
                execution_id = f"{trace_id}:step:{identifier}"
            parent_execution = executions.get(data.get("parent_execution_id"))
            parent_id = parent_execution["span_id"] if parent_execution is not None else trace_id
            span = create(
                execution_id,
                "invoke_agent",
                str(data.get("role") or f"Step {data.get('index', '?')}"),
                timestamp,
                parent_id,
                data,
            )
            span["content"].append(event_content)
            executions[execution_id] = stages[str(data.get("index"))] = span
            active_stage = span
        elif kind == "maestro_step_completed":
            if parent is not root:
                close(parent, timestamp, _outcome(data), cascade=True)
                parent["content"].append(event_content)
            active_stage = root
        elif kind == "tool_start":
            key = (scope, tool_id or f"legacy:{identifier}")
            if key in tools:
                continue
            span = create(
                f"{trace_id}:tool:{identifier}",
                "execute_tool",
                str(data.get("tool") or "Tool"),
                timestamp,
                parent["span_id"],
                {**data, "tool_call_id": tool_id},
            )
            span["content"].append(event_content)
            tools[key] = span
        elif kind == "tool_end":
            span = (
                tools.get((scope, tool_id))
                if tool_id
                else next(
                    (
                        s
                        for (owner, _), s in tools.items()
                        if owner == scope
                        and s["end_ts"] is None
                        and s["attrs"].get("gen_ai.tool.name") == data.get("tool")
                    ),
                    None,
                )
            )
            if span is None:
                span = create(
                    f"{trace_id}:tool:{identifier}",
                    "execute_tool",
                    str(data.get("tool") or "Tool"),
                    None,
                    parent["span_id"],
                    {**data, "tool_call_id": tool_id},
                )
            close(span, timestamp, _outcome(data))
            span["content"].append(event_content)
        elif kind in {"answer_delta", "reasoning_delta", "reasoning_summary"}:
            key = parent["span_id"]
            if key not in chats:
                chats[key] = create(
                    f"{trace_id}:chat:{identifier}", "chat", "Model response", timestamp, key, data
                )
            chats[key]["content"].append(event_content)
        elif kind in {"approval_required", "gate_requested", "gate_pending", "gate_required"}:
            gate_id = data.get("gate_id") or data.get("approval_id") or str(identifier)
            if gate_id not in gates:
                gates[gate_id] = create(
                    f"{trace_id}:gate:{identifier}",
                    "harness.gate",
                    "Needs you",
                    timestamp,
                    parent["span_id"],
                    data,
                )
            gates[gate_id]["content"].append(event_content)
        elif kind.startswith("gate_") or kind in {"approval_resolved", "approval_expired"}:
            gate_id = data.get("gate_id") or data.get("approval_id")
            if gate_id in gates:
                span = gates[gate_id]
                span["content"].append(event_content)
                if kind not in {"gate_updated", "gate_reminded"}:
                    outcome = (
                        "failed"
                        if kind.endswith("expired")
                        else "cancelled"
                        if kind.endswith(("cancelled", "invalidated"))
                        else _outcome(data, "completed" if kind == "gate_resolved" else "unknown")
                    )
                    close(span, timestamp, outcome)
        elif kind in TERMINAL or kind == "terminal":
            outcome = kind if kind in TERMINAL else _outcome(data, job.get("state", "unknown"))
            root["attrs"].update(_attrs(_object(data.get("metrics"))))
            close(root, timestamp, outcome, cascade=True)
            root["content"].append(event_content)
        else:
            parent["events"].append({"ts": timestamp, "name": kind, "attrs": _attrs(data)})
            parent["content"].append(event_content)
            if kind in {"context_usage", "usage_metrics"}:
                parent["attrs"].update(_attrs(data))
                parent["attrs"].update(_attrs(_object(data.get("metrics"))))
    # State can establish the root outcome, but never invent an end timestamp.
    if root["end_ts"] is None and job.get("state") in TERMINAL:
        root["attrs"]["outcome"] = job["state"]
        root["status"] = _status(job["state"])
    result = _object(job.get("result"))
    root["attrs"].update(_attrs(_object(result.get("metrics"))))
    orchestration = _object(result.get("orchestration"))
    coordinator = _object(orchestration.get("coordinator"))
    if plan_span is not None:
        plan_span["attrs"].update(_attrs(_object(coordinator.get("metrics"))))
    for step in orchestration.get("steps", []):
        step = _object(step)
        span = executions.get(step.get("execution_id")) or stages.get(str(step.get("index")))
        if span is not None:
            span["attrs"].update(_attrs(_object(step.get("metrics"))))
    return spans
