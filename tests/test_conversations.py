"""Persistent conversation-title API behavior."""

import hashlib
import json

import pytest
from starlette.testclient import TestClient

from agent_service.app import create_app


@pytest.fixture
def api(tmp_path):
    config = {
        "state_dir": str(tmp_path),
        "origins": ["http://testserver"],
        "projects": {"shared": {}},
        "clients": {
            name: {"sha256": hashlib.sha256(name.encode()).hexdigest(), "projects": ["shared"]}
            for name in ("alice", "bob")
        },
        "services": {
            "codex": {
                "enabled": True,
                "models": ["fixture"],
                "projects": ["shared"],
                "permissions": {},
            }
        },
        "codex_models": {"fixture": ["low"]},
    }
    app = create_app(config)
    client = TestClient(app, headers={"Authorization": "Bearer alice"})
    with app.state.service.db:
        app.state.service.db.execute(
            "INSERT INTO jobs(id,project,owner,state,created,payload,result,idem,digest) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                "conversation-1",
                "shared",
                "alice",
                "completed",
                1,
                json.dumps({"prompt": "Original prompt"}),
                json.dumps({}),
                None,
                "fixture",
            ),
        )
    yield client
    client.close()
    app.state.service.db.close()


def test_rename_persists_in_list_without_changing_prompt(api):
    response = api.patch("/v1/conversations/conversation-1", json={"title": "  Renamed  "})
    assert response.status_code == 200
    assert response.json() == {"id": "conversation-1", "title": "Renamed"}
    assert api.get("/v1/conversations").json()["conversations"][0]["title"] == "Renamed"
    assert (
        api.get("/v1/conversations/conversation-1").json()["turns"][0]["request"]["prompt"]
        == "Original prompt"
    )


def test_rename_requires_conversation_owner(api):
    response = api.patch(
        "/v1/conversations/conversation-1",
        headers={"Authorization": "Bearer bob"},
        json={"title": "Private"},
    )
    assert response.status_code == 403


@pytest.mark.parametrize(
    "title", ["", "   ", "\t\n", "\u00a0", None, True, 5, [], {}, "x" * 101, "🐋" * 101]
)
def test_rename_rejects_invalid_title(api, title):
    assert (
        api.patch("/v1/conversations/conversation-1", json={"title": "Preserved"}).status_code
        == 200
    )
    response = api.patch("/v1/conversations/conversation-1", json={"title": title})
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_conversation_title"
    assert api.get("/v1/conversations").json()["conversations"][0]["title"] == "Preserved"


@pytest.mark.parametrize("title", ["a", "x" * 100, "🐋" * 100, "Café <naïve> & 123 🐋"])
def test_rename_accepts_allowed_characters_and_trims_before_length_check(api, title):
    response = api.patch("/v1/conversations/conversation-1", json={"title": "  " + title + "  "})
    assert response.status_code == 200
    assert response.json()["title"] == title
    assert api.get("/v1/conversations").json()["conversations"][0]["title"] == title


def test_rename_requires_title(api):
    response = api.patch("/v1/conversations/conversation-1", json={})
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_conversation_title"
    assert api.get("/v1/conversations").json()["conversations"][0]["title"] == "Original prompt"


def test_message_attachment_metadata_survives_history_and_job_read(api):
    service = api.app.state.service
    with service.db:
        for fid, owner, name, pages in [
            ("image", "alice", "photo.png", [{"media_type": "image/png"}]),
            ("doc", "alice", "notes.txt", []),
            ("private", "bob", "private.png", [{"media_type": "image/png"}]),
        ]:
            service.db.execute(
                "INSERT INTO files(id,project,owner,name,size,hash,pages) VALUES(?,?,?,?,?,?,?)",
                (fid, "shared", owner, name, 1, "hash", json.dumps(pages)),
            )
        service.db.execute(
            "UPDATE jobs SET payload=? WHERE id=?",
            (
                json.dumps(
                    {"prompt": "Inspect", "file_ids": ["image", "doc", "private", "missing"]}
                ),
                "conversation-1",
            ),
        )
    expected = [
        {
            "id": "image",
            "name": "photo.png",
            "preview_url": "/v1/files/image/preview",
            "media_type": "image/png",
        },
        {"id": "doc", "name": "notes.txt"},
    ]
    assert api.get("/v1/conversations/conversation-1").json()["turns"][0]["attachments"] == expected
    assert api.get("/v1/jobs/conversation-1").json()["attachments"] == expected


def test_native_title_uses_root_prompt_and_explicit_rename(api):
    service = api.app.state.service
    root = dict(service.db.execute("SELECT * FROM jobs WHERE id='conversation-1'").fetchone())
    child = dict(
        root, id="child", payload=json.dumps({"prompt": "Follow up", "parent_job_id": root["id"]})
    )
    assert service.conversation_title(child) == "Original prompt"
    api.patch("/v1/conversations/conversation-1", json={"title": "Shared title"})
    assert service.conversation_title(child) == "Shared title"


def test_terminal_conversation_exposes_gate_audit_for_reload(api):
    service = api.app.state.service
    with service.db:
        service.gates.repository.create(
            "question",
            "conversation-1",
            {
                "gate_id": "question",
                "question": "Choose?",
                "options": [{"id": "one", "label": "One"}],
            },
        )
        service.gates.repository.close("question", "invalidated")
    result = api.get("/v1/conversations/conversation-1")
    assert result.status_code == 200
    assert result.json()["turns"][0]["gates"][0]["state"] == "invalidated"
    assert (
        api.get(
            "/v1/conversations/conversation-1", headers={"Authorization": "Bearer bob"}
        ).status_code
        == 403
    )


def add_turn(service, job_id, prompt, answer, created):
    with service.db:
        service.db.execute(
            "INSERT INTO jobs(id,project,owner,state,created,payload,result,idem,digest) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                job_id,
                "shared",
                "alice",
                "completed",
                created,
                json.dumps({"prompt": prompt, "backend": "codex", "model": "fixture"}),
                json.dumps({"answer": answer}),
                None,
                job_id,
            ),
        )


def test_search_finds_text_inside_answers_and_prompts(api):
    add_turn(
        api.app.state.service,
        "answered",
        "@@planner Summarize the quarterly report. Then mail it.",
        "The Kestrel migration finishes on Friday, café included.",
        2,
    )
    found = api.get("/v1/conversations", params={"q": "KESTREL"}).json()["conversations"]
    assert [c["id"] for c in found] == ["answered"]
    assert "Kestrel migration" in found[0]["snippet"]
    assert found[0]["title"] == "Summarize the quarterly report."
    assert [c["id"] for c in api.get("/v1/conversations", params={"q": "CAFE"}).json()["conversations"]] == ["answered"]
    by_prompt = api.get("/v1/conversations", params={"q": "quarterly"}).json()["conversations"]
    assert "quarterly report" in by_prompt[0]["snippet"]
    assert api.get("/v1/conversations", params={"q": "absent-term"}).json() == {"conversations": []}
    # A query shorter than two characters would match nearly everything: refused.
    for short in ("k", " k ", "", "é"):
        refused = api.get("/v1/conversations", params={"q": short})
        assert (refused.status_code, refused.json()["code"]) == (400, "search_query_too_short")
    # Without q the list is unchanged and carries no snippet.
    listed = api.get("/v1/conversations").json()["conversations"]
    assert {c["id"] for c in listed} == {"conversation-1", "answered"}
    assert all("snippet" not in c for c in listed)


def test_search_reads_a_bounded_window_off_the_event_loop(api, monkeypatch):
    import asyncio

    from agent_service.routes import conversations

    service = api.app.state.service
    for index in range(4):
        add_turn(service, f"turn-{index}", "Question", "Kestrel " + "x" * 100, 10 + index)
    scanning = []
    original = conversations.search_snippets

    def recorded(candidates, needle):
        try:
            asyncio.get_running_loop()
            scanning.append("event loop")
        except RuntimeError:
            scanning.append("worker thread")
        return original(candidates, needle)

    monkeypatch.setattr(conversations, "search_snippets", recorded)
    monkeypatch.setattr(conversations, "SEARCH_MAX_CONVERSATIONS", 2)
    found = api.get("/v1/conversations", params={"q": "kestrel"}).json()
    # Only the newest conversations are read, and the answer says the window was cut.
    assert [c["id"] for c in found["conversations"]] == ["turn-3", "turn-2"]
    assert found["limited"] is True
    assert scanning == ["worker thread"]
    monkeypatch.setattr(conversations, "SEARCH_MAX_CONVERSATIONS", 500)
    monkeypatch.setattr(conversations, "SEARCH_MAX_BYTES", 250)
    found = api.get("/v1/conversations", params={"q": "kestrel"}).json()
    assert [c["id"] for c in found["conversations"]] == ["turn-3"]
    assert found["limited"] is True
    monkeypatch.setattr(conversations, "SEARCH_MAX_BYTES", 16 * 1024 * 1024)
    assert "limited" not in api.get("/v1/conversations", params={"q": "kestrel"}).json()


def test_search_is_rate_limited_per_client(api, monkeypatch):
    from agent_service.routes import conversations

    monkeypatch.setattr(conversations, "SEARCHES_PER_MINUTE", 2)
    for _ in range(2):
        assert api.get("/v1/conversations", params={"q": "kestrel"}).status_code == 200
    limited = api.get("/v1/conversations", params={"q": "kestrel"})
    assert (limited.status_code, limited.json()["code"]) == (429, "search_rate_limit")
    # Listing without a query is not a search and is not counted.
    assert api.get("/v1/conversations").status_code == 200
    other = api.get(
        "/v1/conversations", params={"q": "kestrel"}, headers={"Authorization": "Bearer bob"}
    )
    assert other.status_code == 200


def test_search_is_limited_to_the_callers_conversations(api):
    add_turn(api.app.state.service, "mine", "Question", "Kestrel", 2)
    found = api.get(
        "/v1/conversations", params={"q": "kestrel"}, headers={"Authorization": "Bearer bob"}
    )
    assert found.status_code == 200
    assert found.json() == {"conversations": []}


@pytest.mark.parametrize(
    ("prompt", "title"),
    [
        ("@@planner Summarize the quarterly report. Then mail it.", "Summarize the quarterly report."),
        ("First line of a long request\nsecond line", "First line of a long request"),
        ("Hi. please plan the whole migration for me", "Hi. please plan the whole migration for me"),
        ("@@only @@markers", "Conversation"),
        ("x" * 300, "x" * 100),
    ],
)
def test_default_title_is_a_readable_first_sentence(prompt, title):
    from agent_service.resources import conversation_title

    assert conversation_title(prompt) == title


# Archive, Delete permanently and the storage caps (decisions D31 and D33, PRD-R4-6).
PURGED_THREAD = "0199aaaa-1111-7222-8333-944445555666"
KEPT_THREAD = "0199bbbb-1111-7222-8333-944445555666"


@pytest.fixture
def retention(tmp_path):
    state = tmp_path / "state"
    config = {
        "state_dir": str(state),
        "provider_homes": str(state / "providers"),
        "origins": ["http://testserver"],
        "projects": {"shared": {}},
        "uploads_enabled": True,
        "clients": {
            name: {"sha256": hashlib.sha256(name.encode()).hexdigest(), "projects": ["shared"]}
            for name in ("alice", "bob")
        },
        "services": {
            "codex": {
                "enabled": True,
                "models": ["fixture"],
                "projects": ["shared"],
                "permissions": {"upload": True, "read": True},
            }
        },
        "codex_models": {"fixture": ["low"]},
    }
    app = create_app(config)
    client = TestClient(app, headers={"Authorization": "Bearer alice"})
    yield client, app.state.service, state
    client.close()
    app.state.service.db.close()


def seed_turn(
    service, job, *, parent=None, files=(), thread=None, owner="alice", state="completed"
):
    payload = {"prompt": "Secret prompt " + job, "backend": "codex", "model": "fixture"}
    payload.update({"parent_job_id": parent} if parent else {}, file_ids=list(files))
    result = {"answer": "Secret answer " + job, "metrics": {"input_tokens": 11, "output_tokens": 7}}
    result.update({"thread_id": thread} if thread else {})
    created = service.db.execute("SELECT count(*) FROM jobs").fetchone()[0]
    with service.db:
        service.db.execute(
            "INSERT INTO jobs(id,project,owner,state,created,payload,result,idem,digest) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                job,
                "shared",
                owner,
                state,
                created,
                json.dumps(payload),
                json.dumps(result),
                None,
                "x",
            ),
        )
        service.message_repository.add_event(
            job, 1, "answer_delta", json.dumps({"text": "Secret answer " + job})
        )


def upload(client, data, name):
    response = client.post(
        "/v1/files?project_id=shared", headers={"X-Filename": name}, content=data
    )
    assert response.status_code == 201, response.text
    return response.json()["file_id"]


def listed(client, **params):
    return [c["id"] for c in client.get("/v1/conversations", params=params).json()["conversations"]]


def count(service, table, column, ids):
    marks = ",".join("?" for _ in ids)
    return service.db.execute(
        f"SELECT count(*) FROM {table} WHERE {column} IN ({marks})", list(ids)
    ).fetchone()[0]


def test_deleted_conversation_unreadable_and_purged(retention):
    client, service, state = retention
    shared = b"The same bytes in two conversations"
    kept_file = upload(client, shared, "kept.txt")
    gone_file = upload(client, shared, "gone.txt")
    own_file = upload(client, b"Only the purged conversation has this", "own.txt")
    seed_turn(service, "purged-root", files=[gone_file, own_file], thread=PURGED_THREAD)
    seed_turn(service, "purged-next", parent="purged-root", files=[gone_file])
    seed_turn(service, "kept-root", files=[kept_file], thread=KEPT_THREAD)
    session = state / "sessions" / "purged-root" / "codex"
    session.mkdir(parents=True)
    (session / "native-thread.json").write_text(json.dumps({"id": PURGED_THREAD}))
    rollouts = state / "providers" / "home" / ".codex" / "sessions" / "2026" / "10" / "04"
    rollouts.mkdir(parents=True)
    purged_copy = rollouts / f"rollout-2026-10-04T10-00-00-{PURGED_THREAD}.jsonl"
    kept_copy = rollouts / f"rollout-2026-10-04T11-00-00-{KEPT_THREAD}.jsonl"
    for copy in (purged_copy, kept_copy):
        copy.write_text('{"text":"provider copy"}\n')

    response = client.delete("/v1/conversations/purged-root")

    assert response.status_code == 200, response.text
    assert response.json() == {"id": "purged-root", "deleted": True, "turns": 2}
    jobs = ("purged-root", "purged-next")
    for job in jobs:
        for suffix in ("", "/events", "/spans?include_content=true", "/artifacts/result.json"):
            assert client.get("/v1/jobs/" + job + suffix).status_code == 404, suffix
    assert client.get("/v1/conversations/purged-root").status_code == 404
    assert count(service, "jobs", "id", jobs) == 0
    assert count(service, "events", "job", jobs) == 0
    assert count(service, "files", "id", (gone_file, own_file)) == 0
    assert not (state / "files" / "shared" / gone_file).exists()
    assert not (state / "files" / "shared" / own_file).exists()
    assert not (state / "sessions" / "purged-root").exists()
    assert not purged_copy.exists()
    assert kept_copy.exists()
    # The kept conversation's upload shared the bytes by sha256 and survives whole.
    assert (state / "files" / "shared" / kept_file / "source").read_bytes() == shared
    assert client.get("/v1/conversations/kept-root").status_code == 200
    assert service.message_repository.project_bytes("shared") == len(shared)
    audit = (state / "purged-turns.jsonl").read_text()
    assert "Secret" not in audit
    lines = [json.loads(line) for line in audit.splitlines()]
    assert {line["job_id"] for line in lines} == set(jobs)
    assert {(line["model"], line["input_tokens"], line["output_tokens"]) for line in lines} == {
        ("fixture", 11, 7)
    }


def test_archive_then_unarchive_restores_full_history(retention):
    client, service, _ = retention
    seed_turn(service, "root")
    seed_turn(service, "next", parent="root")

    archived = client.patch("/v1/conversations/root", json={"archived": True})

    assert archived.status_code == 200
    assert archived.json() == {"id": "root", "archived": True}
    assert listed(client) == []
    assert listed(client, archived="true") == ["root"]
    assert client.get("/v1/conversations/root").status_code == 404
    restored = client.patch("/v1/conversations/root", json={"archived": False})
    assert restored.json() == {"id": "root", "archived": False}
    assert listed(client) == ["root"]
    assert listed(client, archived="true") == []
    turns = client.get("/v1/conversations/root").json()["turns"]
    assert [turn["request"]["prompt"] for turn in turns] == [
        "Secret prompt root",
        "Secret prompt next",
    ]
    assert client.get("/v1/jobs/next/events?format=json").json()["events"][0]["data"] == {
        "text": "Secret answer next"
    }


def test_archived_conversation_can_be_deleted_permanently(retention):
    client, service, _ = retention
    seed_turn(service, "root")
    client.patch("/v1/conversations/root", json={"archived": True})

    assert client.delete("/v1/conversations/root").status_code == 200
    assert listed(client, archived="true") == []
    assert client.get("/v1/jobs/root").status_code == 404


def test_guests_archive_and_delete_only_their_own_conversations(retention):
    client, service, _ = retention
    bob = {"Authorization": "Bearer bob"}
    seed_turn(service, "alice-root")
    seed_turn(service, "bob-root", owner="bob")

    assert (
        client.patch(
            "/v1/conversations/alice-root", headers=bob, json={"archived": True}
        ).status_code
        == 403
    )
    assert client.delete("/v1/conversations/alice-root", headers=bob).status_code == 403
    assert listed(client) == ["alice-root"]
    assert count(service, "events", "job", ("alice-root",)) == 1
    assert (
        client.patch("/v1/conversations/bob-root", headers=bob, json={"archived": True}).status_code
        == 200
    )
    assert client.delete("/v1/conversations/bob-root", headers=bob).status_code == 200
    assert count(service, "jobs", "id", ("bob-root",)) == 0


@pytest.mark.parametrize("value", ["true", 1, None])
def test_archive_flag_must_be_a_boolean(retention, value):
    client, service, _ = retention
    seed_turn(service, "root")
    response = client.patch("/v1/conversations/root", json={"archived": value})
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_archived"


def test_running_conversation_is_neither_archived_nor_deleted(retention):
    client, service, _ = retention
    seed_turn(service, "root", state="running")
    assert client.patch("/v1/conversations/root", json={"archived": True}).status_code == 409
    assert client.delete("/v1/conversations/root").status_code == 409
    assert listed(client) == ["root"]
    assert count(service, "jobs", "id", ("root",)) == 1


def test_run_cap_counts_only_kept_turns(retention):
    client, service, _ = retention
    seed_turn(service, "root")
    seed_turn(service, "next", parent="root")
    with service.db:
        service.db.executemany(
            "INSERT INTO jobs(id,project,owner,state,created,payload) VALUES(?,?,?,?,?,?)",
            [(f"old-{n}", "shared", "bob", "completed", 1, "{}") for n in range(998)],
        )
    turn = {"project_id": "shared", "backend": "codex", "model": "fixture", "effort": "low"}
    assert client.post("/v1/jobs", json={**turn, "prompt": "Full"}).json()["code"] == (
        "job_storage_limit"
    )
    storage = client.get("/v1/storage", params={"project_id": "shared"}).json()
    assert storage["runs"] == {"used": 1000, "limit": 1000}
    assert storage["warning"] is True
    # Archived turns are kept content: only a permanent delete frees their place.
    client.patch("/v1/conversations/root", json={"archived": True})
    assert client.post("/v1/jobs", json={**turn, "prompt": "Still full"}).status_code == 429

    assert client.delete("/v1/conversations/root").status_code == 200

    assert client.post("/v1/jobs", json={**turn, "prompt": "Room again"}).status_code == 202


def test_storage_reports_use_against_the_caps(retention):
    client, service, _ = retention
    data = b"Counted once"
    upload(client, data, "one.txt")
    upload(client, data, "two.txt")
    storage = client.get("/v1/storage", params={"project_id": "shared"})
    assert storage.status_code == 200
    assert storage.json() == {
        "project_id": "shared",
        "runs": {"used": 0, "limit": 1000},
        "bytes": {"used": len(data), "limit": 2 * 1024**3},
        "warning": False,
    }
    assert client.get("/v1/storage", params={"project_id": "elsewhere"}).status_code == 403


def test_capability_card_retention_matches_archive_and_permanent_delete(retention):
    client, _, _ = retention
    text = client.get("/.well-known/agent-capabilities.json").json()["retention"]
    assert "Archive" in text and "Unarchive" in text and "Delete permanently" in text
    assert "hidden on deletion" not in text and "admin handles" not in text


def test_purge_never_reaches_the_personal_codex_or_claude_setup(tmp_path, monkeypatch):
    from pathlib import Path

    from agent_service.services import retention

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    ids = {PURGED_THREAD}
    for folder in (".codex/sessions", ".claude/projects", "state/providers/home/.codex"):
        (tmp_path / folder).mkdir(parents=True)
        (tmp_path / folder / f"rollout-{PURGED_THREAD}.jsonl").write_text("{}")

    assert retention.provider_copies(tmp_path, ids) == []
    assert retention.provider_copies(tmp_path / ".codex", ids) == []
    assert retention.provider_copies(tmp_path / ".claude", ids) == []
    owned = tmp_path / "state" / "providers"
    assert retention.provider_copies(owned, ids) == [
        owned / "home" / ".codex" / f"rollout-{PURGED_THREAD}.jsonl"
    ]
