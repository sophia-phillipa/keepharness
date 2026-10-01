import json

import pytest
from starlette.testclient import TestClient
from test_invocation_normalization import invocation_service

from agent_service.app import create_app


@pytest.mark.parametrize("configured_mode", ["native", "scoped"])
def test_maestro_resources_use_eligible_coordinator(tmp_path, monkeypatch, configured_mode):
    service, identity = invocation_service(tmp_path, monkeypatch)
    service.config["services"]["codex"]["mode"] = configured_mode
    app = create_app(service.config)
    try:
        with TestClient(app, headers={"Authorization": "Bearer a"}) as client:
            response = client.get(
                "/v1/resources?project_id=p&backend=maestro&model=auto&execution_mode=native"
            )
        assert response.status_code == 200
        assert any(
            item["name"] == "reviewer" and item["selectable"] for item in response.json()["items"]
        )
        item = next(item for item in response.json()["items"] if item["name"] == "reviewer")
        submitted = service.submit(
            identity,
            dict(
                project_id="p",
                backend="maestro",
                model="auto",
                effort="auto",
                prompt="/reviewer inspect",
                resource_selections=[
                    {"id": item["id"], "revision": item["revision"], "token": "/reviewer"}
                ],
            ),
        )
        payload = json.loads(service.job(identity, submitted["job_id"])["payload"])
        assert payload["execution_mode"] == "native"
        assert payload["backend"] == "codex"
        assert payload["invocations"][0]["resource_id"] == item["resource_id"]
    finally:
        service.db.close()


def test_activity_titles_follow_conversation_names(tmp_path, monkeypatch):
    service, identity = invocation_service(tmp_path, monkeypatch)
    try:
        jobs = [
            service.submit(
                identity, dict(project_id="p", backend="codex", model="gpt-6-astra", prompt=title)
            )["job_id"]
            for title in ("Review report", "Write summary")
        ]
        with service.db:
            service.conversation_repository.set_title(jobs[1], "Renamed summary")
        activity = {item["job_id"]: item for item in service.activity(identity)["jobs"]}
        assert activity[jobs[0]]["title"] == "Review report"
        assert activity[jobs[1]]["title"] == "Renamed summary"
    finally:
        service.db.close()


def test_local_maestro_discovers_and_executes_workflow_in_scoped_mode(tmp_path, monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock, patch

    from agent_service.errors import APIError

    service, identity = invocation_service(tmp_path, monkeypatch)
    service.config["services"]["local"] = {
        "enabled": True,
        "models": ["installed-model"],
        "projects": ["p"],
        "permissions": {"read": True},
    }
    service.config["maestro_coordinator"] = {"backend": "local", "model": "installed-model"}
    folder = tmp_path / "project/workflows"
    folder.mkdir()
    (folder / "local-review.json").write_text(
        json.dumps(
            {
                "id": "local-review",
                "steps": [
                    {
                        "role": "Reviewer",
                        "task": "Review synthetic facts",
                        "reason": "Check facts",
                        "backend": "local",
                        "model": "installed-model",
                        "effort": "configured",
                    }
                ],
            }
        )
    )
    try:
        with pytest.raises(APIError, match="execution_mode_unsupported"):
            service.resource_catalog(identity, "p", "maestro", "auto", "invalid")
        catalog = service.resource_catalog(identity, "p", "maestro", "auto", "native")
        item = next(item for item in catalog["items"] if item["name"] == "local-review")
        assert item["selectable"]
        request = dict(
            project_id="p",
            backend="maestro",
            model="auto",
            effort="auto",
            prompt="/local-review",
            resource_selections=[
                {"id": item["id"], "revision": item["revision"], "token": "/local-review"}
            ],
        )
        job = service.submit(identity, request)["job_id"]
        row = service.job(identity, job)
        assert json.loads(row["payload"])["execution_mode"] == "scoped"
        with patch.object(
            service, "infer", AsyncMock(return_value={"answer": "Local synthetic answer"})
        ):
            assert asyncio.run(service.execute(row))["answer"] == "Local synthetic answer"
        root = service.submit(
            identity, dict(project_id="p", backend="maestro", model="auto", prompt="Plan a task")
        )["job_id"]
        with pytest.raises(APIError, match="conversation_execution_mode_locked"):
            service.submit(identity, {**request, "parent_job_id": root})
    finally:
        service.db.close()
