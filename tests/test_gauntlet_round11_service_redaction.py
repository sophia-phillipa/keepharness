import asyncio
import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from test_workspaces import config

from agent_service.app import create_app
from agent_service.approval_sessions import SESSION_COOKIE, consume_enrollment, issue_enrollment
from agent_service.log_config import redact


@pytest.mark.parametrize("syntax", ["registered_vault", "bearer"])
def test_service_action_diagnostics_are_redacted(tmp_path, syntax):
    async def scenario():
        cfg = config(tmp_path / "state")
        cfg["projects"]["p"]["service_units"] = ["demo.service"]
        cfg["services"]["codex"].update(mode="native", permissions={"shell": True})
        app = create_app(cfg)
        service = app.state.service
        secret = "SYNTHETIC-SERVICE-R11-SECRET-481"
        if syntax == "registered_vault":
            service.vault.set("fixture", {"password": secret})
            diagnostic = "Synthetic failure: " + secret
        else:
            diagnostic = "Synthetic failure: Authorization: Bearer " + secret
        assert secret not in redact(diagnostic)
        process = AsyncMock(
            side_effect=[(1, diagnostic), (0, "ActiveState=failed\nLoadState=loaded")]
        )
        try:
            with (
                patch(
                    "agent_service.service_control.shutil.which", return_value="/usr/bin/systemctl"
                ),
                patch("agent_service.service_control.process", process),
            ):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url="http://localhost",
                    headers={
                        "Cookie": SESSION_COOKIE
                        + "="
                        + consume_enrollment(cfg, issue_enrollment(cfg, "a"))
                    },
                ) as client:
                    response = await client.post(
                        "/v1/services",
                        json={
                            "project_id": "p",
                            "action": "start",
                            "unit": "demo.service",
                            "user_requested": True,
                        },
                    )
            assert response.status_code == 200, response.text
            stored = (service.root / "service-actions.jsonl").read_text()
            facts = {
                "syntax": syntax,
                "status": response.status_code,
                "mock_process_calls": process.await_count,
                "api_leak": secret in response.text,
                "durable_log_leak": secret in stored,
                "log_mode": oct((service.root / "service-actions.jsonl").stat().st_mode & 0o777),
            }
            print(json.dumps(facts))
            assert secret not in response.text and secret not in stored, facts
        finally:
            await service.effects.close()
            service.db.close()

    asyncio.run(scenario())
