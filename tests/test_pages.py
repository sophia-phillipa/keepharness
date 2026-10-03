"""Space pages: storage, HTTP API, validation, owner and project isolation, file safety."""

import hashlib
import itertools
import json
import os
import stat
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from agent_service import pages
from agent_service.app import create_app
from agent_service.errors import APIError

ALICE = {"Authorization": "Bearer a"}
BOB = {"Authorization": "Bearer b"}
TEXT = "# Notes\n\n- one\n- two\n"


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


@pytest.fixture
def config(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    return {
        "state_dir": str(state),
        "origins": [],
        "projects": {"p": {}, "q": {}, "sem-projeto": {}},
        "clients": {
            "a": {"sha256": digest("a"), "projects": ["p", "q", "sem-projeto"]},
            "b": {"sha256": digest("b"), "projects": ["p"]},
        },
        "services": {},
    }


@pytest.fixture
def api(config):
    with TestClient(create_app(config), headers=ALICE) as client:
        yield client


@pytest.fixture
def clock(monkeypatch):
    """A timestamp that moves forward on every call, so ordering never depends on speed."""
    ticks = itertools.count(1)
    monkeypatch.setattr(pages, "timestamp", lambda: "2026-10-03T12:00:%02d.000Z" % next(ticks))


def folder_of(config, owner="a", project="p"):
    return Path(config["state_dir"]) / "pages" / digest(owner)[:16] / project


def make(api, title="Plan", body=TEXT, project="p", headers=None):
    response = api.post(
        "/v1/pages", json={"project_id": project, "title": title, "body": body}, headers=headers
    )
    assert response.status_code == 201, response.text
    return response.json()


def change(page, **fields):
    return {
        "project_id": "p",
        "title": page["title"],
        "body": page["body"],
        "revision": page["revision"],
        **fields,
    }


def remove(api, page, project="p", headers=None):
    return api.request(
        "DELETE",
        "/v1/pages/" + page["id"],
        json={"project_id": project, "revision": page["revision"]},
        headers=headers,
    )


# --------------------------------------------------------------------------- create and read


def test_create_returns_the_page_and_stores_a_private_file(api, config):
    response = api.post("/v1/pages", json={"project_id": "p", "title": " Plan ", "body": TEXT})
    assert response.status_code == 201, response.text
    assert response.headers["Cache-Control"] == "no-store"
    page = response.json()
    assert set(page) == {"id", "title", "body", "created_at", "updated_at", "revision"}
    assert len(page["id"]) == 32 and int(page["id"], 16) >= 0
    assert page["title"] == "Plan" and page["body"] == TEXT
    assert page["created_at"] == page["updated_at"] and page["created_at"].endswith("Z")
    path = folder_of(config) / (page["id"] + ".json")
    text = path.read_text(encoding="utf-8")
    assert page["revision"] == digest(text)
    stored = json.loads(text)
    assert stored == {key: page[key] for key in stored}
    assert set(stored) == {"id", "title", "body", "created_at", "updated_at"}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    for directory in (folder_of(config), folder_of(config).parent, folder_of(config).parents[1]):
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert [entry.name for entry in folder_of(config).iterdir()] == [page["id"] + ".json"]


def test_the_owner_folder_does_not_contain_the_owner_name(api, config):
    make(api)
    names = [entry.name for entry in (Path(config["state_dir"]) / "pages").iterdir()]
    assert names == [digest("a")[:16]]


def test_read_returns_the_stored_page(api):
    created = make(api)
    response = api.get("/v1/pages/" + created["id"], params={"project_id": "p"})
    assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"
    assert response.json() == created


def test_list_is_newest_first_with_a_summary_of_each_page(api, clock):
    first = make(api, title="First", body="é" * 3)
    second = make(api, title="Second", body="")
    third = make(api, title="Third", body="abc")
    api.put("/v1/pages/" + first["id"], json=change(first, title="First, edited"))
    response = api.get("/v1/pages", params={"project_id": "p"})
    assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"
    listing = response.json()["pages"]
    assert [item["title"] for item in listing] == ["First, edited", "Third", "Second"]
    assert all(set(item) == {"id", "title", "updated_at", "revision", "size"} for item in listing)
    sizes = {item["id"]: item["size"] for item in listing}
    assert sizes == {first["id"]: 6, second["id"]: 0, third["id"]: 3}
    current = api.get("/v1/pages/" + first["id"], params={"project_id": "p"}).json()
    assert listing[0]["revision"] == current["revision"]
    assert listing[0]["updated_at"] == current["updated_at"]


def test_an_empty_project_lists_no_pages_and_creates_no_folder(api, config):
    assert api.get("/v1/pages", params={"project_id": "p"}).json() == {"pages": []}
    assert not (Path(config["state_dir"]) / "pages").exists()


# --------------------------------------------------------------------------- replace and delete


def test_replace_updates_the_page_and_changes_its_revision(api, config, clock):
    created = make(api)
    response = api.put(
        "/v1/pages/" + created["id"], json=change(created, title="Plan v2", body="new")
    )
    assert response.status_code == 200, response.text
    updated = response.json()
    assert (updated["title"], updated["body"]) == ("Plan v2", "new")
    assert updated["created_at"] == created["created_at"]
    assert updated["updated_at"] > created["updated_at"]
    assert updated["revision"] != created["revision"]
    text = (folder_of(config) / (created["id"] + ".json")).read_text()
    assert updated["revision"] == digest(text)
    assert api.get("/v1/pages/" + created["id"], params={"project_id": "p"}).json() == updated


def test_a_whole_page_read_can_be_sent_back_unchanged(api):
    created = make(api)
    response = api.put("/v1/pages/" + created["id"], json={**created, "project_id": "p"})
    assert response.status_code == 200, response.text
    assert response.json()["title"] == created["title"]


def test_a_stale_revision_is_a_conflict_and_changes_nothing(api, config):
    created = make(api)
    api.put("/v1/pages/" + created["id"], json=change(created, body="first writer"))
    path = folder_of(config) / (created["id"] + ".json")
    before = path.read_text()
    stale = api.put("/v1/pages/" + created["id"], json=change(created, body="second writer"))
    assert stale.status_code == 409 and stale.json()["code"] == "page_changed"
    gone = remove(api, created)
    assert gone.status_code == 409 and gone.json()["code"] == "page_changed"
    assert path.read_text() == before


def test_delete_removes_the_file_and_then_the_page_is_gone(api, config):
    created = make(api)
    response = remove(api, created)
    assert response.status_code == 200 and response.json() == {"deleted": True}
    assert response.headers["Cache-Control"] == "no-store"
    assert list(folder_of(config).iterdir()) == []
    again = remove(api, created)
    assert again.status_code == 404 and again.json()["code"] == "page_not_found"
    assert api.get("/v1/pages/" + created["id"], params={"project_id": "p"}).status_code == 404


def test_unknown_and_malformed_ids_are_not_found(api):
    for page_id in ("0" * 32, "not-an-id", "A" * 32, "a" * 31):
        for response in (
            api.get("/v1/pages/" + page_id, params={"project_id": "p"}),
            api.put(
                "/v1/pages/" + page_id,
                json={"project_id": "p", "title": "t", "body": "", "revision": "r"},
            ),
            api.request(
                "DELETE", "/v1/pages/" + page_id, json={"project_id": "p", "revision": "r"}
            ),
        ):
            assert response.status_code == 404, response.text
            assert response.json()["code"] == "page_not_found"


def test_concurrent_writers_with_one_revision_have_one_winner(config):
    created = pages.create_page(config, "a", "p", {"title": "Plan", "body": "start"})
    barrier = threading.Barrier(8)

    def writer(number):
        barrier.wait()
        try:
            pages.replace_page(
                config,
                "a",
                "p",
                created["id"],
                {"title": "Plan", "body": "writer %d" % number, "revision": created["revision"]},
            )
        except APIError as error:
            return error.code
        return "won"

    with ThreadPoolExecutor(8) as pool:
        outcomes = list(pool.map(writer, range(8)))
    assert outcomes.count("won") == 1
    assert outcomes.count("page_changed") == 7


# --------------------------------------------------------------------------- validation

VALID = {"project_id": "p", "title": "Plan", "body": "text"}
BAD_BODIES = [
    ("title missing", {"title": None}, "title"),
    ("title number", {"title": 5}, "title"),
    ("title empty", {"title": ""}, "title"),
    ("title blank", {"title": "  \t "}, "title"),
    ("title too long", {"title": "x" * 121}, "title"),
    ("title line break", {"title": "two\nlines"}, "title"),
    ("title control", {"title": "bell\x07"}, "title"),
    ("body missing", {"body": None}, "body"),
    ("body number", {"body": 5}, "body"),
    ("body null byte", {"body": "a\x00b"}, "body"),
    ("body escape", {"body": "a\x1bb"}, "body"),
    ("body one byte over", {"body": "a" * (200 * 1024 + 1)}, "body"),
    ("body multibyte over", {"body": "é" * (100 * 1024 + 1)}, "body"),
    ("unknown field", {"folder": "x"}, "folder"),
    ("project missing", {"project_id": None}, "project_id"),
    ("project number", {"project_id": 5}, "project_id"),
    ("project traversal", {"project_id": "../p"}, "project_id"),
    ("project slash", {"project_id": "p/q"}, "project_id"),
    ("project dot", {"project_id": ".."}, "project_id"),
    ("project empty", {"project_id": ""}, "project_id"),
]


@pytest.mark.parametrize("label,override,field", BAD_BODIES, ids=[row[0] for row in BAD_BODIES])
def test_create_validation_names_the_offending_field(api, config, label, override, field):
    body = {key: value for key, value in {**VALID, **override}.items() if value is not None}
    response = api.post("/v1/pages", json=body)
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "page_invalid" and response.json()["field"] == field
    assert not (Path(config["state_dir"]) / "pages").exists()


@pytest.mark.parametrize("label,override,field", BAD_BODIES, ids=[row[0] for row in BAD_BODIES])
def test_replace_validation_names_the_offending_field(api, label, override, field):
    created = make(api)
    body = {**change(created), **override}
    body = {key: value for key, value in body.items() if value is not None}
    response = api.put("/v1/pages/" + created["id"], json=body)
    assert response.status_code == 422, response.text
    assert response.json()["field"] == field
    assert api.get("/v1/pages/" + created["id"], params={"project_id": "p"}).json() == created


def test_replace_and_delete_need_a_revision_and_the_right_id(api):
    created = make(api)
    without = {key: value for key, value in change(created).items() if key != "revision"}
    response = api.put("/v1/pages/" + created["id"], json=without)
    assert (response.status_code, response.json()["field"]) == (422, "revision")
    response = api.put("/v1/pages/" + created["id"], json=change(created, revision=7))
    assert (response.status_code, response.json()["field"]) == (422, "revision")
    response = api.put("/v1/pages/" + created["id"], json=change(created, id="0" * 32))
    assert (response.status_code, response.json()["field"]) == (422, "id")
    response = api.request("DELETE", "/v1/pages/" + created["id"], json={"project_id": "p"})
    assert (response.status_code, response.json()["field"]) == (422, "revision")


def test_reads_need_a_safe_project_id(api):
    for params in ({}, {"project_id": ""}, {"project_id": "../p"}):
        for path in ("/v1/pages", "/v1/pages/" + "0" * 32):
            response = api.get(path, params=params)
            assert response.status_code == 422, response.text
            assert response.json()["code"] == "page_invalid"
            assert response.json()["field"] == "project_id"


def test_the_limits_are_the_documented_ones():
    assert pages.MAX_PAGES == 500
    assert pages.TITLE_LENGTH == (1, 120)
    assert pages.MAX_BODY_BYTES == 200 * 1024


def test_titles_are_trimmed_and_bodies_are_kept_as_written(api):
    body = "\n\tindented\r\n  trailing  \n\n"
    created = make(api, title="  " + "t" * 120 + "  ", body=body)
    assert created["title"] == "t" * 120 and created["body"] == body
    assert make(api, title="x", body="")["body"] == ""


@pytest.mark.parametrize(
    "body",
    ["a" * (200 * 1024), "é" * (100 * 1024), '"' * (200 * 1024), "\n" * (200 * 1024)],
    ids=["ascii", "two-byte", "quotes", "line-breaks"],
)
def test_a_page_of_the_largest_size_round_trips_whatever_the_escaping(api, body):
    created = make(api, body=body)
    read = api.get("/v1/pages/" + created["id"], params={"project_id": "p"}).json()
    assert read["body"] == body
    listed = api.get("/v1/pages", params={"project_id": "p"}).json()["pages"]
    assert listed[0]["size"] == len(body.encode())
    updated = api.put("/v1/pages/" + created["id"], json=change(read, title="Still fits"))
    assert updated.status_code == 200, updated.text


def test_the_page_limit_is_per_owner_and_project(api, config, monkeypatch):
    monkeypatch.setattr(pages, "MAX_PAGES", 2)
    first, _ = make(api, title="one"), make(api, title="two")
    full = api.post("/v1/pages", json={**VALID, "title": "three"})
    assert full.status_code == 409 and full.json()["code"] == "page_limit"
    assert len(list(folder_of(config).iterdir())) == 2
    make(api, project="q")
    make(api, headers=BOB)
    assert remove(api, first).status_code == 200
    make(api, title="three")


# --------------------------------------------------------------------------- isolation


def test_another_owners_page_is_not_found_in_every_operation(api):
    created = make(api)
    assert api.get("/v1/pages", params={"project_id": "p"}, headers=BOB).json() == {"pages": []}
    reads = api.get("/v1/pages/" + created["id"], params={"project_id": "p"}, headers=BOB)
    writes = api.put(
        "/v1/pages/" + created["id"], json=change(created, body="mine now"), headers=BOB
    )
    deletes = remove(api, created, headers=BOB)
    for response in (reads, writes, deletes):
        assert response.status_code == 404 and response.json()["code"] == "page_not_found"
    assert api.get("/v1/pages/" + created["id"], params={"project_id": "p"}).json() == created


def test_two_owners_keep_separate_folders_and_limits(api, config):
    mine, theirs = make(api, title="mine"), make(api, title="theirs", headers=BOB)
    assert (folder_of(config, "a") / (mine["id"] + ".json")).is_file()
    assert (folder_of(config, "b") / (theirs["id"] + ".json")).is_file()
    assert [
        p["title"] for p in api.get("/v1/pages", params={"project_id": "p"}).json()["pages"]
    ] == ["mine"]


def test_a_page_belongs_to_the_project_it_was_created_in(api):
    created = make(api, project="p")
    assert api.get("/v1/pages", params={"project_id": "q"}).json() == {"pages": []}
    other = api.get("/v1/pages/" + created["id"], params={"project_id": "q"})
    assert other.status_code == 404 and other.json()["code"] == "page_not_found"
    assert remove(api, {**created}, project="q").status_code == 404


def test_the_no_project_scope_works_like_any_project(api, config):
    created = make(api, project="sem-projeto")
    assert (folder_of(config, "a", "sem-projeto") / (created["id"] + ".json")).is_file()
    listing = api.get("/v1/pages", params={"project_id": "sem-projeto"}).json()["pages"]
    assert [item["id"] for item in listing] == [created["id"]]


def test_a_project_the_caller_cannot_use_is_denied_without_touching_storage(api, config):
    calls = [
        api.get("/v1/pages", params={"project_id": "q"}, headers=BOB),
        api.get("/v1/pages", params={"project_id": "nowhere"}),
        api.get("/v1/pages/" + "0" * 32, params={"project_id": "q"}, headers=BOB),
        api.post("/v1/pages", json={**VALID, "project_id": "q"}, headers=BOB),
        api.put(
            "/v1/pages/" + "0" * 32,
            json=change({"title": "t", "body": "", "revision": "r"}, project_id="q"),
            headers=BOB,
        ),
        api.request(
            "DELETE",
            "/v1/pages/" + "0" * 32,
            json={"project_id": "q", "revision": "r"},
            headers=BOB,
        ),
    ]
    for response in calls:
        assert response.status_code == 403 and response.json()["code"] == "project_denied"
    assert not (Path(config["state_dir"]) / "pages").exists()


def test_every_route_needs_authentication(api):
    anonymous = {"Authorization": ""}
    calls = [
        api.get("/v1/pages", params={"project_id": "p"}, headers=anonymous),
        api.post("/v1/pages", json=VALID, headers=anonymous),
        api.get("/v1/pages/" + "0" * 32, params={"project_id": "p"}, headers=anonymous),
        api.put("/v1/pages/" + "0" * 32, json=VALID, headers=anonymous),
        api.request("DELETE", "/v1/pages/" + "0" * 32, json=VALID, headers=anonymous),
    ]
    assert [response.status_code for response in calls] == [401] * 5


# --------------------------------------------------------------------------- file safety


def test_a_symlinked_project_folder_is_refused_and_never_written_through(api, config, tmp_path):
    target = tmp_path / "elsewhere"
    target.mkdir()
    folder = folder_of(config)
    folder.parent.mkdir(parents=True)
    folder.symlink_to(target, target_is_directory=True)
    for response in (
        api.post("/v1/pages", json=VALID),
        api.get("/v1/pages", params={"project_id": "p"}),
    ):
        assert response.status_code == 500
        assert response.json()["code"] == "page_storage_unsafe"
    assert list(target.iterdir()) == []


def test_a_symlinked_page_file_is_refused_and_never_written_through(api, config, tmp_path):
    created = make(api)
    path = folder_of(config) / (created["id"] + ".json")
    outside = tmp_path / "outside.json"
    outside.write_text(path.read_text())
    path.unlink()
    path.symlink_to(outside)
    assert api.get("/v1/pages", params={"project_id": "p"}).json() == {"pages": []}
    for response in (
        api.get("/v1/pages/" + created["id"], params={"project_id": "p"}),
        api.put("/v1/pages/" + created["id"], json=change(created, body="through the link")),
        remove(api, created),
    ):
        assert response.status_code == 500 and response.json()["code"] == "page_storage_unsafe"
    assert json.loads(outside.read_text())["body"] == TEXT


def test_unusable_files_are_skipped_not_fatal(api, config):
    created = make(api)
    folder = folder_of(config)
    valid = json.loads((folder / (created["id"] + ".json")).read_text())
    other = "1" * 32
    (folder / (other + ".json")).write_text("{not json")
    (folder / ("2" * 32 + ".json")).write_text(json.dumps({**valid, "id": other}))
    (folder / ("3" * 32 + ".json")).write_text(json.dumps({**valid, "id": "3" * 32, "title": ""}))
    (folder / ("4" * 32 + ".json")).write_text(json.dumps([valid]))
    (folder / ("5" * 32 + ".json")).write_text(
        json.dumps({**valid, "id": "5" * 32, "body": "x" * 450000})
    )
    (folder / ("6" * 32 + ".json")).mkdir()
    (folder / "notes.txt").write_text("ignored")
    listing = api.get("/v1/pages", params={"project_id": "p"}).json()["pages"]
    assert [item["id"] for item in listing] == [created["id"]]
    broken = api.get("/v1/pages/" + other, params={"project_id": "p"})
    assert broken.status_code == 404 and broken.json()["code"] == "page_not_found"


def test_an_existing_folder_is_tightened_to_owner_only(config):
    folder = folder_of(config)
    folder.mkdir(mode=0o755, parents=True)
    os.chmod(folder, 0o755)
    pages.create_page(config, "a", "p", {"title": "Plan", "body": ""})
    assert stat.S_IMODE(folder.stat().st_mode) == 0o700


def test_a_failed_write_leaves_neither_a_page_nor_a_temp_file(config, monkeypatch):
    def broken(_descriptor):
        raise OSError("disk full")

    with monkeypatch.context() as patch:
        patch.setattr(os, "fsync", broken)
        with pytest.raises(OSError, match="disk full"):
            pages.create_page(config, "a", "p", {"title": "Plan", "body": ""})
    assert list(folder_of(config).iterdir()) == []


def test_a_failed_replace_keeps_the_old_page(config, monkeypatch):
    created = pages.create_page(config, "a", "p", {"title": "Plan", "body": "old"})

    def broken(*_args, **_kwargs):
        raise OSError("rename failed")

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", broken)
        with pytest.raises(OSError, match="rename failed"):
            pages.replace_page(
                config,
                "a",
                "p",
                created["id"],
                {"title": "Plan", "body": "new", "revision": created["revision"]},
            )
    assert pages.read_page(config, "a", "p", created["id"])["body"] == "old"
    assert [entry.name for entry in folder_of(config).iterdir()] == [created["id"] + ".json"]
