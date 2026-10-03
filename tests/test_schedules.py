"""Scheduled tasks: validation, cadence math, storage, HTTP API, isolation and file safety."""

# Fixtures imported from schedule_fixtures are re-declared as test arguments.
# ruff: noqa: F811

import json
import os
import stat
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path

import pytest
from schedule_fixtures import (  # noqa: F401
    ALICE,
    BOB,
    DAILY,
    VALID,
    clock,
    config,
    digest,
    folder_of,
    idle_worker,
    local,
)
from starlette.testclient import TestClient

from agent_service import schedules
from agent_service.app import create_app
from agent_service.errors import APIError

FIELDS = [
    "id",
    "title",
    "prompt",
    "project_id",
    "backend",
    "model",
    "effort",
    "access_mode",
    "cadence",
    "enabled",
    "created_at",
    "updated_at",
    "next_run",
    "last_run",
    "failures",
    "paused_reason",
    "revision",
]


@pytest.fixture
def api(config, clock):
    with TestClient(create_app(config), headers=ALICE) as client:
        yield client


def make(api, headers=None, **overrides):
    body = {**VALID, **overrides}
    response = api.post("/v1/schedules", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def listing(api, headers=None):
    response = api.get("/v1/schedules", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["schedules"]


def change(schedule, **fields):
    return {"revision": schedule["revision"], **fields}


def remove(api, schedule, headers=None):
    return api.request(
        "DELETE",
        "/v1/schedules/" + schedule["id"],
        json={"revision": schedule["revision"]},
        headers=headers,
    )


# --------------------------------------------------------------------------- cadence math


@pytest.mark.parametrize(
    "now,expected",
    [
        ((2026, 10, 3, 8, 59), (2026, 10, 3, 9)),
        ((2026, 10, 3, 9), (2026, 10, 4, 9)),
        ((2026, 10, 3, 9, 1), (2026, 10, 4, 9)),
        ((2026, 12, 31, 23, 59), (2027, 1, 1, 9)),
        ((2028, 2, 28, 12), (2028, 2, 29, 9)),
    ],
    ids=["before", "exactly-at", "after", "year-end", "leap-day"],
)
def test_daily_runs_at_the_next_wall_clock_time_strictly_after_now(zone, now, expected):
    zone("UTC")
    assert schedules.next_run({"kind": "daily", "time": "09:00"}, local(*now)) == local(*expected)


@pytest.mark.parametrize(
    "now,weekday,expected",
    [
        ((2026, 10, 3, 8), 5, (2026, 10, 3, 8, 30)),  # Saturday, later today
        ((2026, 10, 3, 8, 30), 5, (2026, 10, 10, 8, 30)),  # exactly now: next week
        ((2026, 10, 3, 9), 5, (2026, 10, 10, 8, 30)),
        ((2026, 10, 3, 8), 0, (2026, 10, 5, 8, 30)),  # Monday
        ((2026, 10, 3, 8), 6, (2026, 10, 4, 8, 30)),  # Sunday
        ((2026, 10, 3, 8), 4, (2026, 10, 9, 8, 30)),  # Friday
    ],
    ids=["same-day", "exactly-at", "same-day-passed", "monday", "sunday", "friday"],
)
def test_weekly_runs_on_the_next_matching_weekday_with_monday_as_zero(zone, now, weekday, expected):
    zone("UTC")
    cadence = {"kind": "weekly", "weekday": weekday, "time": "08:30"}
    result = schedules.next_run(cadence, local(*now))
    assert result == local(*expected)
    assert datetime.fromtimestamp(result).weekday() == weekday


@pytest.mark.parametrize("hours", [1, 6, 24, 168])
def test_interval_runs_a_whole_number_of_hours_after_now(zone, hours):
    zone("UTC")
    now = local(2026, 10, 3, 8, 15)
    assert schedules.next_run({"kind": "interval", "hours": hours}, now) == now + hours * 3600
    assert schedules.next_run({"kind": "interval", "hours": hours}, now + 0.9) > now + 0.9


def test_cadence_times_are_in_the_servers_local_time_zone(zone):
    zone("UTC")
    utc = schedules.next_run(DAILY, local(2026, 10, 3, 0))
    zone("EST5EDT,M3.2.0,M11.1.0")
    new_york = schedules.next_run(DAILY, local(2026, 10, 3, 0))
    assert datetime.fromtimestamp(new_york).strftime("%H:%M") == "09:00"
    assert new_york - utc == 4 * 3600  # 09:00 EDT is 13:00 UTC


def test_daily_keeps_the_wall_clock_time_across_daylight_saving_changes(zone):
    zone("EST5EDT,M3.2.0,M11.1.0")  # US rules: 2026-03-08 spring forward, 2026-11-01 fall back
    spring = schedules.next_run(DAILY, local(2026, 3, 7, 10))
    autumn = schedules.next_run(DAILY, local(2026, 10, 31, 10))
    assert (spring, autumn) == (local(2026, 3, 8, 9), local(2026, 11, 1, 9))
    assert spring - local(2026, 3, 7, 10) == 22 * 3600  # 23 wall-clock hours, one skipped
    assert autumn - local(2026, 10, 31, 10) == 24 * 3600  # 23 wall-clock hours, one repeated


def test_a_time_that_does_not_exist_still_runs_once_that_day(zone):
    zone("EST5EDT,M3.2.0,M11.1.0")
    cadence = {"kind": "daily", "time": "02:30"}  # skipped on 2026-03-08
    first = schedules.next_run(cadence, local(2026, 3, 7, 3))
    assert datetime.fromtimestamp(first).date() == date(2026, 3, 8)
    second = schedules.next_run(cadence, first)
    assert datetime.fromtimestamp(second).strftime("%Y-%m-%d %H:%M") == "2026-03-09 02:30"
    assert first < second


def test_weekly_keeps_the_wall_clock_time_across_daylight_saving_changes(zone):
    zone("EST5EDT,M3.2.0,M11.1.0")
    cadence = {"kind": "weekly", "weekday": 6, "time": "09:00"}  # Sundays
    result = schedules.next_run(cadence, local(2026, 3, 7, 10))
    assert datetime.fromtimestamp(result).strftime("%Y-%m-%d %H:%M") == "2026-03-08 09:00"
    following = schedules.next_run(cadence, result)
    assert datetime.fromtimestamp(following).strftime("%Y-%m-%d %H:%M") == "2026-03-15 09:00"


# --------------------------------------------------------------------------- create and list


def test_create_returns_the_schedule_and_stores_a_private_file(api, config, clock):
    response = api.post("/v1/schedules", json=VALID)
    assert response.status_code == 201, response.text
    assert response.headers["Cache-Control"] == "no-store"
    created = response.json()
    assert list(created) == FIELDS
    assert len(created["id"]) == 32 and int(created["id"], 16) >= 0
    assert {key: created[key] for key in VALID} == VALID
    assert created["access_mode"] == "ask" and created["enabled"] is True
    assert created["next_run"] == local(2026, 10, 3, 9)
    assert (created["last_run"], created["failures"], created["paused_reason"]) == (None, 0, "")
    assert created["created_at"] == created["updated_at"] and created["created_at"].endswith("Z")
    path = folder_of(config) / (created["id"] + ".json")
    text = path.read_text(encoding="utf-8")
    assert created["revision"] == digest(text)
    stored = json.loads(text)
    assert stored["owner"] == "a" and "revision" not in stored
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    for directory in (folder_of(config), folder_of(config).parent):
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert [entry.name for entry in folder_of(config).iterdir()] == [created["id"] + ".json"]


def test_the_owner_is_not_part_of_the_response_or_the_folder_name(api, config):
    created = make(api)
    assert "owner" not in created
    assert [entry.name for entry in (Path(config["state_dir"]) / "schedules").iterdir()] == [
        digest("a")[:16]
    ]


def test_each_cadence_kind_computes_its_first_run(api, clock):
    weekly = make(api, cadence={"kind": "weekly", "weekday": 0, "time": "07:15"})
    interval = make(api, cadence={"kind": "interval", "hours": 6})
    assert weekly["next_run"] == local(2026, 10, 5, 7, 15)
    assert interval["next_run"] == clock.now + 6 * 3600


def test_a_schedule_can_be_created_read_only_or_paused(api):
    read_only = make(api, access_mode="read_only")
    paused = make(api, enabled=False)
    assert read_only["access_mode"] == "read_only"
    assert paused["enabled"] is False and paused["next_run"] is None


def test_the_list_holds_only_the_callers_schedules_in_creation_order(api, clock):
    first = make(api, title="First")
    clock.now += 60
    second = make(api, title="Second")
    make(api, title="Bob's", headers=BOB)
    assert [item["id"] for item in listing(api)] == [first["id"], second["id"]]
    assert [item["title"] for item in listing(api, BOB)] == ["Bob's"]
    assert listing(api)[0] == first


def test_an_empty_list_creates_no_folder(api, config):
    assert api.get("/v1/schedules").json() == {"schedules": []}
    assert not (Path(config["state_dir"]) / "schedules").exists()


def test_titles_and_prompts_are_trimmed_and_prompts_keep_their_line_breaks(api):
    created = make(api, title="  Digest  ", prompt="\n  Line one\n\tline two  \n")
    assert created["title"] == "Digest" and created["prompt"] == "Line one\n\tline two"


# --------------------------------------------------------------------------- validation

BAD_FIELDS = [
    ("title missing", {"title": None}, "title"),
    ("title number", {"title": 5}, "title"),
    ("title empty", {"title": ""}, "title"),
    ("title blank", {"title": " \t "}, "title"),
    ("title too long", {"title": "x" * 121}, "title"),
    ("title line break", {"title": "a\nb"}, "title"),
    ("prompt missing", {"prompt": None}, "prompt"),
    ("prompt number", {"prompt": 5}, "prompt"),
    ("prompt blank", {"prompt": "  \n "}, "prompt"),
    ("prompt too long", {"prompt": "x" * 20001}, "prompt"),
    ("prompt control", {"prompt": "a\x00b"}, "prompt"),
    ("project missing", {"project_id": None}, "project_id"),
    ("project number", {"project_id": 7}, "project_id"),
    ("project without providers", {"project_id": "q"}, "backend"),
    ("backend missing", {"backend": None}, "backend"),
    ("backend number", {"backend": 1}, "backend"),
    ("backend maestro", {"backend": "maestro"}, "backend"),
    ("backend auto", {"backend": "auto"}, "backend"),
    ("backend unknown", {"backend": "nope"}, "backend"),
    ("backend not enabled", {"backend": "gemini", "model": "m", "effort": "configured"}, "backend"),
    ("model missing", {"model": None}, "model"),
    ("model empty", {"model": ""}, "model"),
    ("model not offered", {"model": "ghost"}, "model"),
    ("model of another provider", {"model": "sonnet"}, "model"),
    ("effort missing", {"effort": None}, "effort"),
    ("effort empty", {"effort": ""}, "effort"),
    ("effort not offered", {"model": "gpt-6-lite", "effort": "high"}, "effort"),
    ("access auto", {"access_mode": "auto"}, "access_mode"),
    ("access full", {"access_mode": "full"}, "access_mode"),
    ("access unknown", {"access_mode": "everything"}, "access_mode"),
    ("access number", {"access_mode": 1}, "access_mode"),
    ("access null", {"access_mode": "<null>"}, "access_mode"),
    ("cadence missing", {"cadence": None}, "cadence"),
    ("cadence text", {"cadence": "daily"}, "cadence"),
    ("cadence empty", {"cadence": {}}, "cadence"),
    ("cadence unknown kind", {"cadence": {"kind": "monthly", "day": 1}}, "cadence"),
    ("daily without time", {"cadence": {"kind": "daily"}}, "cadence"),
    ("daily time shape", {"cadence": {"kind": "daily", "time": "9:00"}}, "cadence"),
    ("daily hour 24", {"cadence": {"kind": "daily", "time": "24:00"}}, "cadence"),
    ("daily minute 60", {"cadence": {"kind": "daily", "time": "09:60"}}, "cadence"),
    ("daily time number", {"cadence": {"kind": "daily", "time": 900}}, "cadence"),
    ("daily extra key", {"cadence": {"kind": "daily", "time": "09:00", "hours": 2}}, "cadence"),
    ("weekly without weekday", {"cadence": {"kind": "weekly", "time": "09:00"}}, "cadence"),
    ("weekly weekday 7", {"cadence": {"kind": "weekly", "weekday": 7, "time": "09:00"}}, "cadence"),
    (
        "weekly weekday -1",
        {"cadence": {"kind": "weekly", "weekday": -1, "time": "09:00"}},
        "cadence",
    ),
    (
        "weekly weekday text",
        {"cadence": {"kind": "weekly", "weekday": "mon", "time": "09:00"}},
        "cadence",
    ),
    (
        "weekly weekday bool",
        {"cadence": {"kind": "weekly", "weekday": True, "time": "09:00"}},
        "cadence",
    ),
    ("interval without hours", {"cadence": {"kind": "interval"}}, "cadence"),
    ("interval 0", {"cadence": {"kind": "interval", "hours": 0}}, "cadence"),
    ("interval 169", {"cadence": {"kind": "interval", "hours": 169}}, "cadence"),
    ("interval fraction", {"cadence": {"kind": "interval", "hours": 1.5}}, "cadence"),
    ("interval text", {"cadence": {"kind": "interval", "hours": "2"}}, "cadence"),
    ("interval bool", {"cadence": {"kind": "interval", "hours": True}}, "cadence"),
    ("enabled text", {"enabled": "yes"}, "enabled"),
    ("enabled number", {"enabled": 1}, "enabled"),
    ("unknown field", {"timezone": "UTC"}, "timezone"),
]


def with_overrides(overrides):
    """``None`` removes a field, ``"<null>"`` sends an explicit JSON null."""
    body = {**VALID, **overrides}
    return {
        key: (None if value == "<null>" else value)
        for key, value in body.items()
        if value is not None or overrides.get(key) == "<null>"
    }


@pytest.mark.parametrize("label,overrides,field", BAD_FIELDS, ids=[row[0] for row in BAD_FIELDS])
def test_create_validation_names_the_offending_field(api, config, label, overrides, field):
    response = api.post("/v1/schedules", json=with_overrides(overrides))
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "schedule_invalid" and response.json()["field"] == field
    assert not (Path(config["state_dir"]) / "schedules").exists()


# A field left out of an update keeps its value, so only the rows that send a bad value apply.
UPDATE_ROWS = [row for row in BAD_FIELDS if None not in row[1].values()]


@pytest.mark.parametrize("label,overrides,field", UPDATE_ROWS, ids=[row[0] for row in UPDATE_ROWS])
def test_update_validation_names_the_offending_field(api, label, overrides, field):
    created = make(api)
    patch = {key: value for key, value in with_overrides(overrides).items() if key in overrides}
    response = api.put("/v1/schedules/" + created["id"], json=change(created, **patch))
    assert response.status_code == 422, response.text
    assert response.json()["field"] == field
    assert listing(api) == [created]


def test_unattended_runs_never_get_automatic_or_full_access(api):
    for mode in ("auto", "full"):
        response = api.post("/v1/schedules", json={**VALID, "access_mode": mode})
        assert (response.status_code, response.json()["field"]) == (422, "access_mode")
    created = make(api)
    response = api.put("/v1/schedules/" + created["id"], json=change(created, access_mode="full"))
    assert (response.status_code, response.json()["field"]) == (422, "access_mode")


def test_the_documented_limits_are_the_ones_in_force():
    assert schedules.MAX_SCHEDULES == 50
    assert schedules.TITLE_LENGTH == (1, 120)
    assert schedules.PROMPT_LENGTH == (1, 20000)
    assert schedules.ACCESS_MODES == ("ask", "read_only")
    assert schedules.FAILURE_LIMIT == 3


def test_boundary_values_are_accepted(api):
    make(api, title="t" * 120, prompt="p" * 20000)
    make(api, cadence={"kind": "daily", "time": "00:00"})
    make(api, cadence={"kind": "daily", "time": "23:59"})
    make(api, cadence={"kind": "weekly", "weekday": 0, "time": "12:00"})
    make(api, cadence={"kind": "weekly", "weekday": 6, "time": "12:00"})
    make(api, cadence={"kind": "interval", "hours": 1})
    make(api, cadence={"kind": "interval", "hours": 168})


@pytest.mark.parametrize(
    "backend,model,effort",
    [
        ("codex", "gpt-6-astra", "high"),
        ("codex", "gpt-6-lite", "low"),
        ("claude", "sonnet", "configured"),
        ("local", "installed-model", "configured"),
    ],
)
def test_every_offered_route_is_accepted(api, backend, model, effort):
    created = make(api, backend=backend, model=model, effort=effort)
    assert (created["backend"], created["model"], created["effort"]) == (backend, model, effort)


def test_the_route_must_be_offered_for_the_schedules_project(api, config):
    config["services"]["codex"]["projects"] = ["sem-projeto"]
    response = api.post("/v1/schedules", json=VALID)
    assert (response.status_code, response.json()["field"]) == (422, "backend")
    assert make(api, project_id="sem-projeto")["project_id"] == "sem-projeto"


def test_a_project_the_caller_cannot_use_is_denied(api, config):
    for response in (
        api.post("/v1/schedules", json={**VALID, "project_id": "q"}, headers=BOB),
        api.post("/v1/schedules", json={**VALID, "project_id": "nowhere"}),
    ):
        assert response.status_code == 403 and response.json()["code"] == "project_denied"
    created = make(api)
    response = api.put("/v1/schedules/" + created["id"], json=change(created, project_id="nowhere"))
    assert response.status_code == 403 and response.json()["code"] == "project_denied"
    assert not (Path(config["state_dir"]) / "schedules" / digest("b")[:16]).exists()


def test_the_limit_is_per_owner(api, monkeypatch):
    monkeypatch.setattr(schedules, "MAX_SCHEDULES", 2)
    first = make(api, title="one")
    make(api, title="two")
    full = api.post("/v1/schedules", json=VALID)
    assert full.status_code == 409 and full.json()["code"] == "schedule_limit"
    make(api, headers=BOB)
    assert remove(api, first).status_code == 200
    make(api, title="three")


# --------------------------------------------------------------------------- update


def test_update_changes_only_the_fields_that_are_sent(api, clock):
    created = make(api)
    clock.now += 600
    response = api.put(
        "/v1/schedules/" + created["id"], json=change(created, title="Evening digest")
    )
    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["title"] == "Evening digest"
    assert {key: updated[key] for key in VALID if key != "title"} == {
        key: value for key, value in VALID.items() if key != "title"
    }
    assert updated["created_at"] == created["created_at"]
    assert updated["updated_at"] >= created["updated_at"]
    assert updated["revision"] != created["revision"]
    assert updated["next_run"] == created["next_run"]  # an edit that is not about timing keeps it
    assert listing(api) == [updated]


def test_a_whole_schedule_read_can_be_sent_back_with_an_edit(api):
    created = make(api)
    response = api.put("/v1/schedules/" + created["id"], json={**created, "prompt": "New prompt."})
    assert response.status_code == 200, response.text
    assert response.json()["prompt"] == "New prompt."


def test_every_editable_field_can_change(api):
    created = make(api)
    edit = {
        "title": "Weekly review",
        "prompt": "Review the week.",
        "backend": "claude",
        "model": "sonnet",
        "effort": "configured",
        "access_mode": "read_only",
        "cadence": {"kind": "weekly", "weekday": 4, "time": "17:00"},
        "project_id": "sem-projeto",
        "enabled": True,
    }
    response = api.put("/v1/schedules/" + created["id"], json=change(created, **edit))
    assert response.status_code == 200, response.text
    assert {key: response.json()[key] for key in edit} == edit


def test_a_new_cadence_recomputes_the_next_run_from_now(api, clock):
    created = make(api)
    clock.now += 3600
    cadence = {"kind": "interval", "hours": 2}
    updated = api.put(
        "/v1/schedules/" + created["id"], json=change(created, cadence=cadence)
    ).json()
    assert updated["next_run"] == clock.now + 2 * 3600


def test_pausing_clears_the_next_run_and_resuming_computes_it_again(api, clock):
    created = make(api)
    paused = api.put("/v1/schedules/" + created["id"], json=change(created, enabled=False)).json()
    assert paused["enabled"] is False and paused["next_run"] is None
    clock.now += 86400 * 2
    resumed = api.put("/v1/schedules/" + created["id"], json=change(paused, enabled=True)).json()
    assert resumed["enabled"] is True and resumed["next_run"] == local(2026, 10, 5, 9)


def test_turning_a_paused_schedule_back_on_clears_the_failures_and_the_reason(api, config):
    created = make(api)
    path = folder_of(config) / (created["id"] + ".json")
    stored = json.loads(path.read_text())
    stored.update(
        enabled=False, next_run=None, failures=3, paused_reason="Paused after 3 failed runs."
    )
    path.write_text(json.dumps(stored))
    current = listing(api)[0]
    assert current["failures"] == 3 and current["paused_reason"].startswith("Paused")
    resumed = api.put("/v1/schedules/" + created["id"], json=change(current, enabled=True)).json()
    assert (resumed["failures"], resumed["paused_reason"]) == (0, "")
    assert resumed["next_run"] is not None


def test_an_unavailable_route_cannot_be_enabled_but_can_always_be_paused_or_deleted(api, config):
    created = make(api)
    config["services"]["codex"]["models"].remove("gpt-6-astra")
    response = api.put("/v1/schedules/" + created["id"], json=change(created, title="Renamed"))
    assert (response.status_code, response.json()["field"]) == (422, "model")
    paused = api.put("/v1/schedules/" + created["id"], json=change(created, enabled=False))
    assert paused.status_code == 200 and paused.json()["enabled"] is False
    renamed = api.put("/v1/schedules/" + created["id"], json=change(paused.json(), title="Renamed"))
    assert renamed.status_code == 200
    again = api.put("/v1/schedules/" + created["id"], json=change(renamed.json(), enabled=True))
    assert (again.status_code, again.json()["field"]) == (422, "model")
    assert remove(api, renamed.json()).status_code == 200


def test_update_and_delete_need_a_current_revision(api):
    created = make(api)
    for body in ({}, {"revision": ""}, {"revision": 7}):
        response = api.put("/v1/schedules/" + created["id"], json={**body, "title": "x"})
        assert (response.status_code, response.json()["field"]) == (422, "revision")
        response = api.request("DELETE", "/v1/schedules/" + created["id"], json=body)
        assert (response.status_code, response.json()["field"]) == (422, "revision")
    assert listing(api) == [created]


def test_a_stale_revision_is_a_conflict_and_changes_nothing(api, config):
    created = make(api)
    api.put("/v1/schedules/" + created["id"], json=change(created, title="First writer"))
    path = folder_of(config) / (created["id"] + ".json")
    before = path.read_text()
    stale = api.put("/v1/schedules/" + created["id"], json=change(created, title="Second writer"))
    assert stale.status_code == 409 and stale.json()["code"] == "schedule_changed"
    gone = remove(api, created)
    assert gone.status_code == 409 and gone.json()["code"] == "schedule_changed"
    assert path.read_text() == before


def test_delete_removes_the_schedule(api, config):
    created = make(api)
    response = remove(api, created)
    assert response.status_code == 200 and response.json() == {"deleted": True}
    assert response.headers["Cache-Control"] == "no-store"
    assert listing(api) == [] and list(folder_of(config).iterdir()) == []
    again = remove(api, created)
    assert again.status_code == 404 and again.json()["code"] == "schedule_not_found"


def test_unknown_and_malformed_ids_are_not_found(api):
    for schedule_id in ("0" * 32, "not-an-id", "A" * 32, "a" * 31):
        for response in (
            api.put("/v1/schedules/" + schedule_id, json={"revision": "r", "title": "x"}),
            api.request("DELETE", "/v1/schedules/" + schedule_id, json={"revision": "r"}),
            api.post("/v1/schedules/" + schedule_id + "/run"),
        ):
            assert response.status_code == 404, response.text
            assert response.json()["code"] == "schedule_not_found"


def test_concurrent_writers_with_one_revision_have_one_winner(config, clock):
    created = schedules.create_schedule(config, "a", VALID)
    barrier = threading.Barrier(8)

    def writer(number):
        barrier.wait()
        try:
            schedules.replace_schedule(
                config,
                "a",
                created["id"],
                {"title": "writer %d" % number, "revision": created["revision"]},
            )
        except APIError as error:
            return error.code
        return "won"

    with ThreadPoolExecutor(8) as pool:
        outcomes = list(pool.map(writer, range(8)))
    assert outcomes.count("won") == 1 and outcomes.count("schedule_changed") == 7


# --------------------------------------------------------------------------- isolation


def test_another_owners_schedule_is_not_found_in_every_operation(api):
    created = make(api)
    assert listing(api, BOB) == []
    writes = api.put(
        "/v1/schedules/" + created["id"], json=change(created, title="Mine now"), headers=BOB
    )
    deletes = remove(api, created, headers=BOB)
    runs = api.post("/v1/schedules/" + created["id"] + "/run", headers=BOB)
    for response in (writes, deletes, runs):
        assert response.status_code == 404 and response.json()["code"] == "schedule_not_found"
    assert listing(api) == [created]


def test_every_route_needs_authentication(api):
    anonymous = {"Authorization": ""}
    some_id = "0" * 32
    calls = [
        api.get("/v1/schedules", headers=anonymous),
        api.post("/v1/schedules", json=VALID, headers=anonymous),
        api.put("/v1/schedules/" + some_id, json={"revision": "r"}, headers=anonymous),
        api.request(
            "DELETE", "/v1/schedules/" + some_id, json={"revision": "r"}, headers=anonymous
        ),
        api.post("/v1/schedules/" + some_id + "/run", headers=anonymous),
    ]
    assert [response.status_code for response in calls] == [401] * 5


# --------------------------------------------------------------------------- file safety


def test_a_symlinked_owner_folder_is_refused_and_never_written_through(api, config, tmp_path):
    target = tmp_path / "elsewhere"
    target.mkdir()
    folder = folder_of(config)
    folder.parent.mkdir(parents=True)
    folder.symlink_to(target, target_is_directory=True)
    for response in (api.post("/v1/schedules", json=VALID), api.get("/v1/schedules")):
        assert response.status_code == 500
        assert response.json()["code"] == "schedule_storage_unsafe"
    assert list(target.iterdir()) == []


def test_a_symlinked_schedule_file_is_refused_and_never_written_through(api, config, tmp_path):
    created = make(api)
    path = folder_of(config) / (created["id"] + ".json")
    outside = tmp_path / "outside.json"
    outside.write_text(path.read_text())
    path.unlink()
    path.symlink_to(outside)
    assert listing(api) == []
    for response in (
        api.put("/v1/schedules/" + created["id"], json=change(created, title="Through the link")),
        remove(api, created),
        api.post("/v1/schedules/" + created["id"] + "/run"),
    ):
        assert response.status_code == 500 and response.json()["code"] == "schedule_storage_unsafe"
    assert json.loads(outside.read_text())["title"] == VALID["title"]


def test_unusable_files_are_skipped_not_fatal(api, config):
    created = make(api)
    folder = folder_of(config)
    valid = json.loads((folder / (created["id"] + ".json")).read_text())
    (folder / ("1" * 32 + ".json")).write_text("{not json")
    (folder / ("2" * 32 + ".json")).write_text(json.dumps({**valid, "id": "9" * 32}))
    (folder / ("3" * 32 + ".json")).write_text(json.dumps({**valid, "id": "3" * 32, "title": ""}))
    (folder / ("4" * 32 + ".json")).write_text(json.dumps([valid]))
    (folder / ("5" * 32 + ".json")).write_text(
        json.dumps({**valid, "id": "5" * 32, "prompt": "x" * 140000})
    )
    (folder / ("6" * 32 + ".json")).mkdir()
    (folder / "notes.txt").write_text("ignored")
    assert [item["id"] for item in listing(api)] == [created["id"]]


@pytest.mark.parametrize("mode", ["auto", "full", "everything"])
def test_a_hand_edited_file_cannot_grant_unattended_automatic_access(api, config, mode):
    created = make(api)
    path = folder_of(config) / (created["id"] + ".json")
    path.write_text(json.dumps({**json.loads(path.read_text()), "access_mode": mode}))
    assert listing(api) == []
    assert schedules.due(config, 10**10) == []


def test_a_file_in_another_owners_folder_is_never_used(api, config):
    created = make(api)
    stolen = folder_of(config, "b")
    stolen.mkdir(parents=True)
    source = folder_of(config) / (created["id"] + ".json")
    (stolen / source.name).write_text(source.read_text())  # still names owner "a"
    assert listing(api, BOB) == []
    assert [item["owner"] for item in schedules.due(config, 10**10)] == ["a"]


def test_an_existing_folder_is_tightened_to_owner_only(config, clock):
    folder = folder_of(config)
    folder.mkdir(mode=0o755, parents=True)
    os.chmod(folder, 0o755)
    schedules.create_schedule(config, "a", VALID)
    assert stat.S_IMODE(folder.stat().st_mode) == 0o700


def test_a_failed_write_leaves_neither_a_schedule_nor_a_temp_file(config, clock, monkeypatch):
    def broken(_descriptor):
        raise OSError("disk full")

    with monkeypatch.context() as patch:
        patch.setattr(os, "fsync", broken)
        with pytest.raises(OSError, match="disk full"):
            schedules.create_schedule(config, "a", VALID)
    assert list(folder_of(config).iterdir()) == []


# --------------------------------------------------------------------------- run now


def test_run_now_submits_a_new_conversation_and_keeps_the_cadence(api, config, clock):
    created = make(api, access_mode="read_only")
    clock.now += 120
    response = api.post("/v1/schedules/" + created["id"] + "/run")
    assert response.status_code == 202, response.text
    assert list(response.json()) == ["job_id"]
    job_id = response.json()["job_id"]
    service = api.app.state.service
    payload = json.loads(service.conversation_repository.get(job_id)["payload"])
    assert payload["prompt"] == VALID["prompt"] and payload["project_id"] == "p"
    assert (payload["backend"], payload["model"], payload["effort"]) == (
        "codex",
        "gpt-6-astra",
        "low",
    )
    assert payload["access_mode"] == "read_only"
    assert (payload["schedule_id"], payload["schedule_title"]) == (created["id"], created["title"])
    assert "parent_job_id" not in payload
    assert service.conversation_repository.get(job_id)["owner"] == "a"
    after = listing(api)[0]
    assert after["next_run"] == created["next_run"]
    assert after["last_run"] == {"job_id": job_id, "at": clock.now, "state": "submitted"}
    assert (after["failures"], after["enabled"]) == (0, True)


def test_run_now_works_for_a_paused_schedule_and_does_not_resume_it(api):
    created = make(api, enabled=False)
    response = api.post("/v1/schedules/" + created["id"] + "/run")
    assert response.status_code == 202, response.text
    after = listing(api)[0]
    assert after["enabled"] is False and after["next_run"] is None
    assert after["last_run"]["job_id"] == response.json()["job_id"]


def test_run_now_reports_why_it_could_not_submit_and_does_not_count_a_failure(api, config):
    created = make(api)
    config["services"]["codex"]["models"].remove("gpt-6-astra")
    response = api.post("/v1/schedules/" + created["id"] + "/run")
    assert response.status_code >= 400 and "code" in response.json()
    after = listing(api)[0]
    assert (after["failures"], after["last_run"]) == (0, None)


def test_run_now_honours_an_idempotency_key(api):
    created = make(api)
    headers = {"Idempotency-Key": "double-click"}
    first = api.post("/v1/schedules/" + created["id"] + "/run", headers=headers).json()
    second = api.post("/v1/schedules/" + created["id"] + "/run", headers=headers).json()
    assert first["job_id"] == second["job_id"]


def test_run_now_needs_the_project_to_still_be_usable(api, config):
    created = make(api)
    config["clients"]["a"]["projects"].remove("p")
    response = api.post("/v1/schedules/" + created["id"] + "/run")
    assert response.status_code == 403 and response.json()["code"] == "project_denied"


# --------------------------------------------------------------------------- conversation list

JOB = {
    "project_id": "p",
    "backend": "codex",
    "model": "gpt-6-astra",
    "effort": "low",
    "prompt": "By hand.",
}


def conversations(api):
    return {item["id"]: item for item in api.get("/v1/conversations").json()["conversations"]}


def test_the_conversation_list_marks_conversations_started_by_a_schedule(api):
    created = make(api, title="Digest")
    scheduled = api.post("/v1/schedules/" + created["id"] + "/run").json()["job_id"]
    by_hand = api.post("/v1/jobs", json=JOB).json()["job_id"]
    items = conversations(api)
    assert items[scheduled]["schedule_id"] == created["id"]
    assert items[scheduled]["schedule_title"] == "Digest"
    assert "schedule_id" not in items[by_hand] and "schedule_title" not in items[by_hand]


def test_a_follow_up_turn_keeps_the_conversation_marked(api):
    created = make(api)
    scheduled = api.post("/v1/schedules/" + created["id"] + "/run").json()["job_id"]
    api.app.state.service.finish(scheduled, "completed", {"answer": "done"})
    follow_up = api.post("/v1/jobs", json={**JOB, "parent_job_id": scheduled})
    assert follow_up.status_code == 202, follow_up.text
    items = conversations(api)
    assert list(items) == [scheduled]
    assert items[scheduled]["schedule_id"] == created["id"]
    assert items[scheduled]["last_job_id"] == follow_up.json()["job_id"]


def test_a_client_cannot_mark_its_own_job_as_scheduled(api):
    for field in ("schedule_id", "schedule_title"):
        response = api.post("/v1/jobs", json={**JOB, field: "forged"})
        assert response.status_code == 422
        assert response.json()["code"] == "invalid_internal_field"
    assert conversations(api) == {}
