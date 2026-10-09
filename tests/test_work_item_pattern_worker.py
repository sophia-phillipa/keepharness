"""The pattern worker's deadline covers matching only, never interpreter startup."""

import asyncio
import json
import signal
import subprocess
import threading
import time
from types import SimpleNamespace

import pytest

from agent_service import work_items
from agent_service.errors import APIError

STARTUP_DELAY = 0.6
CATASTROPHIC = {"work_item_pattern": "(a+)+$"}
CATASTROPHIC_DATA = {"invocations": [{"args": "a" * 100000 + "!"}]}


@pytest.fixture
def slow_startup(monkeypatch):
    """Delay the worker's start-up well past the match budget."""
    monkeypatch.setattr(work_items, "_MATCH_SECONDS", 0.2)
    monkeypatch.setattr(
        work_items,
        "_MATCH_REFERENCES",
        f"import time\ntime.sleep({STARTUP_DELAY})\n" + work_items._MATCH_REFERENCES,
    )


@pytest.fixture
def children(monkeypatch):
    started = []
    original = work_items.subprocess.Popen

    def record(*args, **kwargs):
        child = original(*args, **kwargs)
        started.append(child)
        return child

    monkeypatch.setattr(work_items.subprocess, "Popen", record)
    return started


def test_valid_pattern_survives_slow_worker_startup(slow_startup, children):
    result = work_items.invocation_reference(
        {}, {"work_item_pattern": r"TASK-\d+"}, {"invocations": [{"args": "see TASK-7"}]}
    )
    assert result == "TASK-7"
    assert len(children) == 1 and children[0].returncode == 0


def test_catastrophic_pattern_is_cut_off_after_slow_startup(slow_startup, children):
    started = time.monotonic()
    with pytest.raises(APIError, match="invalid_work_item_pattern"):
        work_items.invocation_reference({}, CATASTROPHIC, CATASTROPHIC_DATA)
    assert time.monotonic() - started < STARTUP_DELAY + 3
    assert len(children) == 1 and children[0].returncode is not None


# A worker that outlives the test would only finish after this long, so a missing kill shows
# up as a failed assertion on elapsed time or exit status instead of a hung test run.
STUCK_SECONDS = 3


def test_worker_that_never_signals_ready_is_killed(monkeypatch, children):
    monkeypatch.setattr(work_items, "_STARTUP_SECONDS", 0.3)
    monkeypatch.setattr(
        work_items, "_MATCH_REFERENCES", f"import time\ntime.sleep({STUCK_SECONDS})\n"
    )
    started = time.monotonic()
    with pytest.raises(APIError, match="invalid_work_item_pattern"):
        work_items.invocation_reference(
            {}, {"work_item_pattern": "x"}, {"invocations": [{"args": "x"}]}
        )
    assert time.monotonic() - started < STUCK_SECONDS - 1
    assert len(children) == 1 and children[0].returncode == -signal.SIGKILL


def test_worker_crash_fails_closed(monkeypatch, children):
    monkeypatch.setattr(
        work_items, "_MATCH_REFERENCES", 'import sys\nprint("ready", flush=True)\nsys.exit(3)\n'
    )
    with pytest.raises(APIError, match="invalid_work_item_pattern"):
        work_items.invocation_reference(
            {}, {"work_item_pattern": "x"}, {"invocations": [{"args": "x"}]}
        )
    assert len(children) == 1 and children[0].returncode == 3


def test_interrupt_while_waiting_for_ready_still_reaps_the_worker(monkeypatch):
    class Interrupted:
        def __init__(self, stream):
            self.stream = stream

        def readline(self):
            raise KeyboardInterrupt

        def close(self):
            self.stream.close()

    children = []
    original = work_items.subprocess.Popen

    def start(*args, **kwargs):
        child = original(*args, **kwargs)
        child.stdout = Interrupted(child.stdout)
        children.append(child)
        return child

    monkeypatch.setattr(work_items.subprocess, "Popen", start)
    monkeypatch.setattr(
        work_items, "_MATCH_REFERENCES", f"import time\ntime.sleep({STUCK_SECONDS})\n"
    )
    started = time.monotonic()
    with pytest.raises(KeyboardInterrupt):
        work_items.invocation_reference(
            {}, {"work_item_pattern": "x"}, {"invocations": [{"args": "x"}]}
        )
    assert time.monotonic() - started < STUCK_SECONDS - 1
    assert children[0].returncode == -signal.SIGKILL


# The match runs off the event loop and at most _MAX_WORKERS children run at once.

PROJECT = {"work_item_pattern": r"TASK-\d+"}
DATA = {"invocations": [{"args": "see TASK-7"}]}


def record_worker(monkeypatch, *, delay=0.0, reply='["TASK-7"]'):
    """Replace the child with a stub that records where and how concurrently it ran."""
    seen = SimpleNamespace(calls=0, active=0, peak=0, on_loop=[], guard=threading.Lock())

    def fake(payload):
        try:
            asyncio.get_running_loop()
            seen.on_loop.append(True)
        except RuntimeError:
            seen.on_loop.append(False)
        with seen.guard:
            seen.calls += 1
            seen.active += 1
            seen.peak = max(seen.peak, seen.active)
        time.sleep(delay)
        with seen.guard:
            seen.active -= 1
        return reply

    monkeypatch.setattr(work_items, "_run_worker", fake)
    return seen


def test_event_loop_keeps_running_while_a_slow_match_is_awaited(slow_startup):
    async def scenario():
        ticks = []

        async def ticker():
            while True:
                ticks.append(time.monotonic())
                await asyncio.sleep(0.02)

        task = asyncio.create_task(ticker())
        await work_items.prematch_reference({}, PROJECT, DATA)
        task.cancel()
        return len(ticks)

    # Blocking the loop for the 0.6 s start-up would leave a single tick.
    assert asyncio.run(scenario()) >= 8


def test_prematched_outcome_is_used_once_by_the_synchronous_call(monkeypatch):
    seen = record_worker(monkeypatch)

    async def scenario():
        await work_items.prematch_reference({}, PROJECT, DATA)
        first = work_items.invocation_reference({}, PROJECT, DATA)
        second = work_items.invocation_reference({}, PROJECT, DATA)
        return first, second

    assert asyncio.run(scenario()) == ("TASK-7", "TASK-7")
    assert seen.calls == 2 and seen.on_loop == [False, True]  # only the second one matched inline


def test_prematched_failure_is_raised_by_the_synchronous_call(monkeypatch):
    def fail(payload):
        raise subprocess.TimeoutExpired("worker", 0.5)

    monkeypatch.setattr(work_items, "_run_worker", fail)

    async def scenario():
        await work_items.prematch_reference({}, PROJECT, DATA)
        monkeypatch.setattr(work_items, "_run_worker", lambda payload: pytest.fail("matched twice"))
        work_items.invocation_reference({}, PROJECT, DATA)

    with pytest.raises(APIError, match="invalid_work_item_pattern"):
        asyncio.run(scenario())


def test_concurrent_matches_respect_the_worker_cap(monkeypatch):
    seen = record_worker(monkeypatch, delay=0.05)

    async def scenario():
        await asyncio.gather(
            *(
                work_items.prematch_reference({}, PROJECT, DATA)
                for _ in range(work_items._MAX_WORKERS * 3)
            )
        )

    asyncio.run(scenario())
    assert seen.calls == work_items._MAX_WORKERS * 3
    assert 1 < seen.peak <= work_items._MAX_WORKERS


def test_match_gives_up_when_no_worker_slot_frees_up(monkeypatch):
    record_worker(monkeypatch)
    monkeypatch.setattr(work_items, "_ADMISSION_SECONDS", 0.05)
    held = [work_items._slots.acquire() for _ in range(work_items._MAX_WORKERS)]
    try:
        with pytest.raises(APIError) as error:
            work_items.invocation_reference({}, PROJECT, DATA)
    finally:
        for _ in held:
            work_items._slots.release()
    # The pattern-check cap is not the job queue: the copy must not say the queue is full.
    assert error.value.code == "work_item_check_busy" and error.value.status == 429
    from agent_service.services import scheduler

    assert "work_item_check_busy" in scheduler.TRANSIENT_CODES  # a busy check defers a run


@pytest.mark.parametrize("source", ["explicit", "chips", "persona", "schedule"])
@pytest.mark.parametrize(
    "reply,error_code",
    [
        ('["TASK-1234"]', None),
        ('["TASK-1234", "TASK-5678"]', "ambiguous_work_item"),
        ("invalid json", "invalid_work_item_pattern"),
    ],
)
def test_submit_route_matches_off_the_event_loop(tmp_path, monkeypatch, source, reply, error_code):
    from test_invocation_normalization import invocation_service

    from agent_service.routes import conversations

    service, identity = invocation_service(tmp_path, monkeypatch)
    service.config["projects"]["p"]["work_item_pattern"] = r"TASK-\d{4}"
    item = next(
        i
        for i in service.resource_catalog(identity, "p", "codex", "gpt-6-astra")["items"]
        if i["name"] == ("discussion" if source == "persona" else "reviewer")
    )
    request_body = dict(
        project_id="p",
        backend="codex",
        model="gpt-6-astra",
        effort="low",
        prompt="Hello",
        invocations=[
            dict(
                kind="agent",
                resource_id=item["resource_id"],
                args="Review TASK-1234",
                order=0,
                mode="conversational" if source == "persona" else "delegated",
            )
        ],
    )

    selection = {"id": item["id"], "revision": item["revision"], "token": "/" + item["name"]}
    route = conversations.submit_job
    if source == "chips":
        request_body.pop("invocations")
        request_body.update(prompt="/reviewer Review TASK-1234", resource_selections=[selection])
    elif source == "persona":
        parent = service.submit(identity, {**request_body, "work_item": None})["job_id"]
        service.db.execute("UPDATE jobs SET state='completed' WHERE id=?", (parent,))
        service.db.commit()
        request_body.pop("invocations")
        request_body.update(parent_job_id=parent, prompt="Review TASK-1234")
    elif source == "schedule":
        from agent_service import schedules
        from agent_service.routes import schedules as schedule_routes

        record = dict(
            project_id="p",
            backend="codex",
            model="gpt-6-astra",
            effort="low",
            prompt="Review TASK-1234",
            access_mode="ask",
            agent="reviewer",
            page_ids=[],
            id="scheduled-review",
            title="Review",
            allow_internet=False,
        )
        monkeypatch.setattr(schedules, "owned_record", lambda *args: record)
        monkeypatch.setattr(schedules, "agent_selection", lambda *args: selection)
        monkeypatch.setattr(schedules, "note_manual_run", lambda *args: None)
        route = schedule_routes.run_now

    async def stream():
        yield json.dumps(request_body).encode()

    seen = record_worker(monkeypatch, reply=reply)
    request = SimpleNamespace(
        stream=stream,
        state=SimpleNamespace(),
        headers={},
        path_params={"schedule": "scheduled-review"},
    )
    try:
        if error_code:
            with pytest.raises(APIError, match=error_code) as error:
                asyncio.run(route(request, service, identity))
            assert error.value.status == 422
        else:
            response = asyncio.run(route(request, service, identity))
            assert response.status_code == 202
            job = json.loads(response.body)["job_id"]
            assert service.job(identity, job)["work_item"] == "TASK-1234"
        assert seen.calls == 1 and seen.on_loop == [False]
    finally:
        service.db.close()


def test_submit_rechecks_project_deletion_after_awaiting_match(tmp_path, monkeypatch):
    from test_invocation_normalization import invocation_service

    from agent_service.services import conversation_service

    service, identity = invocation_service(tmp_path, monkeypatch)
    service.config["projects"]["p"]["work_item_pattern"] = r"TASK-\d{4}"
    item = next(
        item
        for item in service.resource_catalog(identity, "p", "codex", "gpt-6-astra")["items"]
        if item["name"] == "reviewer"
    )
    data = dict(
        project_id="p",
        backend="codex",
        model="gpt-6-astra",
        effort="low",
        prompt="/reviewer Review TASK-1234",
        resource_selections=[
            {"id": item["id"], "revision": item["revision"], "token": "/reviewer"}
        ],
    )
    record_worker(monkeypatch, reply='["TASK-1234"]')

    async def scenario():
        matching = asyncio.Event()
        resume = asyncio.Event()

        async def paused_match(*args):
            matching.set()
            await resume.wait()
            await work_items.prematch_reference(*args)

        monkeypatch.setattr(conversation_service, "prematch_reference", paused_match)
        submission = asyncio.create_task(service.submit_async(identity, data))
        try:
            await asyncio.wait_for(matching.wait(), timeout=2)
            # Deletion can begin while there is still no queued job for this project.
            assert service.conversation_repository.count_pending_for_owner(identity[0]) == 0
            service.deleting_project_folders.add("p")
            resume.set()
            with pytest.raises(APIError, match="project_folder_busy") as error:
                await submission
            assert error.value.status == 409
            assert service.conversation_repository.count_pending_for_owner(identity[0]) == 0
        finally:
            submission.cancel()
            await asyncio.gather(submission, return_exceptions=True)
            service.deleting_project_folders.discard("p")

    try:
        asyncio.run(scenario())
    finally:
        service.db.close()


def test_scheduled_runs_without_an_agent_never_reach_the_worker():
    """Schedules without a selected agent do not produce invocation arguments."""
    from agent_service import schedules

    record = dict(
        prompt="Daily",
        project_id="p",
        backend="codex",
        model="m",
        effort="low",
        access_mode="ask",
        agent=None,
        page_ids=[],
    )
    assert work_items._match_payload({}, PROJECT, schedules.job_request({}, record)) is None


def submission_service(tmp_path, monkeypatch):
    from test_invocation_normalization import invocation_service

    service, identity = invocation_service(tmp_path, monkeypatch)
    service.config["projects"]["p"]["work_item_pattern"] = r"TASK-\d{4}"
    item = next(
        item
        for item in service.resource_catalog(identity, "p", "codex", "gpt-6-astra")["items"]
        if item["name"] == "reviewer"
    )
    data = dict(
        project_id="p",
        backend="codex",
        model="gpt-6-astra",
        effort="low",
        prompt="/reviewer Review TASK-1234",
        resource_selections=[
            {"id": item["id"], "revision": item["revision"], "token": "/reviewer"}
        ],
    )
    return service, identity, data


def test_submit_refuses_a_project_whose_deletion_finished_during_admission(tmp_path, monkeypatch):
    from agent_service.services import conversation_service

    service, identity, data = submission_service(tmp_path, monkeypatch)
    record_worker(monkeypatch, reply='["TASK-1234"]')

    async def deletion_finishes_while_matching(*args):
        await work_items.prematch_reference(*args)
        # Neither "deleting" nor "deleted" was set at the first look; both have passed by now.
        service.deleted_project_folders.add("p")

    monkeypatch.setattr(
        conversation_service, "prematch_reference", deletion_finishes_while_matching
    )
    try:
        with pytest.raises(APIError, match="project_folder_deleted") as error:
            asyncio.run(service.submit_async(identity, data))
        assert error.value.status == 410
        assert service.conversation_repository.count_pending_for_owner(identity[0]) == 0
    finally:
        service.db.close()


def test_a_replayed_idempotency_key_never_spawns_a_pattern_worker(tmp_path, monkeypatch):
    service, identity, data = submission_service(tmp_path, monkeypatch)
    seen = record_worker(monkeypatch, reply='["TASK-1234"]')
    try:
        first = asyncio.run(service.submit_async(identity, data, "key-1"))
        assert seen.calls == 1
        second = asyncio.run(service.submit_async(identity, data, "key-1"))
        assert second["reused"] is True and second["job_id"] == first["job_id"]
        assert seen.calls == 1
    finally:
        service.db.close()


def test_a_prematched_outcome_never_outlives_a_refused_submit(tmp_path, monkeypatch):
    service, identity, data = submission_service(tmp_path, monkeypatch)
    record_worker(monkeypatch, reply='["TASK-1234"]')
    service.deleted_project_folders.add("p")

    async def scenario():
        with pytest.raises(APIError, match="project_folder_deleted"):
            await service.submit_async(identity, data)
        return work_items._prematched.get()

    try:
        assert asyncio.run(scenario()) is None
    finally:
        service.db.close()
