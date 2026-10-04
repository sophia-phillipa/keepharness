"""A long attachment is inlined as an excerpt: the model can read the rest and the user is told."""

import asyncio
import json
from unittest.mock import patch

from starlette.testclient import TestClient
from test_project_browser import config

from adapters import run_native as run
from agent_service.app import create_app
from agent_service.services.conversation_service import EXCERPT_CHARS


def claude_arguments(tmp_path, *, extracted):
    project = tmp_path / "project"
    project.mkdir()
    session = tmp_path / "session"
    if extracted:
        (session / "attachments").mkdir(parents=True)
    exe = tmp_path / "fake-claude"
    exe.write_text("""#!/usr/bin/env python3
import json,sys
item=json.loads(sys.stdin.readline())
print(json.dumps({'type':'result','subtype':'success','result':json.dumps(sys.argv), 'session_id':'fixture'}),flush=True)
""")
    exe.chmod(0o700)

    async def approve(*args):
        raise AssertionError("No real inference")

    with (
        patch("adapters.claude.native.configurations", return_value={"claude": {}}),
        patch("adapters.claude.native.inventory", return_value={"claude": []}),
    ):
        result = asyncio.run(
            run(
                {"binary": str(exe)},
                "Summarize the attachment",
                lambda *args: None,
                {"root": str(project), "permissions": {"read": True}},
                "fixture",
                "configured",
                session,
                "claude",
                approve,
            )
        )
    return json.loads(result["answer"]), session


def test_claude_can_read_the_extracted_attachment_text_without_an_approval_card(tmp_path):
    args, session = claude_arguments(tmp_path, extracted=True)
    offset = args.index("--add-dir") + 1
    assert args[offset] == str((session / "attachments").resolve())
    assert args[args.index("--permission-mode") + 1] == "default"


def test_claude_gets_no_extra_directory_before_any_attachment_was_extracted(tmp_path):
    args, _ = claude_arguments(tmp_path, extracted=False)
    assert "--add-dir" not in args


def test_upload_and_history_mark_the_files_that_were_only_excerpted(tmp_path):
    app = create_app(config(tmp_path))
    client = TestClient(app, headers={"Authorization": "Bearer a"})
    try:
        uploads = {}
        for name, text in {
            "long.txt": "x" * (EXCERPT_CHARS + 1),
            "short.txt": "x" * EXCERPT_CHARS,
        }.items():
            response = client.post(
                "/v1/files?project_id=p&backend=codex&model=fixture",
                content=text,
                headers={"X-Filename": name},
            )
            assert response.status_code == 201, response.text
            uploads[name] = response.json()
        assert uploads["long.txt"]["excerpt"] is True
        assert "excerpt" not in uploads["short.txt"]
        payload = {
            "project_id": "p",
            "prompt": "Summarize",
            "backend": "codex",
            "model": "fixture",
            "effort": "low",
            "file_ids": [item["file_id"] for item in uploads.values()],
        }
        job = client.post("/v1/jobs", json=payload)
        assert job.status_code == 202, job.text
        attachments = client.get("/v1/jobs/" + job.json()["job_id"]).json()["attachments"]
        assert {item["name"]: item.get("excerpt") for item in attachments} == {
            "long.txt": True,
            "short.txt": None,
        }
    finally:
        client.close()
        app.state.service.db.close()
