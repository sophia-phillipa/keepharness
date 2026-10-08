"""D-044: historical evidence, never mutable settings, establishes legacy modes."""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from test_execution_modes import service

from agent_service.app import APIError

pytestmark = pytest.mark.usefixtures("no_retired_side_effects")


def stored(instance, backend="codex", mode=None, *, job="legacy", parent=None, state="completed"):
    data = dict(project_id="p", backend=backend, model="gpt-6-astra", prompt="old")
    if mode is not None:
        data["execution_mode"] = mode
    if parent:
        data["parent_job_id"] = parent
    with instance.db:
        instance.db.execute(
            "INSERT INTO jobs(id,project,owner,state,created,payload,result,idem,digest) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                job,
                "p",
                "a",
                state,
                len(list(instance.db.execute("SELECT id FROM jobs"))) + 1,
                json.dumps(data),
                json.dumps({"answer": "history", "staged_files": {"old.txt": "proposal"}}),
                None,
                job,
            ),
        )
    return instance.job(("a", instance.config["clients"]["a"]), job)


@pytest.mark.parametrize(
    "backend,expected", [("local", "scoped"), ("gemini", "native"), ("deepseek", "native")]
)
def test_legacy_single_mode_provider_continues_without_rewriting_root(tmp_path, backend, expected):
    instance, identity = service(tmp_path)
    try:
        row = stored(instance, backend)
        before = row["payload"]
        assert instance.conversation_execution_mode(row) == expected
        bound = instance.bind_execution_mode(
            identity, dict(backend=backend, parent_job_id="legacy")
        )
        assert bound["execution_mode"] == expected
        if backend == "local":
            child = instance.submit(
                identity,
                dict(
                    project_id="p",
                    backend="local",
                    model="installed-model",
                    prompt="next",
                    parent_job_id="legacy",
                ),
            )["job_id"]
            child_row = instance.job(identity, child)
            with patch(
                "adapters.run_native", AsyncMock(return_value={"answer": "continued"})
            ) as native:
                result = asyncio.run(instance.infer(child_row, json.loads(child_row["payload"])))
            assert result["answer"] == "continued"
            native.assert_awaited_once()
            assert native.call_args.args[7] == "local"
        assert instance.job(identity, "legacy")["payload"] == before
    finally:
        instance.db.close()


@pytest.mark.parametrize("backend", ["codex", "claude", "maestro", None])
@pytest.mark.parametrize("setting", [None, "native", "scoped"])
def test_legacy_missing_evidence_is_unavailable_regardless_of_service_mode(
    tmp_path, backend, setting
):
    instance, identity = service(tmp_path)
    try:
        instance.config["services"].setdefault(backend, {})
        if setting is None:
            instance.config["services"][backend].pop("mode", None)
        else:
            instance.config["services"][backend]["mode"] = setting
        row = stored(instance, backend)
        assert instance.conversation_execution_mode(row) is None
        assert instance.execution(row)["execution_mode"] is None
        with pytest.raises(APIError, match="execution_mode_unsupported"):
            instance.bind_execution_mode(identity, dict(backend="local", parent_job_id="legacy"))
    finally:
        instance.db.close()


@pytest.mark.parametrize(
    "turns,expected",
    [
        ([("codex", "native")], "native"),
        ([("claude", "native")], "native"),
        ([("codex", "scoped")], "scoped"),
        ([("codex", "native"), ("codex", "scoped")], None),
        ([("local", "native")], None),
        ([(None, "native")], None),
        ([("gemini", None)], None),
    ],
)
def test_legacy_turn_evidence_must_be_unambiguous_and_consistent(tmp_path, turns, expected):
    instance, identity = service(tmp_path)
    try:
        root = stored(instance)
        parent = root["id"]
        for i, (backend, mode) in enumerate(turns):
            row = stored(instance, backend, mode, job=f"turn{i}", parent=parent)
            parent = row["id"]
        assert instance.conversation_execution_mode(root) == expected
        if expected == "native":
            assert (
                instance.bind_execution_mode(identity, dict(backend="codex", parent_job_id=parent))[
                    "execution_mode"
                ]
                == "native"
            )
        else:
            with pytest.raises(APIError, match="execution_mode_unsupported"):
                instance.bind_execution_mode(identity, dict(backend="local", parent_job_id=parent))
        assert "execution_mode" not in json.loads(instance.job(identity, "legacy")["payload"])
    finally:
        instance.db.close()


def test_legacy_cloud_origin_survives_changed_backend_and_service_mode(tmp_path):
    instance, identity = service(tmp_path)
    try:
        root = stored(instance)
        child = stored(instance, "gemini", job="handoff", parent="legacy")
        instance.config["services"]["codex"]["mode"] = "native"
        assert instance.conversation_execution_mode(child) is None
        with pytest.raises(APIError, match="execution_mode_unsupported"):
            instance.bind_execution_mode(
                identity, dict(backend="gemini", parent_job_id=child["id"])
            )
        assert instance.conversation_execution_mode(root) is None
    finally:
        instance.db.close()


@pytest.mark.parametrize("backend", [["codex"], {"provider": "codex"}])
def test_legacy_malformed_provider_provenance_is_unavailable(tmp_path, backend):
    instance, identity = service(tmp_path)
    try:
        row = stored(instance, backend)
        assert instance.conversation_execution_mode(row) is None
        with pytest.raises(APIError, match="execution_mode_unsupported"):
            instance.bind_execution_mode(identity, dict(backend="codex", parent_job_id="legacy"))
    finally:
        instance.db.close()
