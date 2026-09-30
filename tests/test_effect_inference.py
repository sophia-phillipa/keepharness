"""Plain chat and standalone invocations publish via the real execution bridge."""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from test_approval_authority import ORIGIN, client_for
from test_effect_crash_recovery import jira_fixture  # noqa: F401
from test_effect_executor import configure_effects, request
from test_invocation_normalization import invocation_service

from agent_service.app import create_app
from agent_service.approval_sessions import issue_enrollment
from agent_service.effect_mcp import prepare_request


@pytest.mark.parametrize("invocation", [False, True], ids=["plain-chat", "standalone-agent"])
def test_inference_prepares_then_human_publishes(tmp_path, monkeypatch, jira_fixture, invocation):
    instance, identity = invocation_service(tmp_path, monkeypatch)
    config = configure_effects(instance.config, jira_fixture.endpoint)
    config["origins"] = [ORIGIN]
    config["codex"] = {"binary": "synthetic"}
    instance.db.close()
    app = create_app(config)
    service = app.state.service
    service.effects.credentials.set(
        "synthetic", {"email": "fixture@example.invalid", "token": "fixture-secret"}
    )
    data = dict(
        project_id="p",
        backend="codex",
        model="gpt-6-astra",
        prompt="Prepare publication",
        access_mode="full",
    )
    if invocation:
        item = next(
            item
            for item in service.resource_catalog(identity, "p", "codex", "gpt-6-astra")["items"]
            if item["name"] == "discussion"
        )
        data["invocations"] = [
            dict(
                kind="agent",
                resource_id=item["resource_id"],
                args="Prepare publication",
                order=0,
                mode="conversational",
            )
        ]
    job_id = service.submit(identity, data)["job_id"]
    with service.db:
        service.db.execute("UPDATE jobs SET state='running' WHERE id=?", (job_id,))
    row = service.job(identity, job_id)
    payload = json.loads(row["payload"])
    prepared = []

    async def provider(config, prompt, progress, *args):
        assert "fixture-secret" not in json.dumps(config)
        effect = await prepare_request(config["_effect_capability"], request())
        assert effect["status"] == "prepared"
        assert not jira_fixture.posts
        prepared.append(effect)
        return {"answer": "Publication prepared for human review."}

    async def scenario():
        with (
            patch("adapters.run_native", side_effect=provider),
            patch.object(service, "quota", AsyncMock(return_value=None)),
        ):
            result = await service.infer(row, payload)
        service.finish(job_id, "done", result)
        effect = prepared[0]
        assert service.effects.get(effect["effect_id"])["status"] == "prepared"
        async with client_for(app, headers={"Authorization": "Bearer a"}) as client:
            # The API/MCP owner's bearer cannot approve a publication, even in full mode.
            assert (
                await client.post("/v1/approvals/" + effect["gate_id"], json={"choice": "approve"})
            ).status_code == 403
            nonce = issue_enrollment(config, "a")
            assert (
                await client.post("/approve-device?nonce=" + nonce, headers={"Origin": ORIGIN})
            ).status_code == 303
            assert (
                await client.post("/v1/approvals/" + effect["gate_id"], json={"choice": "approve"})
            ).status_code == 200
        await service.effects.tasks[effect["effect_id"]]
        assert service.effects.get(effect["effect_id"])["receipt"] == {"issue_key": "TEST-1"}
        assert len(jira_fixture.posts) == 1
        await service.effects.close()
        service.db.close()

    asyncio.run(scenario())
