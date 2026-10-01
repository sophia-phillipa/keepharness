import json
import sqlite3

from control.dashboard import execution, snapshot


def test_visibility_uses_terminal_event_and_preserves_history(tmp_path):
    root = tmp_path / "runs"
    root.mkdir()
    db = sqlite3.connect(root / "jobs.sqlite3")
    db.executescript(
        "CREATE TABLE jobs(id TEXT,project TEXT,state TEXT,created REAL,payload TEXT,result TEXT); CREATE TABLE events(id INTEGER PRIMARY KEY,job TEXT,time REAL,type TEXT,data TEXT);"
    )
    now = 100000
    for name, status, created, ended in [
        ("old", "completed", now - 100, now - 1801),
        ("boundary", "completed", now - 90000, now - 1800),
        ("running", "running", 1, None),
        ("failed", "failed", now - 2000, now - 1900),
        ("recent", "cancelled", now - 200, now - 100),
    ]:
        db.execute(
            "INSERT INTO jobs VALUES(?,?,?,?,?,?)",
            (
                name,
                "test",
                status,
                created,
                json.dumps({"prompt": "<script>test</script>"}),
                json.dumps({"answer": "ok"}),
            ),
        )
        if ended is not None:
            db.execute(
                "INSERT INTO events(job,time,type,data) VALUES(?,?,?,?)",
                (name, ended, status, "{}"),
            )
    db.commit()
    data = snapshot(tmp_path, now)
    assert {r["id"] for r in data["recent"]} == {"boundary", "running", "recent"}
    assert data["active"] == 1
    assert db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 5
    assert execution(tmp_path, "old")["answer"] == "ok"
    assert execution(tmp_path, "' OR 1=1 --") is None
    assert {r["id"] for r in snapshot(tmp_path, now + 1)["recent"]} == {"running", "recent"}
    db.close()


def test_empty_dashboard(tmp_path):
    data = snapshot(tmp_path, 100)
    assert data["available"] and data["recent"] == [] and data["requests_per_second"] == 0


def test_legacy_session_totals_are_not_execution_throughput():
    from control.dashboard import metric

    assert metric(
        {
            "context_usage": {"total": {}},
            "metrics": {"input_tokens": 100, "output_tokens": 50, "inference_seconds": 1},
        }
    ) == (None, None, None)
    assert metric(
        {
            "context_usage": {"total": {}},
            "metrics": {
                "usage_scope": "turn",
                "input_tokens": 100,
                "output_tokens": 50,
                "inference_seconds": 2,
            },
        }
    ) == (100, 50, 25)


def test_corrupt_execution_is_a_controlled_unavailable_state(tmp_path):
    root = tmp_path / "runs"
    root.mkdir()
    path = root / "jobs.sqlite3"
    path.write_bytes(b"not a database")
    assert execution(tmp_path, "job")["available"] is False
    path.unlink()
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE jobs(id TEXT,payload TEXT,result TEXT)")
        db.execute("INSERT INTO jobs VALUES('job','[]','null')")
    assert execution(tmp_path, "job")["available"] is False


def test_execution_spans_use_complete_read_only_history_without_content(tmp_path):
    root = tmp_path / "runs"
    root.mkdir()
    path = root / "jobs.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript(
            "CREATE TABLE jobs(id TEXT,project TEXT,state TEXT,created REAL,payload TEXT,result TEXT); CREATE TABLE events(id INTEGER PRIMARY KEY,job TEXT,time REAL,type TEXT,data TEXT);"
        )
        db.execute(
            "INSERT INTO jobs VALUES(?,?,?,?,?,?)",
            (
                "job",
                "p",
                "completed",
                0,
                json.dumps({"backend": "codex", "prompt": "private prompt"}),
                json.dumps({"answer": "private answer"}),
            ),
        )
        records = [
            (0, "queued", {}),
            (1, "running", {}),
            (
                2,
                "tool_start",
                {"tool": "Read", "tool_call_id": "call", "input": {"secret": "private input"}},
            ),
        ]
        records += [(index + 3, "answer_delta", {"text": "private text"}) for index in range(501)]
        records += [
            (505, "tool_end", {"tool": "Read", "tool_call_id": "call", "status": "completed"}),
            (506, "completed", {}),
        ]
        db.executemany(
            "INSERT INTO events(job,time,type,data) VALUES(?,?,?,?)",
            [("job", at, kind, json.dumps(data)) for at, kind, data in records],
        )
    before = path.read_bytes()
    data = execution(tmp_path, "job")
    assert data["prompt"] == "private prompt" and data["answer"] == "private answer"
    assert len(data["events"]) == data["event_limit"] == 500
    spans = data["spans"]
    tool = next(span for span in spans if span["kind"] == "execute_tool")
    assert (tool["start_ts"], tool["end_ts"], tool["status"]) == (2, 505, "ok")
    assert all("content" not in span for span in spans)
    assert "private" not in json.dumps(spans)
    assert path.read_bytes() == before
