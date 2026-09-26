"""Portable conversation evidence and verified cursors for provider-owned sessions."""

import json

from adapters.local.sandbox import ISOLATION_VERSION

MARKERS = ("native-thread.json", "remote-thread.json", "claude-session.json", "gemini-session.json")
EVIDENCE_EVENTS = ("tool_start", "tool_end", "plan_updated", "changes_applied", "deployment_failed")


def read_json(path):
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def session_markers(session):
    return {
        name: read_json(session / name).get("id") for name in MARKERS if (session / name).exists()
    }


def pending_turns(session, turns, backend, mode):
    """Only omit turns when the matching native session demonstrably contains them."""
    cursor = read_json(session / "harness-context.json")
    markers = session_markers(session)
    previous = next((p for p, _ in reversed(turns) if p.get("backend") == backend), {})
    valid = (
        bool(markers)
        and all(markers.values())
        and cursor.get("markers") == markers
        and cursor.get("mode") == mode
        and cursor.get("job_id") == previous.get("_job_id")
        and (
            previous.get("_state") == "completed"
            or (
                backend == "codex"
                and mode == "native"
                and cursor.get("started") is True
                and previous.get("_state") in ("failed", "cancelled", "interrupted")
            )
        )
    )
    if backend == "local":
        valid = (
            valid
            and read_json(session / "native-thread.json").get("isolation") == ISOLATION_VERSION
        )
    if valid:
        index = next(
            i for i, (payload, _) in enumerate(turns) if payload["_job_id"] == cursor["job_id"]
        )
        return turns[index + 1 :], True
    # Legacy, interrupted or replaced sessions must not silently discard history.
    for name in markers:
        (session / name).replace(session / (name + ".before-context-transfer"))
    return turns, False


def save_cursor(session, job_id, result, mode, *, started=False):
    markers = session_markers(session)
    if not result.get("thread_id") or result.get("error") or result.get("incomplete"):
        return
    if not markers or any(value != result["thread_id"] for value in markers.values()):
        return
    target = session / "harness-context.json"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(
        json.dumps({"job_id": job_id, "markers": markers, "mode": mode, "started": started})
    )
    temporary.chmod(0o600)
    temporary.replace(target)


def portable_history(db, turns):
    history = []
    for payload, result in turns:
        if payload.get("_overflow_job_id"):
            continue
        record = {
            "job_id": payload["_job_id"],
            "backend": payload.get("backend"),
            "model": payload.get("model"),
            "effort": payload.get("effort"),
            "state": payload["_state"],
            "user": payload.get("prompt", ""),
            "assistant": result.get("answer", ""),
            "error": result.get("error"),
        }
        evidence, partial = [], []
        for event in db.execute(
            "SELECT type,data FROM events WHERE job=? AND type IN (?,?,?,?,?,?,?) ORDER BY id",
            (payload["_job_id"], *EVIDENCE_EVENTS, "answer_delta", "interrupted"),
        ):
            data = json.loads(event["data"])
            if event["type"] == "answer_delta":
                if not record["assistant"]:
                    partial.append(data.get("text", ""))
            else:
                evidence.append({"type": event["type"], "data": data})
        if partial:
            record["assistant"] = "".join(partial)
            record["partial"] = True
        if evidence:
            record["evidence"] = evidence
        if result.get("deployment"):
            record["deployment"] = result["deployment"]
        history.append(record)
    return history
