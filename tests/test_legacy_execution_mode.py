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


@pytest.mark.parametrize("count", [100, 998])
@pytest.mark.parametrize("cloud_ancestor", [None, "codex", "claude"])
def test_long_local_history_mode_validation_uses_linear_queries(
    tmp_path, count, cloud_ancestor, record_property
):
    instance, identity = service(tmp_path)
    try:
        with instance.db:
            instance.db.executemany(
                "INSERT INTO jobs(id,project,owner,state,created,payload,result,idem,digest) VALUES(?,?,?,?,?,?,?,?,?)",
                [
                    (
                        f"turn-{i}",
                        "p",
                        "a",
                        "completed",
                        i,
                        json.dumps(
                            dict(
                                backend="local",
                                execution_mode="scoped",
                                **({"parent_job_id": f"turn-{i - 1}"} if i else {}),
                            )
                        ),
                        "{}",
                        None,
                        f"turn-{i}",
                    )
                    for i in range(count)
                ],
            )
        if cloud_ancestor:
            with instance.db:
                instance.db.execute(
                    "UPDATE jobs SET payload=json_set(payload, '$.backend', ?) WHERE id=?",
                    (cloud_ancestor, f"turn-{count // 2}"),
                )
        row = instance.job(identity, f"turn-{count - 1}")
        queries = []
        instance.db.set_trace_callback(queries.append)
        if cloud_ancestor:
            with pytest.raises(APIError, match="execution_mode_unsupported"):
                instance.dispatch_execution_mode(row, {"backend": "local"})
        else:
            assert (
                instance.dispatch_execution_mode(row, {"backend": "local"})["execution_mode"]
                == "scoped"
            )
        instance.db.set_trace_callback(None)
        record_property("query_count", len(queries))
        assert len(queries) <= 4 * count + 10, len(queries)
    finally:
        instance.db.close()


@pytest.mark.parametrize("invalid", ["missing", "cycle", "other-owner", "other-project"])
def test_history_mode_scan_rejects_invalid_unrelated_ancestry(tmp_path, invalid):
    instance, identity = service(tmp_path)
    try:
        row = stored(instance, "local", "scoped", job="valid")
        stored(instance, "local", job="broken", parent="ancestor")
        if invalid != "missing":
            stored(
                instance, "local", job="ancestor", parent="broken" if invalid == "cycle" else None
            )
        with instance.db:
            if invalid == "other-owner":
                instance.db.execute("UPDATE jobs SET owner='other' WHERE id='ancestor'")
            if invalid == "other-project":
                instance.db.execute("UPDATE jobs SET project='other' WHERE id='ancestor'")
        with pytest.raises(APIError, match="invalid_parent_job"):
            instance.dispatch_execution_mode(row, {"backend": "local"})
    finally:
        instance.db.close()
