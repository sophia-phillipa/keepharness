import asyncio
import json
from unittest.mock import AsyncMock, patch

from test_workflow_resume_rerun import setup_run

from agent_service import maestro
from agent_service.spans import events_to_spans


def test_reused_skip_preserves_outcome(tmp_path):
    service, identity, row, data, plan = setup_run(tmp_path)
    plan["steps"][0]["id"] = "probe"
    plan["steps"][1]["condition"] = {"from": "probe.ok", "is": True}
    try:
        with patch.object(
            service,
            "infer",
            AsyncMock(return_value={"answer": '```harness-result\n{"ok":false}\n```'}),
        ) as infer:
            original = asyncio.run(maestro.execute_plan(service, row, data, plan))
        assert infer.await_count == 1
        child_id = service.submit(identity, data)["job_id"]
        child = service.job(identity, child_id)
        with patch.object(service, "infer", AsyncMock()) as infer:
            result = asyncio.run(
                maestro.execute_plan(
                    service,
                    child,
                    {**data, "_workflow_parent_job_id": row["id"], "_workflow_resume": True},
                    plan,
                )
            )
        assert infer.await_count == 0
        events = service.message_repository.all_events(child_id)
        spans = events_to_spans(child, events)
        step = next(s for s in spans if s["attrs"].get("index") == 2)
        print(
            json.dumps(
                {
                    "original": original["orchestration"]["steps"][1]["outcome"],
                    "recovered": result["orchestration"]["steps"][1]["outcome"],
                    "span": step,
                },
                indent=2,
            )
        )
        assert step["attrs"]["outcome"] == "skipped"
        assert step["status"] != "ok"
        assert original["orchestration"]["steps"][1]["outcome"] == "skipped"
        assert result["orchestration"]["steps"][1]["outcome"] == "skipped"
        assert step["attrs"]["source_job_id"] == row["id"]
        completed = next(s for s in spans if s["attrs"].get("index") == 1)
        assert completed["attrs"]["outcome"] == "completed"
    finally:
        service.db.close()
