"""Continuation prompt: a redacted, bounded handoff text for Claude or ChatGPT."""

import hashlib
import json

import pytest
from starlette.testclient import TestClient

from agent_service import conversation_context
from agent_service.app import create_app

GITHUB_TOKEN = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6"
BEARER = "Authorization: Bearer hunter2token"
URL_SECRET = "https://deploy:s3cretpass@git.example.invalid/repo.git"
LEAKY = f"{GITHUB_TOKEN} {BEARER} {URL_SECRET}"
TARGET_LINE = "cat /home/sophia/private/target-only.txt"


def project_spec(tmp_path):
    return {
        "label": "Demo Project",
        "root": str(tmp_path / "demo"),
        "additional_roots": [str(tmp_path / "extra")],
    }


@pytest.fixture
def env(tmp_path):
    for name in ("demo", "extra"):
        (tmp_path / name).mkdir()
    config = {
        "state_dir": str(tmp_path / "state"),
        "origins": ["http://testserver"],
        "projects": {"shared": project_spec(tmp_path)},
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
    service = app.state.service
    client = TestClient(app, headers={"Authorization": "Bearer alice"})
    yield client, service, app
    client.close()
    service.db.close()


def seed(service, job, state, payload, result, owner="alice", events=()):
    with service.db:
        service.conversation_repository.insert(
            job, "shared", owner, state, 1, json.dumps(payload), json.dumps(result), None, job
        )
        for kind, data in events:
            service.db.execute(
                "INSERT INTO events(job,time,type,data) VALUES(?,?,?,?)",
                (job, 1, kind, json.dumps(data)),
            )


def seed_conversation(service):
    seed(
        service,
        "c1",
        "completed",
        {"prompt": "Build the report. " + LEAKY, "backend": "codex", "model": "fixture"},
        {"answer": "Done. " + LEAKY},
        events=[
            ("tool_start", {"tool": "Bash", "target": TARGET_LINE, "command": LEAKY}),
            (
                "tool_end",
                {"tool": "Bash", "status": "completed", "target": TARGET_LINE, "output": LEAKY},
            ),
            ("changes_applied", {"applied": True, "files": ["report.md", "src/app.py"]}),
        ],
    )
    seed(
        service,
        "c2",
        "failed",
        {"prompt": "Now fix the failure", "parent_job_id": "c1", "backend": "codex"},
        {"error": "claude_output_limit"},
    )


def get(client, cid="c1", **query):
    query.setdefault("target", "claude")
    return client.get(f"/v1/conversations/{cid}/continuation", params=query)


def test_response_shape_sections_and_header(env):
    client, service, _ = env
    seed_conversation(service)
    response = get(client, target="chatgpt")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert set(body) == {"target", "text", "turns_included", "truncated"}
    assert body["target"] == "chatgpt"
    assert body["turns_included"] == 2
    assert body["truncated"] is False
    text = body["text"]
    assert "ChatGPT" in text.splitlines()[0] and "continue" in text.splitlines()[0]
    assert "Demo Project" in text
    assert "Goal" in text and "Build the report." in text
    assert "Bash completed" in text
    assert "report.md" in text and "src/app.py" in text
    assert "claude_output_limit" in text
    assert text.index("Build the report.") < text.index("Now fix the failure")


def test_secrets_and_tool_targets_never_reach_the_text(env):
    client, service, _ = env
    seed_conversation(service)
    text = get(client).json()["text"]
    for leaked in (
        GITHUB_TOKEN,
        "hunter2token",
        "s3cretpass",
        "target-only",
        "/home/sophia/private",
    ):
        assert leaked not in text


def test_paths_included_by_default_and_omitted_on_request(env, tmp_path):
    client, service, _ = env
    seed_conversation(service)
    assert str(tmp_path / "demo") in get(client).json()["text"]
    assert str(tmp_path / "extra") in get(client).json()["text"]
    without = get(client, include_paths="0").json()["text"]
    assert "Demo Project" in without
    assert str(tmp_path) not in without


def test_attachments_by_name_and_unfinished_turn_listed_as_open(env):
    client, service, _ = env
    seed(service, "c1", "running", {"prompt": "Look at this", "file_ids": ["f1"]}, {})
    with service.db:
        service.db.execute(
            "INSERT INTO files(id,project,name,size,hash,pages,owner) VALUES(?,?,?,?,?,?,?)",
            ("f1", "shared", "notes.pdf", 1, "h", "[]", "alice"),
        )
    text = get(client).json()["text"]
    assert "notes.pdf" in text
    assert "running" in text


def test_truncation_marker_and_cap(env):
    client, service, _ = env
    seed(service, "c0", "completed", {"prompt": "first goal"}, {"answer": "a0"})
    parent = "c0"
    for index in range(1, 40):
        job = f"c{index}"
        seed(
            service,
            job,
            "completed",
            {"prompt": f"turn {index} " + "x" * 1800, "parent_job_id": parent},
            {"answer": "y" * 1800},
        )
        parent = job
    body = get(client, "c0").json()
    assert len(body["text"]) <= conversation_context.CONTINUATION_LIMIT
    assert body["truncated"] is True
    assert body["turns_included"] < 40
    assert "earlier turn" in body["text"] and "omitted" in body["text"]
    assert "first goal" in body["text"]
    assert "turn 39" in body["text"]
    assert "turn 1 " not in body["text"]


def test_unknown_target_is_400_and_foreign_conversation_404(env):
    client, service, app = env
    seed_conversation(service)
    bad = get(client, target="foo")
    assert bad.status_code == 400
    assert bad.json()["code"] == "invalid_continuation_target"
    assert get(client, target="").status_code == 400
    bob = TestClient(app, headers={"Authorization": "Bearer bob"})
    assert get(bob).status_code == 404
    assert get(client, cid="missing").status_code == 404
    bob.close()


def test_builder_is_pure_and_only_whitelists_tool_fields():
    history = [
        {
            "job_id": "j1",
            "backend": "codex",
            "model": "m",
            "state": "completed",
            "user": "goal",
            "assistant": "ok",
            "evidence": [
                {
                    "type": "tool_end",
                    "data": {"tool": "Edit", "status": "completed", "args": "SECRET-ARG"},
                }
            ],
        }
    ]
    result = conversation_context.continuation_prompt(history, {"name": "P", "paths": []}, "claude")
    assert "Edit completed" in result["text"]
    assert "SECRET-ARG" not in result["text"]
    assert result["turns_included"] == 1
