"""POST /v1/jobs/{job}/retry: one idempotent retry of the latest failed or interrupted turn."""

import asyncio
import json

import pytest
from starlette.testclient import TestClient
from test_workspaces import config

from agent_service.app import Service, create_app
from agent_service.errors import APIError

PROMPT = {
    "project_id": "p",
    "backend": "codex",
    "model": "gpt-6-astra",
    "effort": "low",
    "prompt": "Summarize the report",
}


def make_service(tmp_path, **overrides):
    cfg = config(tmp_path)
    cfg.update(overrides)
    service = Service(cfg)
    return service, ("a", service.config["clients"]["a"])


def settle(service, identity, state, error="provider_error", **fields):
    job = service.submit(identity, {**PROMPT, **fields})["job_id"]
    if state == "running":
        service.conversation_repository.set_running(job)
    elif state != "queued":
        service.finish(job, state, {"error": error, "metrics": None})
    return job


def retry(service, identity, job):
    return asyncio.run(service.retry_turn(identity, job))


def code_of(service, identity, job):
    with pytest.raises(APIError) as caught:
        retry(service, identity, job)
    return caught.value.code, caught.value.status


def test_failed_turn_retries_with_same_prompt_files_and_route(tmp_path):
    service, identity = make_service(tmp_path)
    source = settle(service, identity, "failed")
    result = retry(service, identity, source)
    assert result["reused"] is False
    assert result["job_id"] != source
    assert (result["backend"], result["model"], result["effort"]) == (
        "codex",
        "gpt-6-astra",
        "low",
    )
    assert result["execution_mode"]
    child = json.loads(service.job(identity, result["job_id"])["payload"])
    assert child["prompt"] == PROMPT["prompt"]
    assert child["parent_job_id"] == source
    assert child["retry_of"] == source
    service.db.close()


def test_retry_keeps_attachments_and_failed_turn_model(tmp_path):
    service, identity = make_service(tmp_path)
    service.message_repository.add_file("f1", "p", "a.txt", "a.txt", "d", json.dumps([]), "a")
    source = settle(service, identity, "failed", file_ids=["f1"])
    result = retry(service, identity, source)
    child = json.loads(service.job(identity, result["job_id"])["payload"])
    assert child["file_ids"] == ["f1"]
    assert child["model"] == "gpt-6-astra"
    service.db.close()


def test_second_retry_replays_the_same_job(tmp_path):
    service, identity = make_service(tmp_path)
    source = settle(service, identity, "failed")
    first = retry(service, identity, source)
    second = retry(service, identity, source)
    assert second["reused"] is True
    assert second["job_id"] == first["job_id"]
    count = service.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    assert count == 2
    service.db.close()


def test_interrupted_turn_is_accepted(tmp_path):
    service, identity = make_service(tmp_path)
    source = settle(service, identity, "interrupted", error="service_restarted")
    assert retry(service, identity, source)["reused"] is False
    service.db.close()


@pytest.mark.parametrize("state", ["completed", "running", "cancelled", "queued"])
def test_other_states_are_refused(tmp_path, state):
    service, identity = make_service(tmp_path)
    source = settle(service, identity, state)
    assert code_of(service, identity, source) == ("retry_source_not_failed", 409)
    service.db.close()


def test_superseded_source_is_refused(tmp_path):
    service, identity = make_service(tmp_path)
    source = settle(service, identity, "failed")
    follow_up = service.submit(identity, {**PROMPT, "parent_job_id": source})["job_id"]
    service.finish(follow_up, "completed", {"answer": "ok"})
    assert code_of(service, identity, source) == ("retry_source_superseded", 409)
    service.db.close()


def test_replayed_retry_stays_reusable_after_the_child_finishes(tmp_path):
    service, identity = make_service(tmp_path)
    source = settle(service, identity, "failed")
    first = retry(service, identity, source)
    service.finish(first["job_id"], "failed", {"error": "provider_error", "metrics": None})
    assert retry(service, identity, source)["job_id"] == first["job_id"]
    service.db.close()


@pytest.mark.parametrize(
    "extra",
    [
        {"schedule_id": "s1"},
        {"invocations": [{"kind": "workflow", "resource_id": "wf"}]},
    ],
)
def test_workflow_and_schedule_sources_are_not_retryable(tmp_path, extra):
    service, identity = make_service(tmp_path)
    source = settle(service, identity, "failed")
    payload = json.loads(service.job(identity, source)["payload"]) | extra
    service.conversation_repository.set_payload(source, json.dumps(payload))
    assert code_of(service, identity, source) == ("retry_not_supported", 409)
    service.db.close()


def test_maestro_stage_source_is_not_retryable(tmp_path):
    service, identity = make_service(tmp_path)
    source = settle(service, identity, "failed")
    payload = json.loads(service.job(identity, source)["payload"]) | {"_maestro_stage": "plan"}
    service.conversation_repository.set_payload(source, json.dumps(payload))
    assert code_of(service, identity, source)[0] == "retry_not_supported"
    service.db.close()


def set_access_mode(service, identity, job, mode):
    payload = json.loads(service.job(identity, job)["payload"]) | {"access_mode": mode}
    service.conversation_repository.set_payload(job, json.dumps(payload))


def test_owner_only_access_mode_is_rechecked_on_retry(tmp_path):
    service, identity = make_service(tmp_path)
    source = settle(service, identity, "failed")
    set_access_mode(service, identity, source, "full")
    assert code_of(service, identity, source) == ("access_mode_owner_only", 403)
    service.db.close()


def test_disabled_full_access_is_rechecked_on_retry(tmp_path, monkeypatch):
    monkeypatch.setattr("agent_service.harness_agents.LOCAL_CLIENT", "a")
    service, identity = make_service(tmp_path, full_access=True)
    source = settle(service, identity, "failed", access_mode="full")
    service.config["full_access"] = False
    assert code_of(service, identity, source) == ("full_access_disabled", 403)
    service.db.close()


def test_other_owner_gets_the_denial_resume_gives(tmp_path):
    service, identity = make_service(tmp_path)
    source = settle(service, identity, "failed")
    other = ("b", service.config["clients"]["b"])
    assert code_of(service, other, source) == ("job_not_found", 404)
    service.db.close()


def test_route_returns_202_and_replays(tmp_path):
    app = create_app(config(tmp_path))
    client = TestClient(app, headers={"Authorization": "Bearer a"})
    service = app.state.service
    identity = ("a", service.config["clients"]["a"])
    source = settle(service, identity, "failed")
    first = client.post(f"/v1/jobs/{source}/retry", json={})
    assert first.status_code == 202, first.text
    again = client.post(f"/v1/jobs/{source}/retry", json={})
    assert again.status_code == 202
    assert again.json()["reused"] is True
    assert again.json()["job_id"] == first.json()["job_id"]
    job = client.get(f"/v1/jobs/{first.json()['job_id']}").json()
    assert job["request"]["retry_of"] == source
    completed = settle(service, identity, "completed")
    refused = client.post(f"/v1/jobs/{completed}/retry", json={})
    assert refused.status_code == 409
    assert refused.json()["code"] == "retry_source_not_failed"
    service.db.close()


def test_clients_cannot_set_retry_of(tmp_path):
    service, identity = make_service(tmp_path)
    source = settle(service, identity, "failed")
    with pytest.raises(APIError) as caught:
        service.submit(identity, {**PROMPT, "retry_of": source})
    assert caught.value.code == "invalid_internal_field"


def test_clients_cannot_use_the_retry_key_prefix(tmp_path):
    service, identity = make_service(tmp_path)
    source = settle(service, identity, "failed")
    with pytest.raises(APIError) as caught:
        service.submit(identity, PROMPT, "retry:" + source)
    assert caught.value.code == "invalid_idempotency_key"
    assert retry(service, identity, source)["reused"] is False


def test_retry_of_deleted_attachment_reports_file_not_found(tmp_path):
    service, identity = make_service(tmp_path)
    source = settle(service, identity, "failed")
    payload = json.loads(service.job(identity, source)["payload"])
    payload["file_ids"] = ["gone-file"]
    with service.db:
        service.db.execute(
            "UPDATE jobs SET payload=? WHERE id=?", (json.dumps(payload), source)
        )
    code, _status = code_of(service, identity, source)
    assert code == "file_not_found"
