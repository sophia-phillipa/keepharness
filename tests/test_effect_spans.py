"""Publication provenance remains useful without exposing artifact content."""

from agent_service.spans import events_to_spans


def project(*events):
    return events_to_spans(
        {"id": "run", "state": "completed", "payload": {}},
        [
            {"id": i, "time": i, "type": kind, "data": data}
            for i, (kind, data) in enumerate(events, 1)
        ],
    )


def test_effect_provenance_and_receipt_are_visible_without_content():
    binding = dict(
        effect_id="effect",
        gate_id="gate",
        operation="jira.create_issue",
        destination="https://jira.example.test",
        arguments_digest="args",
        artifact_digest="artifact",
        enforcement="unenforced",
    )
    spans = project(
        ("effect_prepared", {**binding, "artifact": {"secret_text": "private body"}}),
        ("gate_required", {**binding, "publish": True}),
        ("effect_approved", {**binding, "approved_by": "owner"}),
        ("effect_intent", {**binding, "idempotency_key": "correlation"}),
        ("effect_execution", binding),
        ("effect_done", {**binding, "receipt": {"issue_key": "SYN-1"}, "status": "done"}),
    )
    effect = next(s for s in spans if s["kind"] == "harness.effect")
    assert effect["attrs"]["effect_status"] == "done"
    assert effect["attrs"]["approved_by"] == "owner"
    assert effect["attrs"]["artifact_digest"] == "artifact"
    assert effect["attrs"]["receipt_issue_key"] == "SYN-1"
    assert [e["name"] for e in effect["events"]] == [
        "effect_prepared",
        "effect_approved",
        "effect_intent",
        "effect_execution",
        "effect_done",
    ]
    assert all(s["attrs"]["enforcement"] == "unenforced" for s in spans)
    assert "private body" not in str({k: v for k, v in effect.items() if k != "content"})


def test_unknown_survives_run_completion_and_inconclusive_reconciliation():
    spans = project(
        ("effect_prepared", {"effect_id": "effect"}),
        ("effect_unknown", {"effect_id": "effect", "status": "unknown"}),
        ("completed", {}),
        ("effect_reconciled", {"effect_id": "effect", "status": "unknown", "decision": "check"}),
    )
    effect = next(s for s in spans if s["kind"] == "harness.effect")
    assert effect["attrs"]["effect_status"] == "unknown"
    assert effect["attrs"]["outcome"] == "unknown"
    assert effect["status"] == "unset"


def test_evidenced_reconciliation_can_complete_unknown_effect():
    spans = project(
        ("effect_unknown", {"effect_id": "effect", "status": "unknown"}),
        (
            "effect_reconciled",
            {"effect_id": "effect", "status": "done", "receipt": {"issue_key": "SYN-2"}},
        ),
    )
    effect = next(s for s in spans if s["kind"] == "harness.effect")
    assert effect["attrs"]["effect_status"] == "done"
    assert effect["attrs"]["outcome"] == "completed"
    assert effect["attrs"]["receipt_issue_key"] == "SYN-2"


def test_advisory_publish_gate_labels_root_and_gate_unenforced():
    spans = project(
        ("gate_required", {"gate_id": "gate", "publish": True, "enforcement": "advisory"})
    )
    assert all(s["attrs"]["enforcement"] == "unenforced" for s in spans)


def test_publication_policy_marks_run_without_claiming_enforcement_upgrade():
    spans = project(
        ("publication_policy", {"enforcement": "unenforced", "supported": False}),
        ("publication_policy", {"enforcement": "mediated", "supported": True}),
    )
    assert spans[0]["attrs"]["enforcement"] == "unenforced"
    assert (
        project(("publication_policy", {"enforcement": "mediated"}))[0]["attrs"]["enforcement"]
        == "mediated"
    )


def test_publication_outlives_provider_completion_until_human_resolution():
    rows = [
        ("effect_prepared", {"effect_id": "effect", "status": "prepared"}),
        ("gate_required", {"effect_id": "effect", "gate_id": "gate", "publish": True}),
        ("completed", {}),
    ]
    spans = project(*rows)
    assert spans[0]["end_ts"] == 3
    assert all(span["end_ts"] is None for span in spans[1:])
    spans = project(
        *rows,
        ("gate_resolved", {"gate_id": "gate", "choice": "approve"}),
        ("effect_done", {"effect_id": "effect", "status": "done"}),
    )
    assert next(s for s in spans if s["kind"] == "harness.gate")["end_ts"] == 4
    assert next(s for s in spans if s["kind"] == "harness.effect")["end_ts"] == 5
