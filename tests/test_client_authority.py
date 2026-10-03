"""Host-side changes a client asks for: service control and project workflows (HAR-R4-4, HAR-R4-5)."""

import asyncio
import hashlib
from unittest.mock import AsyncMock, patch

import httpx
from test_workspaces import config

from agent_service.app import create_app
from agent_service.approval_sessions import SESSION_COOKIE, consume_enrollment, issue_enrollment


def request(app, method, path, *, headers=None, json=None, local=False):
    async def send():
        transport = httpx.ASGITransport(
            app=app, client=("127.0.0.1" if local else "100.64.0.2", 40000)
        )
        async with httpx.AsyncClient(
            transport=transport, base_url="http://127.0.0.1" if local else "http://harness.test"
        ) as client:
            return await client.request(method, path, headers=headers, json=json)

    return asyncio.run(send())


def test_service_state_changes_need_an_enrolled_session(tmp_path):
    cfg = config(tmp_path)
    cfg["projects"]["p"]["service_units"] = ["demo.service"]
    cfg["services"]["codex"].update(mode="native", permissions={"shell": True})
    app = create_app(cfg)
    bearer = {"Authorization": "Bearer a"}
    start = {"project_id": "p", "action": "start", "unit": "demo.service", "user_requested": True}
    try:
        with (
            patch("agent_service.service_control.shutil.which", return_value="/usr/bin/systemctl"),
            patch(
                "agent_service.service_control.process",
                AsyncMock(return_value=(0, "ActiveState=active\nLoadState=loaded")),
            ) as process,
        ):
            # A key-holding client (or the model behind the MCP bridge) asserting the request.
            denied = request(app, "POST", "/v1/services", headers=bearer, json=start)
            assert denied.status_code == 403
            assert denied.json()["code"] == "approval_session_required"
            process.assert_not_awaited()
            listed = request(app, "POST", "/v1/services", headers=bearer, json={"project_id": "p"})
            assert listed.status_code == 200, listed.text
            # The person's enrolled browser session starts it, with or without the flag.
            token = consume_enrollment(cfg, issue_enrollment(cfg, "a"))
            started = request(
                app,
                "POST",
                "/v1/services",
                headers={"Cookie": SESSION_COOKIE + "=" + token},
                json={**start, "user_requested": False},
            )
            assert started.status_code == 200, started.text
            assert ["systemctl", "--user", "start", "demo.service"] in [
                call.args[0] for call in process.call_args_list
            ]
    finally:
        app.state.service.db.close()


def test_only_the_local_owner_saves_project_workflows(tmp_path):
    cfg = config(tmp_path)
    cfg["local_access"] = True
    cfg["clients"]["local"] = {"sha256": hashlib.sha256(b"local").hexdigest(), "projects": ["p"]}
    app = create_app(cfg)
    try:
        remote = request(
            app,
            "POST",
            "/v1/jobs/j1/save-workflow",
            headers={"Authorization": "Bearer a"},
            json={"id": "planted"},
        )
        assert remote.status_code == 403
        assert remote.json()["code"] == "workflow_save_local_only"
        # The owner on this computer reaches the save itself (here: an unknown job).
        local = request(app, "POST", "/v1/jobs/j1/save-workflow", json={"id": "mine"}, local=True)
        assert local.json()["code"] == "job_not_found", local.text
    finally:
        app.state.service.db.close()
