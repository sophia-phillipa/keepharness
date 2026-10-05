"""Durable UI preferences: schema, caps, atomic private writes, tolerant reads and owner-only access."""

import hashlib
import json
import os
import stat
import threading
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from agent_service import ui_state
from agent_service.app import create_app

SAMPLES = {
    "theme": ["graphite", "violet-bordeaux"],
    "sidebar_collapsed": [True, False],
    "panel_order": ["conversations-left", "conversations-right"],
    "panel_widths": [{"sidebar": 300, "activity_panel": 390.5}, {}],
    "reading_size": ["15", "19"],
    "chat_selection": [{"model": "gpt-6-astra", "effort": "high"}, {"model": "", "effort": ""}, {}],
    "project_list_preferences": [{"p": {"favorite": True, "hidden": False, "hide_icon": False}}, {}],
    "project_expanded": [{"p": False, "q": True}, {}],
    "right_panel_view": ["files", "activity"],
    "activity_open": [True, False],
    "conversation_activity": [
        {"a" * 32: {"token": "b" * 32, "state": "completed", "unread": True}},
        {"c": {"token": "", "state": "running", "unread": False}},
        {},
    ],
    "conversation_scroll": [[["a" * 32, 120.5], ["b", -1]], []],
    "tour_seen": ["0.16.0", ""],
    "run_console_height": [340, 190.5],
    "workspace_sections": [{"files": {"open": True, "height": None}, "activity": {"open": False, "height": 220}}, {}],
    "last_section": ["appearance", ""],
}
OVER = {
    "theme": "x" * 41,
    "reading_size": "16",
    "panel_order": "up",
    "sidebar_collapsed": 1,
    "chat_selection": {"model": "m" * 201},
    "project_expanded": {f"p{i}": True for i in range(201)},
    "project_list_preferences": {f"p{i}": {} for i in range(201)},
    "conversation_activity": {f"c{i}": {} for i in range(101)},
    "conversation_scroll": [[f"c{i}", 1] for i in range(101)],
    "workspace_sections": {f"s{i}": {} for i in range(17)},
    "panel_widths": {"sidebar": "wide"},
    "run_console_height": float("1e9"),
    "tour_seen": "r" * 33,
}


def config(tmp_path):
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    return {
        "state_dir": str(state),
        "origins": [],
        "projects": {"p": {}},
        "clients": {
            "local": {"sha256": hashlib.sha256(b"local").hexdigest(), "projects": ["p"]},
            "a": {"sha256": hashlib.sha256(b"a").hexdigest(), "projects": ["p"]},
        },
        "services": {},
    }


@pytest.fixture
def cfg(tmp_path):  # a new state folder per test: no store survives between tests
    return config(tmp_path)


@pytest.fixture
def api(cfg):
    with TestClient(create_app(cfg), headers={"Authorization": "Bearer local"}) as client:
        yield client


def stored_file(cfg) -> Path:
    return next(Path(cfg["state_dir"]).glob("ui-state/*/preferences.json"))


def patch(api, **values):
    return api.patch("/v1/ui-state", json={"values": values})


def test_empty_store_reads_defaults_without_cache(api):
    response = api.get("/v1/ui-state")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert (body["version"], body["values"], body["read_only"]) == (1, {}, False)


@pytest.mark.parametrize("key", sorted(SAMPLES))
def test_round_trip_per_schema_key_including_sentinels(api, cfg, key):
    for sample in SAMPLES[key]:
        response = patch(api, **{key: sample})
        assert response.status_code == 200, (key, sample, response.text)
        assert response.json()["values"][key] == sample
        assert response.headers["cache-control"] == "no-store"
        with TestClient(create_app(cfg), headers={"Authorization": "Bearer local"}) as restarted:
            assert restarted.get("/v1/ui-state").json()["values"][key] == sample


def test_samples_cover_the_whole_schema():
    assert set(SAMPLES) == set(ui_state.SCHEMA)


def test_null_clears_one_key_and_keeps_the_others(api):
    patch(api, theme="paper", last_section="appearance")
    assert patch(api, theme=None).json()["values"] == {"last_section": "appearance"}


def test_unknown_key_is_422_and_writes_nothing(api, cfg):
    response = patch(api, theme="paper", draft="secret")
    assert (response.status_code, response.json()["code"], response.json()["field"]) == (
        422, "ui_state_unknown_key", "draft")
    assert api.get("/v1/ui-state").json()["values"] == {}


@pytest.mark.parametrize("key", sorted(OVER))
def test_invalid_or_over_cap_value_is_422(api, key):
    response = patch(api, **{key: OVER[key]})
    assert (response.status_code, response.json()["code"], response.json()["field"]) == (
        422, "ui_state_invalid_value", key)


def test_value_bytes_cap_is_enforced(api):
    big = {("k" * 120) + str(i): True for i in range(200)}
    response = patch(api, project_expanded=big)
    assert (response.status_code, response.json()["code"], response.json()["field"]) == (
        422, "ui_state_invalid_value", "project_expanded")


def test_values_must_be_an_object(api):
    assert api.patch("/v1/ui-state", json={"values": []}).json()["code"] == "ui_state_invalid_value"
    response = api.patch("/v1/ui-state", json={})
    assert (response.status_code, response.json()["code"]) == (422, "ui_state_invalid_value")


@pytest.mark.parametrize("key", ["run_console_height", "conversation_scroll", "panel_widths"])
def test_a_huge_integer_is_422_not_500(api, key):
    huge = 10**400
    value = {"run_console_height": huge, "conversation_scroll": [["c", huge]],
             "panel_widths": {"sidebar": huge}}[key]
    response = patch(api, **{key: value})
    assert (response.status_code, response.json()["code"], response.json()["field"]) == (
        422, "ui_state_invalid_value", key)


def test_a_stored_huge_integer_is_dropped_and_the_rest_loads(api, cfg):
    patch(api, theme="paper")
    stored_file(cfg).write_text('{"version": 1, "values": {"theme": "graphite", "run_console_height": 1'
                                + "0" * 400 + "}}")
    response = api.get("/v1/ui-state")
    assert (response.status_code, response.json()["values"]) == (200, {"theme": "graphite"})


def test_project_ids_and_activity_tokens_must_be_identifiers(api):
    for key, value in [("project_expanded", {"/home/someone/secret": True}),
                       ("project_list_preferences", {"a\nb": {"favorite": True}}),
                       ("conversation_activity", {"c1": {"token": "free text with spaces", "state": "done"}})]:
        response = patch(api, **{key: value})
        assert (response.status_code, response.json()["field"]) == (422, key)
    assert patch(api, project_expanded={"sem-projeto": True}).status_code == 200


def test_keys_from_a_newer_build_survive_a_write_from_this_one(api, cfg):
    patch(api, theme="paper")
    document = json.loads(stored_file(cfg).read_text())
    document["values"]["future_key"] = {"kept": True}
    stored_file(cfg).write_text(json.dumps(document))
    patch(api, reading_size="17")
    stored = json.loads(stored_file(cfg).read_text())["values"]
    assert stored["future_key"] == {"kept": True}
    assert "future_key" not in api.get("/v1/ui-state").json()["values"]


def test_quarantine_moved_by_another_reader_is_not_read_only(cfg):
    from agent_service import ui_state
    store = ui_state.repository(cfg, "local")
    store.folder.mkdir(parents=True, exist_ok=True)
    assert ui_state.quarantine(store) is True  # nothing left to move: someone else already did


def test_oversize_body_is_413(api):
    response = api.patch("/v1/ui-state", json={"values": {"theme": "x" * ui_state.BODY_BYTES}})
    assert (response.status_code, response.json()["code"]) == (413, "payload_limit")


def test_limits_are_shared_and_cover_every_capped_key(api):
    limits = api.get("/v1/ui-state").json()["limits"]
    assert limits == ui_state.limits()
    assert set(limits["max_items"]) <= set(ui_state.SCHEMA)
    assert limits["value_bytes"] < limits["body_bytes"]
    assert len(ui_state.SCHEMA) * limits["value_bytes"] <= ui_state.MAX_FILE_BYTES
    # The sizes the client keeps today (scroll: NAV_LIMIT 50) never exceed the server caps.
    assert limits["max_items"]["conversation_scroll"] >= 50
    at_cap = [[f"c{i}", 1] for i in range(limits["max_items"]["conversation_scroll"])]
    assert patch(api, conversation_scroll=at_cap).status_code == 200


def test_write_is_atomic_private_and_leaves_no_temp_file(api, cfg):
    patch(api, theme="paper")
    path = stored_file(cfg)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert sorted(p.name for p in path.parent.iterdir()) == ["preferences.json"]
    assert json.loads(path.read_text())["values"] == {"theme": "paper"}


def test_corrupt_file_loads_defaults_and_keeps_a_timestamped_bak(api, cfg):
    patch(api, theme="paper")
    path = stored_file(cfg)
    path.write_text("{not json")
    body = api.get("/v1/ui-state").json()
    assert body["values"] == {} and body["read_only"] is False
    backups = list(path.parent.glob("preferences.json.*.bak"))
    assert len(backups) == 1 and backups[0].read_text() == "{not json"
    assert patch(api, theme="graphite").json()["values"] == {"theme": "graphite"}


def test_stored_unknown_and_invalid_fields_are_dropped_and_the_rest_loads(api, cfg):
    patch(api, theme="paper")
    stored_file(cfg).write_text(json.dumps({"version": 9, "values": {
        "theme": "graphite", "future_key": 1, "reading_size": "99",
        "chat_selection": {"model": "m", "effort": 7, "extra": 1},
        "project_expanded": {"p": True, "q": "yes"},
    }}))
    assert api.get("/v1/ui-state").json()["values"] == {
        "theme": "graphite", "chat_selection": {"model": "m"}, "project_expanded": {"p": True}}


def test_stored_maps_over_cap_keep_the_most_recent_entries(api, cfg):
    patch(api, theme="paper")
    entries = [[f"c{i}", i] for i in range(150)]
    stored_file(cfg).write_text(json.dumps({"version": 1, "values": {"conversation_scroll": entries}}))
    assert api.get("/v1/ui-state").json()["values"]["conversation_scroll"] == entries[-100:]


def test_concurrent_patches_on_different_keys_both_persist(api):
    keys = list(SAMPLES)
    errors = []

    def write(key):
        try:
            errors.append(patch(api, **{key: SAMPLES[key][0]}).status_code)
        except Exception as exc:  # pragma: no cover - reported by the assertion below
            errors.append(repr(exc))

    threads = [threading.Thread(target=write, args=(key,)) for key in keys]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == [200] * len(keys)
    assert api.get("/v1/ui-state").json()["values"] == {k: SAMPLES[k][0] for k in keys}


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_read_only_store_is_reported_and_patch_has_a_specific_error(api, cfg):
    patch(api, theme="paper")
    folder = stored_file(cfg).parent
    folder.chmod(0o500)
    try:
        body = api.get("/v1/ui-state").json()
        assert body["read_only"] is True and body["values"] == {"theme": "paper"}
        response = patch(api, theme="graphite")
        assert (response.status_code, response.json()["code"]) == (409, "ui_state_read_only")
        assert response.json()["retryable"] is False
        assert [p.name for p in folder.iterdir()] == ["preferences.json"]
    finally:
        folder.chmod(0o700)


@pytest.mark.parametrize("method", ["GET", "PATCH"])
def test_a_client_that_is_not_the_local_owner_is_refused(cfg, method):
    with TestClient(create_app(cfg), headers={"Authorization": "Bearer a"}) as guest:
        response = guest.request(method, "/v1/ui-state", json={"values": {}} if method == "PATCH" else None)
    assert (response.status_code, response.json()["code"]) == (403, "ui_state_local_only")
    assert not list(Path(cfg["state_dir"]).glob("ui-state/**/*.json"))


def test_unauthenticated_and_foreign_origin_are_refused(cfg):
    with TestClient(create_app(cfg)) as anonymous:
        assert anonymous.get("/v1/ui-state").status_code == 401
    with TestClient(create_app(cfg), headers={"Authorization": "Bearer local", "Origin": "http://evil.example"}) as client:
        assert client.patch("/v1/ui-state", json={"values": {}}).json()["code"] == "origin_denied"
