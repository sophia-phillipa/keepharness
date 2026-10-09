"""Read side of a turn's file changes (issue #57, D-053): GET /v1/jobs/{job}/file-changes.

Rows are written through ``service.event`` (the path the adapters use), so each test starts from the
stored ``turn_edit`` shape that WP1 produces, and reads back through the repository, the service and
the route.
"""

import builtins
import hashlib
import io
import json

import pytest
from starlette.testclient import TestClient

from agent_service.app import create_app

ALICE = {"Authorization": "Bearer alice"}
BOB = {"Authorization": "Bearer bob"}


def config(state_dir):
    return {
        "state_dir": str(state_dir),
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


@pytest.fixture
def api(tmp_path):
    client = TestClient(create_app(config(tmp_path)), headers=ALICE)
    yield client
    client.close()


def add_job(api, job, state="completed", retry_of=None):
    payload = {"prompt": f"prompt of {job}", "backend": "codex", "model": "fixture"}
    if retry_of:
        payload["retry_of"] = retry_of
    service = api.app.state.service
    with service.db:
        service.db.execute(
            "INSERT INTO jobs(id,project,owner,state,created,payload,result,idem,digest) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (job, "shared", "alice", state, 10, json.dumps(payload), json.dumps({}), None, job),
        )


def store(api, job, *items):
    for item in items:
        api.app.state.service.event(job, "turn_edit", item)


def edit(path, op="modified", diff_state="diff", diff="+x\n", tool_id="t", **extra):
    return {
        "path": path,
        "op": op,
        "source": "codex",
        "tool": "fileChange",
        "diff": diff,
        "diff_state": diff_state,
        "tool_id": tool_id,
        **extra,
    }


def changes(api, job, headers=None):
    return api.get(f"/v1/jobs/{job}/file-changes", headers=headers)


def paths(body):
    return [item["path"] for item in body["files"]]


def test_each_job_lists_only_its_own_edits(api):
    add_job(api, "turn-a")
    add_job(api, "turn-b")
    store(api, "turn-a", edit("src/a.py"))
    store(api, "turn-b", edit("src/b.py", tool_id="t2"))

    assert paths(changes(api, "turn-a").json()) == ["src/a.py"]
    assert paths(changes(api, "turn-b").json()) == ["src/b.py"]


def test_turn_without_edits_after_an_editing_turn_is_empty(api):
    add_job(api, "editing")
    add_job(api, "reading")
    store(api, "editing", edit("src/a.py"))

    body = changes(api, "reading").json()

    assert body["state"] == "none"
    assert body["files"] == []
    assert body["truncated"] is False
    assert body["shell_unattributed"] is False


def test_files_group_by_path_in_first_appearance_order_with_file_op(api):
    add_job(api, "turn")
    store(
        api,
        "turn",
        edit("x.py", op="modified", tool_id="1"),
        edit("y.py", op="created", tool_id="2"),
        edit("x.py", op="modified", diff="+second\n", tool_id="3"),
        edit("z.py", op="modified", tool_id="4"),
        edit("z.py", op="deleted", diff=None, diff_state="unavailable", tool_id="5"),
    )

    body = changes(api, "turn").json()

    assert paths(body) == ["x.py", "y.py", "z.py"]
    assert [e["diff"] for e in body["files"][0]["edits"]] == ["+x\n", "+second\n"]
    assert body["files"][0]["op"] == "modified"
    assert body["files"][1]["op"] == "created"
    assert body["files"][2]["op"] == "deleted"


def test_diff_text_is_returned_only_for_diff_and_partial_states(api):
    add_job(api, "turn")
    store(
        api,
        "turn",
        edit("full.py", diff="+full\n", diff_state="diff"),
        edit("part.py", diff="-a\n+b\n", diff_state="partial"),
        edit("blob.bin", diff="stale", diff_state="binary"),
        edit("big.py", diff="stale", diff_state="oversized"),
        edit("lost.py", diff="stale", diff_state="unavailable"),
    )

    diffs = {
        item["path"]: item["edits"][0]["diff"] for item in changes(api, "turn").json()["files"]
    }

    assert diffs == {
        "full.py": "+full\n",
        "part.py": "-a\n+b\n",
        "blob.bin": None,
        "big.py": None,
        "lost.py": None,
    }


def test_truncation_marker_sets_truncated_and_is_not_listed_as_a_file(api):
    add_job(api, "turn")
    store(api, "turn", edit("a.py"), {"truncated": True})

    body = changes(api, "turn").json()

    assert body["truncated"] is True
    assert paths(body) == ["a.py"]
    assert None not in paths(body)


def test_shell_run_marks_shell_unattributed(api):
    add_job(api, "codex-shell")
    add_job(api, "claude-shell")
    add_job(api, "reads-only")
    api.app.state.service.event("codex-shell", "tool_start", {"tool": "commandExecution"})
    api.app.state.service.event("claude-shell", "tool_start", {"tool": "Bash"})
    api.app.state.service.event("reads-only", "tool_start", {"tool": "repository_read"})

    assert changes(api, "codex-shell").json()["shell_unattributed"] is True
    assert changes(api, "claude-shell").json()["shell_unattributed"] is True
    assert changes(api, "reads-only").json()["shell_unattributed"] is False


@pytest.mark.parametrize("state", ["cancelled", "failed", "interrupted"])
def test_stopped_jobs_keep_what_was_captured(api, state):
    add_job(api, "stopped", state=state)
    store(api, "stopped", edit("partial.py"))

    body = changes(api, "stopped").json()

    assert body["job_state"] == state
    assert paths(body) == ["partial.py"]
    assert body["state"] == "captured"


def test_running_job_returns_edits_stored_so_far(api):
    add_job(api, "live", state="running")
    store(api, "live", edit("first.py"))

    body = changes(api, "live").json()

    assert changes(api, "live").status_code == 200
    assert body["job_state"] == "running"
    assert paths(body) == ["first.py"]


def test_edits_survive_a_restart_of_the_harness(api, tmp_path):
    add_job(api, "turn")
    store(api, "turn", edit("kept.py", diff="+kept\n"))
    before = changes(api, "turn").json()
    api.close()

    restarted = TestClient(create_app(config(tmp_path)), headers=ALICE)
    try:
        after = changes(restarted, "turn").json()
    finally:
        restarted.close()

    assert after == before
    assert paths(after) == ["kept.py"]


def test_retry_child_and_its_failed_source_each_show_only_their_own_edits(api):
    add_job(api, "source", state="failed")
    add_job(api, "retry", retry_of="source")
    store(api, "source", edit("from-source.py", tool_id="s1"))
    store(api, "retry", edit("from-retry.py", tool_id="r1"))

    assert paths(changes(api, "source").json()) == ["from-source.py"]
    assert paths(changes(api, "retry").json()) == ["from-retry.py"]


def test_another_owners_job_and_a_missing_job_both_return_404(api):
    add_job(api, "alices")
    store(api, "alices", edit("secret.py"))

    foreign = changes(api, "alices", headers=BOB)
    missing = changes(api, "no-such-job")

    assert foreign.status_code == 404
    assert missing.status_code == 404
    assert foreign.json()["code"] == missing.json()["code"] == "job_not_found"
    assert "job_not_found" in foreign.text
    assert "secret.py" not in foreign.text


def test_request_reads_no_file_from_disk(api, monkeypatch, tmp_path):
    add_job(api, "turn")
    store(api, "turn", edit("a.py"))
    (tmp_path / "a.py").write_text("on disk, never read", encoding="utf-8")
    opened = []
    real_io_open = io.open

    def spy(*args, **kwargs):
        opened.append(args[0])
        return real_io_open(*args, **kwargs)

    monkeypatch.setattr(builtins, "open", spy)
    monkeypatch.setattr(io, "open", spy)

    response = changes(api, "turn")

    assert response.status_code == 200
    assert opened == []


def test_response_shape_and_headers_match_the_contract(api):
    add_job(api, "turn")
    store(api, "turn", edit("a.py"))

    response = changes(api, "turn")
    body = response.json()

    assert response.headers["cache-control"] == "no-store"
    assert set(body) == {"job", "job_state", "state", "truncated", "shell_unattributed", "files"}
    assert body["job"] == "turn"
    assert set(body["files"][0]) == {"path", "op", "edits"}
    assert set(body["files"][0]["edits"][0]) == {"op", "diff_state", "diff", "tool", "source"}


def test_repository_reads_only_the_job_turn_edit_rows_in_order(api):
    add_job(api, "turn-a")
    add_job(api, "turn-b")
    store(api, "turn-a", edit("first.py", tool_id="1"), edit("second.py", tool_id="2"))
    store(api, "turn-b", edit("other.py"))
    messages = api.app.state.service.message_repository

    rows = messages.turn_edits("turn-a")

    assert [row["path"] for row in rows] == ["first.py", "second.py"]
    assert messages.ran_shell("turn-a") is False


def test_a_moved_file_reports_its_old_path_only_when_moved():
    from agent_service.turn_edits import shown_edit

    moved = {"op": "modified", "diff_state": "diff", "diff": "d", "moved_from": "old.py"}
    assert shown_edit(moved)["moved_from"] == "old.py"
    assert "moved_from" not in shown_edit({"op": "created", "diff_state": "diff", "diff": "d"})
