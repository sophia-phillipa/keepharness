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
