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
    assert api.get("/v1/conversations", params={"q": "k"}).json() == {"conversations": []}
    # Without q the list is unchanged and carries no snippet.
    listed = api.get("/v1/conversations").json()["conversations"]
    assert {c["id"] for c in listed} == {"conversation-1", "answered"}
    assert all("snippet" not in c for c in listed)


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
