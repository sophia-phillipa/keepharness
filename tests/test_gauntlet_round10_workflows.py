import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from test_workflow_effect import effect_run, pending
from test_workflow_resume_rerun import setup_run

from agent_service import maestro, workflows
from agent_service.errors import APIError


@pytest.mark.parametrize("drift", ["edit", "delete"])
@pytest.mark.parametrize("rerun", [False, True])
def test_accepted_recovery_retry_survives_source_drift(tmp_path, drift, rerun):
    service, identity, row, data, plan = setup_run(tmp_path)
    folder = tmp_path / "project/workflows"
    folder.mkdir(parents=True)
    path = folder / "review.json"
    path.write_text(json.dumps(plan))
    service.config["projects"]["p"]["root"] = str(folder.parent)
    try:
        resolved = workflows.resolve_workflow(
            service.config,
            "p",
            "project/p/workflows/review.json",
            execution_mode=data["execution_mode"],
        )
        with patch.object(service, "infer", AsyncMock(return_value={"answer": "done"})):
            result = asyncio.run(maestro.execute_workflow(service, row, data, resolved))
        service.finish(row["id"], "completed", result)
        first = service.recover_workflow(identity, row["id"], {}, rerun=rerun, idem="retry-key")
        if drift == "edit":
            path.write_text(path.read_text() + "\n")
        else:
            path.unlink()
        second = service.recover_workflow(identity, row["id"], {}, rerun=rerun, idem="retry-key")
        assert second["job_id"] == first["job_id"] and second["reused"]
        assert (
            service.db.execute("SELECT count(*) FROM jobs WHERE idem='retry-key'").fetchone()[0]
            == 1
        )
        with pytest.raises(APIError, match="idempotency_conflict"):
            service.recover_workflow(
                identity,
                row["id"],
                {"workflow_inputs": {"changed": True}},
                rerun=rerun,
                idem="retry-key",
            )
        with pytest.raises(APIError, match="idempotency_conflict"):
            service.recover_workflow(identity, row["id"], {}, rerun=not rerun, idem="retry-key")
        service.config["clients"][identity[0]]["projects"] = []
        with pytest.raises(APIError) as denied:
            service.recover_workflow(identity, row["id"], {}, rerun=rerun, idem="retry-key")
        assert denied.value.status == 403
    finally:
        service.db.close()


def test_rerun_preserves_consumed_publication_approval(tmp_path):
    async def scenario():
        service, identity, row, data, plan = effect_run(tmp_path)
        try:
            with (
                patch.object(service, "infer", AsyncMock(return_value={"answer": "prepared"})),
                patch.object(
                    service.effects.driver,
                    "create",
                    AsyncMock(return_value=("done", {"issue_key": "SYNTH-1"})),
                ),
            ):
                first = asyncio.create_task(maestro.execute_plan(service, row, data, plan))
                original = await pending(service, first)
                service.gates.resolve(original, identity, {"choice": "approve"})
                await first
                before = dict(service.gates.repository.get(original))
                child_id = service.submit(identity, {**data})["job_id"]
                with service.db:
                    service.db.execute("UPDATE jobs SET state='running' WHERE id=?", (child_id,))
                child = service.job(identity, child_id)
                second = asyncio.create_task(
                    maestro.execute_plan(
                        service,
                        child,
                        {**data, "_workflow_parent_job_id": row["id"], "_workflow_from_step": 1},
                        plan,
                    )
                )
                fresh = await pending(service, second)
                try:
                    assert fresh != original
                    assert service.gates.repository.get(fresh)["state"] == "pending"
                    assert dict(service.gates.repository.get(original)) == before
                    assert (
                        before["state"] == "resolved" and json.loads(before["choice"]) == "approve"
                    )
                    assert before["resolved_by"] == identity[0] and before["resolved_at"] > 0
                    assert service.effects.for_job(row["id"])[0]["status"] == "done"
                finally:
                    second.cancel()
                    await asyncio.gather(second, return_exceptions=True)
        finally:
            await service.effects.close()
            service.db.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("rerun", [False, True])
def test_legacy_recovery_retry_preserves_existing_idempotency_contract(tmp_path, rerun):
    import hashlib

    service, identity, row, data, plan = setup_run(tmp_path)
    try:
        with patch.object(service, "infer", AsyncMock(return_value={"answer": "done"})):
            result = asyncio.run(maestro.execute_plan(service, row, data, plan))
        service.finish(row["id"], "completed", result)
        first = service.recover_workflow(identity, row["id"], {}, rerun=rerun, idem="legacy-retry")
        accepted = json.loads(service.job(identity, first["job_id"])["payload"])
        accepted.pop("_workflow_recovery_digest")
        legacy_digest = hashlib.sha256(json.dumps(accepted, sort_keys=True).encode()).hexdigest()
        with service.db:
            service.db.execute(
                "UPDATE jobs SET payload=?,digest=? WHERE id=?",
                (json.dumps(accepted), legacy_digest, first["job_id"]),
            )
        second = service.recover_workflow(identity, row["id"], {}, rerun=rerun, idem="legacy-retry")
        assert second["job_id"] == first["job_id"] and second["reused"]
        with pytest.raises(APIError, match="idempotency_conflict"):
            service.recover_workflow(
                identity,
                row["id"],
                {"workflow_inputs": {"changed": True}},
                rerun=rerun,
                idem="legacy-retry",
            )
        with pytest.raises(APIError, match="idempotency_conflict"):
            service.recover_workflow(identity, row["id"], {}, rerun=not rerun, idem="legacy-retry")
    finally:
        service.db.close()
