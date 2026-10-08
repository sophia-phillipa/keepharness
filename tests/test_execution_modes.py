"""Conversation execution-mode admission and dispatch contracts."""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from starlette.testclient import TestClient
from test_workspaces import config

from agent_service.app import APIError, Service, create_app


def service(tmp_path):
    cfg = config(tmp_path)
    cfg["services"]["codex"]["mode"] = "scoped"  # legacy fallback only
    cfg["services"]["local"]["mode"] = "native"  # local remains intrinsically isolated
    cfg["local"] = {"binary": "fixture"}
    result = Service(cfg)
    return result, ("a", cfg["clients"]["a"])


def payload(instance, identity, job_id):
    return json.loads(instance.job(identity, job_id)["payload"])


def test_new_conversation_defaults_to_native_but_local_is_explicitly_scoped(tmp_path):
    instance, identity = service(tmp_path)
    try:
        codex = instance.submit(
            identity,
            {"project_id": "p", "backend": "codex", "model": "gpt-6-astra", "prompt": "native"},
        )
        local = instance.submit(
            identity,
            {
                "project_id": "p",
                "backend": "local",
                "model": "installed-model",
                "prompt": "isolated",
            },
        )
        assert payload(instance, identity, codex["job_id"])["execution_mode"] == "native"
        assert payload(instance, identity, local["job_id"])["execution_mode"] == "scoped"
    finally:
        instance.db.close()


def test_unknown_and_internal_execution_mode_inputs_are_rejected_cleanly(tmp_path):
    instance, identity = service(tmp_path)
    try:
        with pytest.raises(APIError, match="backend_unavailable"):
            instance.submit(
                identity,
                {"project_id": "p", "backend": "unknown", "model": "fixture", "prompt": "bad"},
            )
        with pytest.raises(APIError, match="invalid_internal_field"):
            instance.submit(
                identity,
                {
                    "project_id": "p",
                    "backend": "codex",
                    "model": "gpt-6-astra",
                    "prompt": "bad",
                    "_maestro_stage": "spoof",
                },
            )
    finally:
        instance.db.close()


def test_model_catalog_and_conversation_responses_report_effective_mode(tmp_path):
    instance, identity = service(tmp_path)
    try:
        modes = {model["backend"]: model["execution_modes"] for model in instance.models("p")}
        assert modes["codex"] == ["native"]
        assert modes["local"] == ["scoped"]
        job = instance.submit(
            identity,
            {
                "project_id": "p",
                "backend": "local",
                "model": "installed-model",
                "prompt": "isolated",
            },
        )["job_id"]
        row = instance.job(identity, job)
        assert instance.execution(row)["execution_mode"] == "scoped"
    finally:
        instance.db.close()


def test_local_scoped_mode_uses_its_isolated_native_adapter(tmp_path):
    instance, identity = service(tmp_path)
    try:
        job = instance.submit(
            identity,
            {
                "project_id": "p",
                "backend": "local",
                "model": "installed-model",
                "prompt": "isolated",
            },
        )["job_id"]
        row = instance.job(identity, job)
        data = payload(instance, identity, job)
        with (
            patch("adapters.run_native", AsyncMock(return_value={"answer": "ok"})) as native,
            patch("adapters.run_scoped", AsyncMock()) as scoped,
        ):
            asyncio.run(instance.infer(row, data))
        native.assert_awaited_once()
        scoped.assert_not_awaited()
    finally:
        instance.db.close()


def test_job_and_conversation_api_report_the_locked_mode(tmp_path):
    cfg = config(tmp_path)
    app = create_app(cfg)
    with TestClient(app, headers={"Authorization": "Bearer a"}) as client:
        created = client.post(
            "/v1/jobs",
            json={
                "project_id": "p",
                "backend": "codex",
                "model": "gpt-6-astra",
                "prompt": "native",
            },
        )
        assert created.status_code == 202
        job = created.json()["job_id"]
        assert created.json()["execution_mode"] == "native"
        assert client.get("/v1/jobs/" + job).json()["request"]["execution_mode"] == "native"
        conversation = client.get("/v1/conversations/" + job).json()
        assert conversation["execution_mode"] == "native"
    app.state.service.db.close()


def test_isolated_cloud_conversation_without_bubblewrap_is_refused_up_front(tmp_path):
    """F-23: the harness checks bubblewrap when a conversation is admitted, not mid-run."""
    instance, identity = service(tmp_path)
    request = {"project_id": "p", "backend": "codex", "model": "gpt-6-astra", "prompt": "x"}
    try:
        with patch("agent_service.services.conversation_service.shutil.which", return_value=None):
            with pytest.raises(APIError, match="execution_mode_unsupported") as refused:
                instance.submit(identity, {**request, "execution_mode": "scoped"})
            assert refused.value.status == 422
            instance.submit(identity, request)
            # Local models keep their own sandbox contract and run-time error.
            instance.submit(identity, {**request, "backend": "local", "model": "installed-model"})
    finally:
        instance.db.close()


def test_a_service_without_a_mode_defaults_to_native(tmp_path):
    """F-24: providers are native-only at the service level; no fallback says scoped."""
    from agent_service import maestro

    cfg = config(tmp_path)
    assert "mode" not in cfg["services"]["codex"]
    instance = Service(cfg)
    try:
        assert instance.capabilities()["backends"]["codex"]["mode"] == "native"
        asyncio.run(instance.validate_images("codex", "gpt-6-astra"))
        codex = [m for m in maestro.candidates(cfg, "p") if m["backend"] == "codex"]
        assert codex and all(m["mode"] == "native" for m in codex)
    finally:
        instance.db.close()


@pytest.mark.parametrize(
    "backend,model,mode",
    [("codex", "gpt-6-astra", "native"), ("local", "installed-model", "scoped")],
)
def test_continuation_inherits_root_mode_and_cannot_select_again(tmp_path, backend, model, mode):
    instance, identity = service(tmp_path)
    try:
        request = dict(project_id="p", backend=backend, model=model, prompt="first")
        first = instance.submit(identity, {**request, "execution_mode": mode})["job_id"]
        instance.finish(first, "completed", {"answer": "done"})
        child = instance.submit(identity, {**request, "parent_job_id": first})["job_id"]
        assert payload(instance, identity, child)["execution_mode"] == mode
        with pytest.raises(APIError, match="conversation_execution_mode_locked") as exc:
            instance.submit(identity, {**request, "parent_job_id": child, "execution_mode": mode})
        assert exc.value.status == 409
    finally:
        instance.db.close()
