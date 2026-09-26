"""Twenty attachments work through folder selection and job submission, without inference."""

import pytest
from starlette.testclient import TestClient
from test_project_browser import config

from agent_service import workspaces
from agent_service.app import create_app
from agent_service.tools import ToolError


@pytest.mark.parametrize(
    "select", [workspaces.selected_project_files, workspaces.selected_system_files]
)
def test_folder_selection_twenty(select, tmp_path):
    for i in range(21):
        (tmp_path / f"{i:02}.txt").write_text("text")
    names = [f"{i:02}.txt" for i in range(21)]
    selected, skipped = select(tmp_path, names, 20)
    assert len(selected) == 20
    assert skipped
    with pytest.raises(ToolError):
        select(tmp_path, names, 21)


def test_attach_default_and_submit_twenty(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    for i in range(21):
        (root / f"{i}.txt").write_text("text")
    app = create_app(config(tmp_path))
    # No TestClient lifespan: do not start inference workers.
    client = TestClient(app, headers={"Authorization": "Bearer a"})
    try:
        result = client.post(
            "/v1/project-files/attach?project_id=p",
            json={
                "root_id": "system",
                "paths": [str(root).lstrip("/")],
                "backend": "codex",
                "model": "fixture",
            },
        )
        assert result.status_code == 200, result.text
        ids = [item["file_id"] for item in result.json()["attachments"]]
        assert len(ids) == 20
        payload = {
            "project_id": "p",
            "prompt": "count boundary",
            "backend": "codex",
            "model": "fixture",
            "effort": "low",
            "file_ids": ids,
        }
        # No lifespan means no inference worker is running.
        response = client.post("/v1/jobs", json=payload)
        assert response.status_code == 202, response.text
        response = client.post("/v1/jobs", json={**payload, "file_ids": ids + [ids[0]]})
        assert response.status_code == 422, response.text
        assert response.json()["code"] == "file_limit"
    finally:
        client.close()
        app.state.service.db.close()
