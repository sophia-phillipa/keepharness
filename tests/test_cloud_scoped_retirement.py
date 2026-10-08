"""Retired cloud routes fail before credentials, subprocesses or effects are reached."""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from test_execution_modes import service
from test_legacy_execution_mode import stored

import adapters
from agent_service import maestro
from agent_service.app import APIError
from agent_service.config import EXECUTION_MODES
from agent_service.effect_transport import transport_support
from agent_service.services import queue_worker
from agent_service.tools import ToolError


@pytest.mark.parametrize("backend", ["codex", "claude"])
def test_cloud_admission_and_direct_dispatch_refuse_without_side_effects(
    tmp_path, backend, no_retired_side_effects
):
    instance, identity = service(tmp_path)
    try:
        with pytest.raises(APIError, match="execution_mode_unsupported") as exc:
            instance.submit(
                identity,
                dict(
                    project_id="p",
                    backend=backend,
                    model="gpt-6-astra",
                    prompt="old",
                    execution_mode="scoped",
                ),
            )
        assert exc.value.status == 422
        assert instance.conversation_repository.count_pending() == 0
        with pytest.raises(ToolError, match="execution_mode_unsupported"):
            asyncio.run(adapters.run_scoped({}, "old", lambda *a: None, provider=backend))
        assert not hasattr(adapters.get_adapter(backend), "run_scoped")
    finally:
        instance.db.close()


@pytest.mark.parametrize("backend", ["codex", "claude"])
@pytest.mark.parametrize(
    "action", ["continue", "switch_provider", "switch_preset", "retry", "resume", "branch"]
)
def test_stored_cloud_scoped_cannot_resume_retry_branch_or_switch(
    tmp_path, backend, action, no_retired_side_effects
):
    instance, identity = service(tmp_path)
    try:
        row = stored(instance, backend, "scoped", state="failed")
        session = instance.root / "sessions" / "legacy" / backend
        session.mkdir(parents=True)
        marker = session / "remote-thread.json"
        marker.write_text("old session")
        attachment = instance.root / "old-attachment.txt"
        attachment.write_bytes(b"old attachment")
        before = tuple(row)
        with pytest.raises(APIError, match="execution_mode_unsupported"):
            if action == "retry":
                asyncio.run(instance.retry_turn(identity, "legacy"))
            elif action in ("resume", "branch"):
                instance.recover_workflow(identity, "legacy", {}, rerun=action == "branch")
            else:
                data = dict(
                    project_id="p",
                    backend="local" if action == "switch_provider" else backend,
                    model="installed-model" if action == "switch_provider" else "gpt-6-astra",
                    prompt="continue",
                    parent_job_id="legacy",
                )
                if action == "switch_preset":
                    data["access_mode"] = "full"
                instance.submit(identity, data)
        assert tuple(instance.job(identity, "legacy")) == before
        assert (
            json.loads(instance.conversation(identity, "legacy")[0]["result"])["answer"]
            == "history"
        )
        assert marker.read_text() == "old session"
        assert attachment.read_bytes() == b"old attachment"
    finally:
        instance.db.close()


@pytest.mark.parametrize("backend", ["codex", "claude"])
@pytest.mark.parametrize("mode", ["scoped", None])
def test_preupgrade_queued_cloud_jobs_fail_before_provider_checks(
    tmp_path, backend, mode, no_retired_side_effects, monkeypatch
):
    instance, identity = service(tmp_path)
    try:
        row = stored(instance, backend, mode, state="queued")
        check = AsyncMock(side_effect=AssertionError("provider check reached"))
        monkeypatch.setattr(queue_worker, "provider_state_run_check", check)
        asyncio.run(queue_worker.run_job(instance, row))
        final = instance.job(identity, row["id"])
        assert final["state"] == "failed"
        assert json.loads(final["result"])["error"] == "execution_mode_unsupported"
        check.assert_not_awaited()
    finally:
        instance.db.close()


@pytest.mark.parametrize("entry", ["infer", "workflow"])
def test_workflow_and_internal_inference_cannot_dispatch_retired_cloud(
    tmp_path, entry, no_retired_side_effects
):
    instance, identity = service(tmp_path)
    try:
        row = stored(instance, "codex", "scoped", state="queued")
        data = json.loads(row["payload"])
        with pytest.raises(APIError, match="execution_mode_unsupported"):
            if entry == "infer":
                asyncio.run(instance.infer(row, {**data, "_maestro_stage": "1"}))
            else:
                asyncio.run(maestro.execute_plan(instance, row, data, {"steps": []}))
    finally:
        instance.db.close()


def test_authoritative_capabilities_and_effects_retire_cloud_scoped(tmp_path):
    assert dict(EXECUTION_MODES) == {
        "codex": ("native",),
        "claude": ("native",),
        "gemini": ("native",),
        "deepseek": ("native",),
        "local": ("scoped",),
    }
    assert not {"codex", "claude"} & set(adapters.SCOPED_PROVIDERS)
    for backend in ("codex", "claude"):
        assert not transport_support(backend, "scoped")["supported"]
        assert transport_support(backend, "native")["supported"]
    instance, _ = service(tmp_path)
    try:
        assert all(
            item["backend"] == "local"
            for item in maestro.candidates(instance.config, "p", execution_mode="scoped")
        )
        assert all(
            item["mode"] == ("scoped" if item["backend"] == "local" else "native")
            for item in maestro.candidates(instance.config, "p")
        )
    finally:
        instance.db.close()


@pytest.mark.parametrize("backend", ["codex", "claude"])
def test_http_cloud_scoped_is_422_without_enqueue(tmp_path, backend, no_retired_side_effects):
    import httpx
    from test_workspaces import config

    from agent_service.app import create_app

    app = create_app(config(tmp_path))

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://localhost",
            headers={"Authorization": "Bearer a"},
        ) as client:
            response = await client.post(
                "/v1/jobs",
                json=dict(
                    project_id="p",
                    backend=backend,
                    model="gpt-6-astra",
                    prompt="retired",
                    execution_mode="scoped",
                ),
            )
            assert response.status_code == 422
            assert response.json()["code"] == "execution_mode_unsupported"

    try:
        asyncio.run(scenario())
        assert app.state.service.conversation_repository.count_pending() == 0
    finally:
        app.state.service.db.close()


def test_new_explicit_native_conversation_runs_without_old_session(tmp_path):
    instance, identity = service(tmp_path)
    try:
        instance.config["codex"] = {"binary": "fixture"}
        stored(instance, "codex", "scoped")
        job = instance.submit(
            identity,
            dict(
                project_id="p",
                backend="codex",
                model="gpt-6-astra",
                prompt="new",
                execution_mode="native",
            ),
        )["job_id"]
        row = instance.job(identity, job)
        native = AsyncMock(return_value={"answer": "new answer"})
        with (
            patch("adapters.run_native", native),
            patch.object(instance, "quota", AsyncMock(return_value=None)),
        ):
            result = asyncio.run(instance.infer(row, json.loads(row["payload"])))
        assert result["answer"] == "new answer"
        native.assert_awaited_once()
        assert "history" not in native.call_args.args[1]
        assert "legacy" not in str(native.call_args.args[6])
    finally:
        instance.db.close()
