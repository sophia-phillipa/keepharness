"""The scheduler: due runs go through the job path once, failures pause, shutdown is clean."""

# Fixtures imported from schedule_fixtures are re-declared as test arguments.
# ruff: noqa: F811

import asyncio
import json
import logging
from unittest.mock import AsyncMock, patch

import pytest
from schedule_fixtures import (  # noqa: F401
    ALICE,
    VALID,
    clock,
    config,
    folder_of,
    idle_worker,
    local,
)
from starlette.testclient import TestClient
from test_workflow_resume_rerun import setup_run

from agent_service import maestro, schedules, workflows
from agent_service.app import create_app
from agent_service.errors import APIError
from agent_service.persistence.db import encoded
from agent_service.services import scheduler
from agent_service.services.conversation_service import ConversationService

HOUR = 3600


@pytest.fixture
def service(config, clock):
    instance = ConversationService(config)
    yield instance
    instance.db.close()


def identity(config, owner="a"):
    return owner, config["clients"][owner]


def add(config, owner="a", **overrides):
    return schedules.create_schedule(config, owner, {**VALID, **overrides})


def current(config, created, owner="a"):
    return next(
        item for item in schedules.list_schedules(config, owner) if item["id"] == created["id"]
    )


def tick(service, now):
    asyncio.run(scheduler.tick(service, now))


def jobs(service):
    return [
        {**dict(row), "payload": json.loads(row["payload"])}
        for row in service.db.execute("SELECT * FROM jobs ORDER BY created, id")
    ]


def fail_the_codex_route(config):
    config["services"]["codex"]["models"].remove("gpt-6-astra")


# --------------------------------------------------------------------------- due runs


def test_a_due_schedule_is_submitted_through_the_job_path_exactly_once(config, service, clock):
    created = add(config)  # next run: Saturday 2026-10-03 09:00
    tick(service, local(2026, 10, 3, 8, 59))
    assert jobs(service) == []
    now = local(2026, 10, 3, 9, 0) + 5
    tick(service, now)
    (job,) = jobs(service)
    assert (job["owner"], job["state"], job["project"]) == ("a", "queued", "p")
    request = job["payload"]
    assert request["prompt"] == VALID["prompt"]
    assert (request["backend"], request["model"], request["effort"]) == (
        "codex",
        "gpt-6-astra",
        "low",
    )
    assert request["access_mode"] == "ask" and request["execution_mode"] == "native"
    assert (request["schedule_id"], request["schedule_title"]) == (created["id"], created["title"])
    assert "parent_job_id" not in request
    after = current(config, created)
    assert after["last_run"] == {"job_id": job["id"], "at": now, "state": "submitted"}
    assert (after["failures"], after["enabled"], after["paused_reason"]) == (0, True, "")
    assert after["next_run"] == local(2026, 10, 4, 9)
    tick(service, now)
    tick(service, now + 20)
    assert len(jobs(service)) == 1
    tick(service, local(2026, 10, 4, 9) + 1)
    assert len(jobs(service)) == 2
    assert current(config, created)["next_run"] == local(2026, 10, 5, 9)


def test_a_scheduled_job_is_the_same_as_one_submitted_by_hand(config, service, clock):
    add(config, access_mode="read_only")
    tick(service, local(2026, 10, 3, 9) + 1)
    by_hand = service.submit(
        identity(config),
        {
            "prompt": VALID["prompt"],
            "project_id": "p",
            "backend": "codex",
            "model": "gpt-6-astra",
            "effort": "low",
            "access_mode": "read_only",
        },
    )["job_id"]
    scheduled, manual = (job["payload"] for job in jobs(service))
    assert {
        key: value for key, value in scheduled.items() if not key.startswith("schedule_")
    } == manual
    assert by_hand


def test_downtime_does_not_cause_a_burst_of_catch_up_runs(config, service, clock):
    daily = add(config)
    hourly = add(config, cadence={"kind": "interval", "hours": 1})
    now = local(2026, 10, 9, 12, 30)  # six days later
    tick(service, now)
    assert sorted(job["payload"]["schedule_id"] for job in jobs(service)) == sorted(
        [daily["id"], hourly["id"]]
    )
    assert current(config, daily)["next_run"] == local(2026, 10, 10, 9)
    assert current(config, hourly)["next_run"] == now + HOUR
    tick(service, now + 1)
    tick(service, now + 600)
    assert len(jobs(service)) == 2


def test_only_enabled_schedules_that_are_due_run(config, service, clock):
    soon = add(config, title="Soon")
    add(config, title="Later", cadence={"kind": "daily", "time": "23:00"})
    add(config, title="Paused", enabled=False)
    tick(service, local(2026, 10, 3, 10))
    assert [job["payload"]["schedule_id"] for job in jobs(service)] == [soon["id"]]


def test_each_schedule_runs_for_its_own_owner(config, service, clock):
    mine = add(config, title="Mine")
    theirs = add(config, owner="b", title="Theirs")
    tick(service, local(2026, 10, 3, 10))
    owners = {job["payload"]["schedule_id"]: job["owner"] for job in jobs(service)}
    assert owners == {mine["id"]: "a", theirs["id"]: "b"}


def test_one_schedule_failing_does_not_stop_the_others(config, service, clock):
    good = add(config, title="Good")
    bad = add(config, title="Bad", backend="claude", model="sonnet", effort="configured")
    config["services"]["claude"]["models"].remove("sonnet")
    tick(service, local(2026, 10, 3, 10))
    assert [job["payload"]["schedule_id"] for job in jobs(service)] == [good["id"]]
    assert current(config, good)["last_run"]["state"] == "submitted"
    assert current(config, bad)["last_run"]["state"] == "failed"


# --------------------------------------------------------------------------- failures


def test_a_failed_submit_is_recorded_and_the_next_occurrence_is_scheduled(config, service, clock):
    created = add(config)
    fail_the_codex_route(config)
    now = local(2026, 10, 3, 9) + 1
    tick(service, now)
    after = current(config, created)
    assert jobs(service) == []
    assert after["failures"] == 1 and after["enabled"] is True and after["paused_reason"] == ""
    assert after["next_run"] == local(2026, 10, 4, 9)
    last = after["last_run"]
    assert (last["job_id"], last["at"], last["state"]) == (None, now, "failed")
    assert last["error"] and last["error"].replace("_", "").isalnum()


def test_three_failures_in_a_row_pause_the_schedule(config, service, clock):
    created = add(config)
    fail_the_codex_route(config)
    for day in (3, 4, 5):
        tick(service, local(2026, 10, day, 9) + 1)
    after = current(config, created)
    assert jobs(service) == []
    assert (after["enabled"], after["failures"], after["next_run"]) == (False, 3, None)
    assert after["paused_reason"].startswith("Paused after 3 failed runs in a row")
    assert after["last_run"]["error"] in after["paused_reason"]
    tick(service, local(2026, 10, 9, 9))
    assert current(config, created) == after


def test_a_success_resets_the_count_of_failures(config, service, clock):
    created = add(config)
    fail_the_codex_route(config)
    tick(service, local(2026, 10, 3, 9) + 1)
    tick(service, local(2026, 10, 4, 9) + 1)
    assert current(config, created)["failures"] == 2
    config["services"]["codex"]["models"].append("gpt-6-astra")
    tick(service, local(2026, 10, 5, 9) + 1)
    after = current(config, created)
    assert (after["failures"], after["enabled"], after["last_run"]["state"]) == (
        0,
        True,
        "submitted",
    )


def test_turning_a_paused_schedule_back_on_lets_it_run_again(config, service, clock):
    created = add(config)
    fail_the_codex_route(config)
    for day in (3, 4, 5):
        tick(service, local(2026, 10, day, 9) + 1)
    config["services"]["codex"]["models"].append("gpt-6-astra")
    clock.now = local(2026, 10, 5, 12)
    paused = current(config, created)
    schedules.replace_schedule(
        config, "a", created["id"], {"enabled": True, "revision": paused["revision"]}
    )
    tick(service, local(2026, 10, 6, 9) + 1)
    assert len(jobs(service)) == 1
    assert current(config, created)["failures"] == 0


def test_a_busy_queue_defers_the_run_without_counting_a_failure(config, service, clock):
    created = add(config)
    hand = [
        service.submit(identity(config), {**VALID, "prompt": "busy %d" % number})["job_id"]
        for number in range(10)
    ]
    now = local(2026, 10, 3, 9) + 1
    tick(service, now)
    after = current(config, created)
    assert len(jobs(service)) == 10
    assert (after["failures"], after["last_run"]) == (0, None)
    assert after["next_run"] == created["next_run"]  # still due: tried again on the next tick
    for job_id in hand[:3]:
        service.finish(job_id, "completed", {"answer": "done"})
    tick(service, now + 30)
    assert len(jobs(service)) == 11
    assert current(config, created)["last_run"]["state"] == "submitted"
    assert current(config, created)["next_run"] == local(2026, 10, 4, 9)


def test_an_unexpected_error_is_a_failure_and_its_details_are_not_stored(
    config, service, clock, monkeypatch, caplog
):
    created = add(config)

    def explode(*_args, **_kwargs):
        raise RuntimeError("password=hunter2 /home/sophia/secret")

    monkeypatch.setattr(service, "submit", explode)
    with caplog.at_level(logging.ERROR):
        tick(service, local(2026, 10, 3, 9) + 1)
    after = current(config, created)
    assert after["failures"] == 1 and after["last_run"]["error"] == "submit_failed"
    stored = (folder_of(config) / (created["id"] + ".json")).read_text()
    assert "hunter2" not in stored and "/home/sophia" not in stored
    assert "hunter2" in caplog.text  # the operator still gets the traceback


def test_a_lost_project_counts_as_a_failure(config, service, clock):
    created = add(config)
    config["clients"]["a"]["projects"].remove("p")
    tick(service, local(2026, 10, 3, 9) + 1)
    after = current(config, created)
    assert (after["failures"], after["last_run"]["error"]) == (1, "project_denied")


def test_a_removed_client_pauses_its_schedules_at_once(config, service, clock):
    created = add(config, owner="b")
    kept = add(config)
    del config["clients"]["b"]
    tick(service, local(2026, 10, 3, 9) + 1)
    after = current(config, created, owner="b")
    assert (after["enabled"], after["next_run"], after["failures"]) == (False, None, 0)
    assert "client" in after["paused_reason"] and after["paused_reason"].endswith(".")
    assert [job["payload"]["schedule_id"] for job in jobs(service)] == [kept["id"]]


# --------------------------------------------------------------------------- bookkeeping races


def test_an_outcome_is_not_applied_to_a_schedule_edited_since_it_was_due(config, service, clock):
    created = add(config)
    (due,) = schedules.due(config, local(2026, 10, 3, 10))
    edited = schedules.replace_schedule(
        config,
        "a",
        created["id"],
        {"cadence": {"kind": "interval", "hours": 5}, "revision": created["revision"]},
    )
    schedules.finish_due(config, due, local(2026, 10, 3, 10), job_id="x" * 32)
    assert current(config, created) == edited


def test_an_outcome_does_not_bring_back_a_deleted_schedule(config, service, clock):
    created = add(config)
    (due,) = schedules.due(config, local(2026, 10, 3, 10))
    schedules.delete_schedule(config, "a", created["id"], {"revision": created["revision"]})
    schedules.finish_due(config, due, local(2026, 10, 3, 10), job_id="x" * 32)
    assert schedules.list_schedules(config, "a") == []
    assert list(folder_of(config).iterdir()) == []


def test_a_crash_between_submitting_and_recording_does_not_run_the_schedule_twice(
    config, service, clock, monkeypatch, caplog
):
    created = add(config)
    now = local(2026, 10, 3, 9) + 1

    def crash(*_args, **_kwargs):
        raise OSError("killed before the file was updated")

    with monkeypatch.context() as patch:
        patch.setattr(schedules, "finish_due", crash)
        with caplog.at_level(logging.ERROR):
            tick(service, now)
    (first,) = jobs(service)
    assert current(config, created)["last_run"] is None  # nothing was recorded
    tick(service, now + 30)  # after the restart: the same due time, the same job
    assert [job["id"] for job in jobs(service)] == [first["id"]]
    assert current(config, created)["last_run"]["job_id"] == first["id"]
    assert current(config, created)["next_run"] == local(2026, 10, 4, 9)


@pytest.mark.parametrize("field", ["schedule_id", "schedule_title"])
def test_clients_cannot_mark_their_own_jobs_as_scheduled(config, service, field):
    with pytest.raises(APIError, match="invalid_internal_field"):
        service.submit(identity(config), {**VALID, "prompt": "hi", field: "x"})
    assert jobs(service) == []


def test_recovering_a_scheduled_workflow_starts_an_unscheduled_conversation(tmp_path):
    service, who, row, data, plan = setup_run(tmp_path)
    folder = tmp_path / "project/workflows"
    folder.mkdir(parents=True)
    (folder / "review.json").write_text(json.dumps(plan))
    service.config["projects"]["p"]["root"] = str(folder.parent)
    try:
        resolved = workflows.resolve_workflow(
            service.config, "p", "project/p/workflows/review.json", execution_mode="native"
        )
        with patch.object(service, "infer", AsyncMock(return_value={"answer": "done"})):
            result = asyncio.run(maestro.execute_workflow(service, row, data, resolved))
        service.finish(row["id"], "completed", result)
        marked = {**data, "schedule_id": "f" * 32, "schedule_title": "Nightly review"}
        with service.db:
            service.conversation_repository.set_payload(row["id"], encoded(marked))
        child = service.recover_workflow(who, row["id"], {})["job_id"]
        request = json.loads(service.job(who, child)["payload"])
        assert "schedule_id" not in request and "schedule_title" not in request
    finally:
        service.db.close()


# --------------------------------------------------------------------------- the background task


def test_the_app_starts_the_scheduler_and_cancels_it_on_shutdown(config, monkeypatch):
    events = []

    async def probe(service):
        events.append(("started", service))
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            events.append(("cancelled", service))
            raise

    monkeypatch.setattr(scheduler, "run", probe)
    with TestClient(create_app(config), headers=ALICE) as client:
        service = client.app.state.service
        assert client.get("/v1/schedules").status_code == 200
        assert [name for name, _ in events] == ["started"]
    assert [name for name, _ in events] == ["started", "cancelled"]
    assert events[0][1] is service


def test_the_loop_waits_a_tick_before_the_first_check_and_never_runs_a_due_time_twice(
    config, service, clock, monkeypatch
):
    created = add(config)
    clock.now = local(2026, 10, 3, 9) + 1
    monkeypatch.setattr(scheduler, "TICK_SECONDS", 0.05)

    async def scenario():
        task = asyncio.create_task(scheduler.run(service))
        await asyncio.sleep(0.01)
        assert jobs(service) == []  # the first check comes after one interval
        await asyncio.sleep(0.4)  # many ticks, one due time
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert len(jobs(service)) == 1
    assert current(config, created)["last_run"]["job_id"] == jobs(service)[0]["id"]


def test_a_tick_that_raises_is_logged_and_the_loop_goes_on(config, service, monkeypatch, caplog):
    calls = []

    async def flaky(_service, now=None):
        calls.append(now)
        if len(calls) == 1:
            raise RuntimeError("storage hiccup")

    monkeypatch.setattr(scheduler, "tick", flaky)
    monkeypatch.setattr(scheduler, "TICK_SECONDS", 0.02)

    async def scenario():
        task = asyncio.create_task(scheduler.run(service))
        await asyncio.sleep(0.3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    with caplog.at_level(logging.ERROR):
        asyncio.run(scenario())
    assert len(calls) >= 3 and "storage hiccup" in caplog.text


def test_a_broken_schedules_folder_is_reported_and_the_loop_survives(
    config, service, tmp_path, caplog
):
    root = tmp_path / "state" / "schedules"
    target = tmp_path / "elsewhere"
    target.mkdir()
    root.symlink_to(target, target_is_directory=True)
    with caplog.at_level(logging.WARNING):
        tick(service, local(2026, 10, 3, 10))
    assert jobs(service) == [] and list(target.iterdir()) == []
    assert any("schedules" in record.getMessage().lower() for record in caplog.records)


def test_the_tick_interval_is_thirty_seconds():
    assert scheduler.TICK_SECONDS == 30
