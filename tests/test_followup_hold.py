"""D16: Stop keeps the queued follow-ups waiting until the user runs or discards them."""

import json

import pytest
from test_dispatch_capacity import insert
from test_execution_modes import service

from agent_service.errors import APIError


def ready(instance):
    return [row["id"] for row in instance.conversation_repository.ready()]


def events(instance, job, kind):
    return [
        json.loads(row["data"]).get("reason")
        for row in instance.message_repository.all_events(job)
        if row["type"] == kind
    ]


@pytest.fixture
def stopped(tmp_path):
    """``first`` was stopped while ``second`` (its follow-up) and ``third`` waited behind it."""
    instance, identity = service(tmp_path)
    insert(instance, "first", "queued")
    insert(instance, "second", parent_job_id="first")
    insert(instance, "third", parent_job_id="second")
    insert(instance, "other")
    reply = instance.cancel(identity, "first")
    yield instance, identity, reply
    instance.db.close()


def test_stop_holds_the_queued_follow_up(stopped):
    instance, _, reply = stopped
    assert reply == {"job_id": "first", "cancel_requested": True}
    assert instance.conversation_repository.state("first")[0] == "cancelled"
    assert instance.conversation_repository.state("second")[0] == "queued"
    assert ready(instance) == ["other"]
    assert events(instance, "second", "queue_wait") == ["held_after_stop"]
    (job,) = [
        item
        for item in instance.activity(("a", instance.config["clients"]["a"]))["jobs"]
        if item["job_id"] == "second"
    ]
    assert job["wait_reason"] == "held_after_stop"


def test_run_queued_message_lets_the_follow_up_run(stopped):
    instance, identity, _ = stopped
    assert instance.run_queued(identity, "second") == {"job_id": "second", "released": True}
    assert sorted(ready(instance)) == ["other", "second"]
    assert events(instance, "second", "queue_released") == [None]
    with pytest.raises(APIError, match="job_not_held"):
        instance.run_queued(identity, "second")


def test_discard_cancels_it_and_holds_the_next_one(stopped):
    instance, identity, _ = stopped
    instance.cancel(identity, "second")
    assert events(instance, "third", "queue_wait") == ["held_after_stop"]
    assert instance.conversation_repository.state("second")[0] == "cancelled"
    assert ready(instance) == ["other"]


def test_a_deleted_conversation_is_never_resumed(stopped):
    instance, identity, _ = stopped
    with instance.db:
        instance.conversation_repository.archive("first")
    with pytest.raises(APIError, match="job_not_found"):
        instance.run_queued(identity, "second")
    assert ready(instance) == ["other"]
    assert json.loads(instance.conversation_repository.get("second")["payload"])["_held_after_stop"]
    assert not instance.db.in_transaction


def test_run_queued_writes_in_one_immediate_transaction(stopped):
    instance, identity, _ = stopped
    statements = []
    instance.db.set_trace_callback(statements.append)
    try:
        instance.run_queued(identity, "second")
    finally:
        instance.db.set_trace_callback(None)
    begin = statements.index("BEGIN IMMEDIATE")
    reads = [i for i, sql in enumerate(statements) if sql.lstrip().upper().startswith("SELECT")]
    assert reads and min(reads) > begin
    assert not instance.db.in_transaction


def test_a_finished_run_holds_nothing(tmp_path):
    instance, identity = service(tmp_path)
    try:
        insert(instance, "first", "completed")
        insert(instance, "second", parent_job_id="first")
        assert instance.cancel(identity, "first") == {"job_id": "first", "cancel_requested": False}
        assert ready(instance) == ["second"]
        with pytest.raises(APIError, match="job_not_held"):
            instance.run_queued(identity, "second")
    finally:
        instance.db.close()


def test_clients_cannot_submit_a_held_follow_up(tmp_path):
    instance, identity = service(tmp_path)
    try:
        with pytest.raises(APIError, match="invalid_internal_field"):
            instance.submit(
                identity,
                dict(
                    project_id="p",
                    backend="codex",
                    model="gpt-6-astra",
                    prompt="x",
                    _held_after_stop=True,
                ),
            )
    finally:
        instance.db.close()
