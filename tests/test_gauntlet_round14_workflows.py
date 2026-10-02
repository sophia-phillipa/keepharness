import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from test_workflow_resume_rerun import setup_run

from agent_service import maestro
from agent_service.errors import APIError
from agent_service.spans import events_to_spans


def test_save_rejects_disappeared_project_root_without_recreating_it(tmp_path):
    service, identity, row, data, plan = setup_run(tmp_path / "state")
    root = tmp_path / "project"
    root.mkdir()
    service.config["projects"]["p"]["root"] = str(root)
    try:
        with patch.object(service, "infer", AsyncMock(return_value={"answer": "done"})):
            result = asyncio.run(maestro.execute_plan(service, row, data, plan))
        with service.db:
            service.conversation_repository.set_result(row["id"], "completed", json.dumps(result))

        deletion = service.project_folder_deletion(identity, "p")
        asyncio.run(service.delete_project_folder(identity, "p", {**deletion, "confirmed": True}))
        assert not root.exists()
        assert "p" in service.deleted_project_folders

        with pytest.raises(APIError, match="project_folder_deleted"):
            service.save_workflow(identity, row["id"], "saved-after-root-loss")
        assert not root.exists(), "saving must not silently recreate a missing registered project"
        assert not (root / "workflows/saved-after-root-loss.json").exists()
    finally:
        service.db.close()


@pytest.mark.parametrize("partial", [False, True])
def test_reused_recovery_projects_each_completed_step(tmp_path, partial):
    service, identity, source, source_data, plan = setup_run(tmp_path)
    try:
        with patch.object(
            service,
            "infer",
            AsyncMock(side_effect=[{"answer": "first"}, {"answer": "second"}]),
        ):
            source_result = asyncio.run(maestro.execute_plan(service, source, source_data, plan))
        service.finish(source["id"], "completed", source_result)

        child_id = service.recover_workflow(
            identity, source["id"], {"from_step": 2} if partial else {}, rerun=partial
        )["job_id"]
        with service.db:
            service.conversation_repository.set_running(child_id)
        child = service.job(identity, child_id)
        child_data = json.loads(child["payload"])
        with patch.object(
            service,
            "infer",
            AsyncMock(return_value={"answer": "fresh", "metrics": {"input_tokens": 3}}),
        ) as inference:
            child_result = asyncio.run(
                maestro.execute_workflow(
                    service, child, child_data, child_data["_declared_workflow"]
                )
            )
        service.finish(child_id, "completed", child_result)

        events = service.message_repository.all_events(child_id)
        assert inference.await_count == int(partial)
        assert sum(event["type"] == "workflow_checkpoint_reused" for event in events) == (
            1 if partial else 2
        )
        assert len(child_result["orchestration"]["steps"]) == 2

        spans = events_to_spans(service.job(identity, child_id), events)
        projected_steps = [span for span in spans if span["kind"] == "invoke_agent"]
        reused = [span for span in projected_steps if span["attrs"].get("checkpoint_reused")]
        assert len(reused) == (1 if partial else 2)
        for span in reused:
            assert span["attrs"]["source_job_id"] == source["id"]
            assert span["attrs"]["source_execution_id"]
            assert "gen_ai.usage.input_tokens" not in span["attrs"]
            assert span["end_ts"] == span["start_ts"]
        assert len(projected_steps) == 2, (
            "the completed child reports two reused workflow steps, so its trace must "
            "project two auditable step records"
        )
    finally:
        service.db.close()
