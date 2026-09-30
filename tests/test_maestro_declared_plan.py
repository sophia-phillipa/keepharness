import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from agent_service import maestro
from agent_service.app import Service
from agent_service.tools import ToolError
from test_workspaces import config


def test_declared_chain_executes_without_planner_and_keeps_order(tmp_path):
    service = Service(config(tmp_path))
    identity = ("a", service.config["clients"]["a"])
    submitted = service.submit(identity, {"project_id": "p", "backend": "codex", "model": "gpt-6-astra", "effort": "low", "prompt": "Review"})
    row = service.job(identity, submitted["job_id"])
    data = json.loads(row["payload"])
    plan = {"steps": [{"role": role, "backend": "codex", "model": "gpt-6-astra", "effort": "low", "task": role, "reason": "Declared"} for role in ("research", "review")]}
    with patch.object(service, "infer", AsyncMock(side_effect=[{"answer": "evidence"}, {"answer": "reviewed"}])) as infer:
        result = asyncio.run(maestro.execute_plan(service, row, data, plan))
    assert infer.await_count == 2
    assert [call.args[1]["_maestro_stage"] for call in infer.call_args_list] == ["1", "2"]
    assert "evidence" in infer.call_args_list[1].args[1]["prompt"]
    assert result["answer"] == "reviewed"
    assert result["orchestration"]["coordinator"] is None
    events = list(service.db.execute("SELECT type,data FROM events WHERE job=?", (row["id"],)))
    assert "maestro_planning" not in [event["type"] for event in events]
    assert [json.loads(event["data"])["role"] for event in events if event["type"] == "invocation_started"] == ["research", "review"]
    service.db.close()


def test_declared_plan_cannot_skip_policy_or_continue_after_failure(tmp_path):
    service = Service(config(tmp_path))
    row = {"id": "run", "project": "p", "owner": "a"}
    step = {"role": "review", "backend": "codex", "model": "forbidden", "effort": "low", "task": "review", "reason": "declared"}
    with patch.object(service, "infer", AsyncMock()) as infer, pytest.raises(ToolError):
        asyncio.run(maestro.execute_plan(service, row, {"project_id": "p"}, {"steps": [step]}))
    infer.assert_not_called()
    service.db.close()


def test_submitted_resource_chain_uses_declared_dispatch(tmp_path, monkeypatch):
    from test_invocation_normalization import invocation_service
    service, identity = invocation_service(tmp_path, monkeypatch)
    items = service.resource_catalog(identity, "p", "codex", "gpt-6-astra")["items"]
    refs = [{"id": item["id"], "revision": item["revision"], "token": "/" + item["name"]} for item in items if item["name"] in ("reviewer", "writer")]
    submitted = service.submit(identity, {"project_id": "p", "backend": "codex", "model": "gpt-6-astra", "effort": "low", "prompt": "/reviewer inspect\n/writer describe", "resource_selections": refs})
    row = service.job(identity, submitted["job_id"])
    with patch.object(service, "infer", AsyncMock(side_effect=[{"answer": "evidence"}, {"answer": "written"}])) as infer:
        result = asyncio.run(service.execute(row))
    assert infer.await_count == 2
    assert infer.call_args_list[0].args[1]["prompt"] == "/reviewer inspect\n"
    assert infer.call_args_list[1].args[1]["prompt"] == "/writer describe"
    assert "evidence" in infer.call_args_list[1].args[1]["_invocation_context"]
    assert result["answer"] == "written"
    service.db.close()
