"""Conversation continuity across provider/model/effort changes (no paid inference)."""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from test_workspaces import config

from adapters.local.sandbox import ISOLATION_VERSION
from agent_service.app import Service


@pytest.fixture
def service(tmp_path):
    cfg = config(tmp_path)
    cfg["services"]["deepseek"] = {**cfg["services"]["local"], "models": ["deepseek-flash"]}
    cfg["services"]["gemini"] = {**cfg["services"]["codex"], "models": ["gemini-model"]}
    for backend in ("codex", "local", "deepseek", "gemini"):
        cfg["services"][backend]["mode"] = "native"
        cfg[backend] = {}
    result = Service(cfg)
    result.quota = AsyncMock(return_value={"available": False})
    yield result
    result.db.close()


def add_turn(service, ident, backend, parent=None, files=(), model=None, effort="low"):
    data = {
        "project_id": "p",
        "backend": backend,
        "execution_mode": "scoped" if backend == "local" else "native",
        "model": model or backend + "-model",
        "effort": effort,
        "prompt": "request-" + ident,
        "file_ids": list(files),
    }
    if parent:
        data["parent_job_id"] = parent
    service.db.execute(
        "INSERT INTO jobs(id,project,owner,state,created,payload) VALUES(?,?,?,?,?,?)",
        (
            ident,
            "p",
            "a",
            "running",
            len(service.db.execute("SELECT id FROM jobs").fetchall()),
            json.dumps(data),
        ),
    )
    service.db.commit()
    return dict(service.db.execute("SELECT * FROM jobs WHERE id=?", (ident,)).fetchone()), data


def execute(service, ident, backend, parent=None, files=(), model=None, effort="low"):
    row, data = add_turn(service, ident, backend, parent, files, model, effort)
    captured = {}

    async def run(
        config, prompt, event, project, selected_model, selected_effort, session, provider, approve
    ):
        captured.update(
            prompt=prompt, project=project, model=selected_model, effort=selected_effort
        )
        marker = session / "native-thread.json"
        session.mkdir(parents=True, exist_ok=True)
        captured["resumed"] = marker.exists()
        thread_id = json.loads(marker.read_text())["id"] if marker.exists() else "thread-" + ident
        marker.write_text(json.dumps({
            "id": thread_id,
            "isolation": ISOLATION_VERSION,
            **({"adapter": "deepseek"} if provider == "deepseek" else {}),
        }))
        return {
            "answer": "answer-" + ident,
            "thread_id": thread_id,
            "backend": provider,
            "model": selected_model,
        }

    with patch("adapters.run_native", side_effect=run):
        result = asyncio.run(service.infer(row, data))
    service.finish(ident, "completed", result)
    return captured


def attachment(service, fid):
    service.db.execute(
        "INSERT INTO files(id,project,owner,name,size,hash,pages) VALUES(?,?,?,?,?,?,?)",
        (fid, "p", "a", fid + ".txt", 1, "fixture", json.dumps([{"text": "source-" + fid}])),
    )
    service.db.commit()


def test_roundtrip_replays_missing_turns_and_attachments_after_restart(service):
    attachment(service, "first")
    attachment(service, "during-gemini")
    execute(service, "a", "deepseek", files=["first"])
    local = execute(service, "b", "gemini", "a", files=["during-gemini"])
    assert "request-a" in local["prompt"] and "source-first" in local["prompt"]
    astra = execute(service, "c", "codex", "b", model="gpt-6-astra")
    assert all(
        text in astra["prompt"] for text in ("request-a", "answer-b", "source-during-gemini")
    )
    restarted = Service(service.config)
    try:
        resumed = execute(restarted, "d", "deepseek", "c")
    finally:
        restarted.db.close()
    assert resumed["resumed"]
    assert "request-a" not in resumed["prompt"], "already synchronized turn must not be duplicated"
    assert all(
        text in resumed["prompt"] for text in ("answer-b", "answer-c", "source-during-gemini")
    )
    assert "source-first" not in resumed["prompt"]


def test_model_and_effort_change_keep_native_session(service):
    execute(service, "a", "codex", model="gpt-6-astra", effort="low")
    changed = execute(service, "b", "codex", "a", model="gpt-5.6-terra", effort="high")
    assert changed["resumed"]
    assert changed["model"] == "gpt-5.6-terra" and changed["effort"] == "high"
    assert "request-a" not in changed["prompt"]


def test_partial_turn_transfers_tools_and_partial_answer_without_reasoning(service):
    execute(service, "a", "deepseek")
    row, _ = add_turn(service, "b", "gemini", "a")
    service.event("b", "answer_delta", {"text": "Partial verified result"})
    service.event("b", "reasoning_delta", {"text": "PRIVATE_REASONING"})
    service.event("b", "tool_start", {"tool": "exec_command", "tool_id": "t1"})
    service.event(
        "b", "tool_end", {"tool": "exec_command", "tool_id": "t1", "result": "7 tests passed"}
    )
    service.event("b", "tool_start", {"tool": "apply_patch", "tool_id": "pending"})
    service.finish("b", "cancelled", {"partial_output": "persisted_events"})
    final = execute(service, "c", "codex", "b")
    for text in (
        "Partial verified result",
        "7 tests passed",
        "cancelled",
        "pending",
        "deepseek",
        "gemini",
    ):
        assert text in final["prompt"]
    assert "PRIVATE_REASONING" not in final["prompt"]


@pytest.mark.parametrize("damage", ["missing", "replaced", "legacy", "isolation"])
def test_untrusted_session_replays_full_context(service, damage):
    execute(service, "a", "local")
    session = service.root / "sessions/a/local"
    marker = session / "native-thread.json"
    if damage == "missing":
        marker.unlink()
    elif damage == "replaced":
        marker.write_text(json.dumps({"id": "unrelated", "isolation": ISOLATION_VERSION}))
    elif damage == "legacy":
        (session / "harness-context.json").unlink(missing_ok=True)
    else:
        marker.write_text(json.dumps({"id": "thread-a", "isolation": "old"}))
    result = execute(service, "b", "local", "a")
    assert not result["resumed"]
    assert "request-a" in result["prompt"] and "answer-a" in result["prompt"]


def test_failed_native_turn_is_not_resumed_as_a_completed_checkpoint(service):
    execute(service, "a", "deepseek")
    row, _ = add_turn(service, "b", "deepseek", "a")
    service.event("b", "tool_end", {"tool": "write_file", "result": "file already written"})
    service.finish("b", "failed", {"error": "provider_disconnected"})
    final = execute(service, "c", "deepseek", "b")
    assert not final["resumed"]
    assert all(
        text in final["prompt"]
        for text in ("request-a", "provider_disconnected", "file already written")
    )


def test_images_added_by_another_provider_reach_resumed_session(service):
    execute(service, "a", "deepseek")
    attachment(service, "image")
    with service.db:
        service.db.execute(
            "UPDATE files SET pages=? WHERE id=?",
            (json.dumps([{"media_type": "image/png"}]), "image"),
        )
    service.validate_images = AsyncMock()
    execute(service, "b", "gemini", "a", files=["image"])
    final = execute(service, "c", "deepseek", "b")
    assert final["resumed"]
    assert final["project"]["_images"] == [
        {"media_type": "image/png", "path": str(service.root / "files/p/image/source")}
    ]


def test_context_limit_fails_explicitly_without_truncating_history(service):
    from agent_service.app import APIError

    execute(service, "a", "gemini")
    with service.db:
        service.db.execute(
            "UPDATE jobs SET result=? WHERE id=?", (json.dumps({"answer": "x" * 150001}), "a")
        )
    row, data = add_turn(service, "b", "deepseek", "a")
    with (
        patch("adapters.run_native", new_callable=AsyncMock) as run,
        pytest.raises(APIError, match="conversation_context_limit"),
    ):
        asyncio.run(service.infer(row, data))
    run.assert_not_called()


def test_mode_change_rebuilds_history_instead_of_using_other_transport(service):
    execute(service, "a", "codex")
    cursor = service.root / "sessions/a/codex/harness-context.json"
    saved = json.loads(cursor.read_text())
    saved["mode"] = "scoped"
    cursor.write_text(json.dumps(saved))
    result = execute(service, "b", "codex", "a")
    assert not result["resumed"] and "answer-a" in result["prompt"]


@pytest.mark.parametrize("backend", ["codex", "claude", "gemini", "deepseek", "local"])
def test_title_is_provider_independent_and_survives_handoff(service, backend):
    service.config["services"][backend] = {**service.config["services"]["codex"], "mode": "native"}
    service.config.setdefault(backend, {})
    first = execute(service, "root-title", "local" if backend == "local" else "codex")
    assert first["project"]["_conversation_title"] == "request-root-title"
    with service.db:
        service.db.execute(
            "INSERT INTO conversation_titles VALUES(?,?)", ("root-title", "Same title everywhere")
        )
    next_turn = execute(service, "child-title", backend, "root-title")
    assert next_turn["project"]["_conversation_title"] == "Same title everywhere"


@pytest.mark.parametrize("state", ["failed", "cancelled", "interrupted"])
def test_confirmed_codex_turn_resumes_after_interruption(service, state):
    execute(service, "a", "codex")
    row, data = add_turn(service, "b", "codex", "a")

    async def interrupted(*args):
        args[2]("session_turn_started", {"thread_id": "thread-a"})
        args[2]("tool_end", {"result": "large output " * 15000})
        raise TimeoutError()

    with patch("adapters.run_native", side_effect=interrupted), pytest.raises(TimeoutError):
        asyncio.run(service.infer(row, data))
    service.finish("b", state, {"error": "TimeoutError"})
    resumed = execute(service, "c", "codex", "b")
    assert resumed["resumed"]
    assert "large output" not in resumed["prompt"]
    assert "interrupted" in resumed["prompt"]


def test_legacy_oversized_codex_history_is_readable_without_inline_replay(service):
    from pathlib import Path

    service.config["services"]["codex"]["permissions"]["shell"] = True
    execute(service, "a", "gemini")
    service.event("a", "tool_end", {"result": "large evidence " * 15000})
    final = execute(service, "b", "codex", "a")
    assert len(final["prompt"]) < 20000
    roots = final["project"]["additional_roots"]
    history = Path(roots[-1]) / "history-b.jsonl"
    saved = [json.loads(line) for line in history.read_text().splitlines()]
    assert saved[0]["user"] == "request-a"
    assert saved[0]["evidence"][0]["data"]["result"] == "large evidence " * 15000
    assert str(history) in final["prompt"]
    assert history.stat().st_mode & 0o777 == 0o600
    assert "request-b" in final["prompt"]


def test_oversized_codex_history_without_tools_still_fails_explicitly(service):
    from agent_service.app import APIError

    execute(service, "a", "gemini")
    service.event("a", "tool_end", {"result": "x" * 160000})
    row, data = add_turn(service, "b", "codex", "a")
    with pytest.raises(APIError, match="conversation_context_limit"):
        asyncio.run(service.infer(row, data))


def test_prompt_without_sources_has_no_empty_sources_trailer(service):
    """A bare ``SOURCES:\\n[]`` trailer was echoed by real models ("PONG\\nSOURCES:\\n[]")."""
    plain = execute(service, "plain", "codex")
    assert "SOURCES" not in plain["prompt"]
    assert plain["prompt"].endswith("request-plain")
    attachment(service, "doc")
    cited = execute(service, "cited", "codex", files=("doc",))
    assert "\nSOURCES:\n" in cited["prompt"] and "source-doc" in cited["prompt"]
