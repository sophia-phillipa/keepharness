"""Version mapping payloads without changing legacy scalar wire values."""

import json

import pytest

from agent_service.app import Service


@pytest.mark.parametrize("payload", [None, [], "unknown"])
def test_legacy_nonmapping_event_keeps_original_payload(make_harness_config, payload):
    service = Service(make_harness_config())
    service.event("job", "quota_after", payload)
    event = service.message_repository.last_event("job")
    assert json.loads(event["data"]) == payload
    service.db.close()


def test_event_envelope_retains_explicit_execution_identity(make_harness_config):
    service = Service(make_harness_config())
    source = {
        "execution_id": "child",
        "parent_execution_id": "job",
        "attempt": 2,
        "outcome": "completed",
    }
    service.event("job", "maestro_step_completed", source)
    event = json.loads(service.message_repository.last_event("job")["data"])
    assert event == {"schema_version": 1, **source}
    assert "schema_version" not in source
    service.db.close()


def test_maestro_progress_keeps_unavailable_quota_null(tmp_path, monkeypatch):
    monkeypatch.setattr("agent_service.harness_agents.LOCAL_CLIENT", "a")  # quota events are owner-only
    import asyncio
    from unittest.mock import AsyncMock, patch

    from test_workspaces import config

    configuration = config(tmp_path)
    configuration["codex"] = {}
    service = Service(configuration)
    data = dict(
        project_id="p",
        backend="codex",
        model="gpt-6-astra",
        prompt="Check",
        execution_mode="native",
        _maestro_stage="1",
        _execution_id="child",
        _parent_execution_id="job",
        _attempt=2,
    )
    service.conversation_repository.insert(
        "job", "p", "a", "running", 1, json.dumps(data), None, None, None
    )
    service.db.commit()
    row = service.job(("a", configuration["clients"]["a"]), "job")

    async def provider(*args):
        args[2]("answer_delta", {"text": "Done"})
        return {"answer": "Done"}

    with (
        patch.object(service, "quota", AsyncMock(return_value=None)),
        patch("adapters.run_native", side_effect=provider),
    ):
        result = asyncio.run(service.infer(row, data))
    events = {
        event["type"]: json.loads(event["data"])
        for event in service.message_repository.all_events("job")
    }
    assert result["answer"] == "Done"
    assert events["quota_after"] is None
    assert events["answer_delta"]["execution_id"] == "child"
    assert events["answer_delta"]["attempt"] == 2
    service.db.close()
