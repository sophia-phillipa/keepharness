import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from test_effect_executor import configure_effects, request
from test_workflow_resume_rerun import setup_run

from agent_service import maestro
from agent_service.tools import ToolError


def effect_run(tmp_path):
    service, identity, row, data, plan = setup_run(tmp_path)
    configure_effects(service.config)
    service.effects.credentials.set(
        "synthetic", {"email": "fixture@example.invalid", "token": "secret"}
    )
    with service.db:
        service.db.execute("UPDATE jobs SET state='running' WHERE id=?", (row["id"],))
    plan["steps"] = [plan["steps"][0]]
    plan["steps"][0].update(publish=True, effect=request())
    return service, identity, row, data, plan


async def pending(service, task):
    for _ in range(100):
        if service.approvals or task.done():
            break
        await asyncio.sleep(0)
    assert not task.done()
    return next(iter(service.approvals))


def test_workflow_effect_waits_for_real_gate_and_rerun_has_fresh_key(tmp_path):
    async def scenario():
        service, identity, row, data, plan = effect_run(tmp_path)
        keys = []
        with (
            patch.object(service, "infer", AsyncMock(return_value={"answer": "prepared"})),
            patch.object(
                service.effects.driver,
                "create",
                AsyncMock(return_value=("done", {"issue_key": "TEST-1"})),
            ) as publish,
        ):
            for number in range(2):
                if number:
                    source = row
                    child = service.submit(identity, {**data})["job_id"]
                    with service.db:
                        service.db.execute("UPDATE jobs SET state='running' WHERE id=?", (child,))
                    row = service.job(identity, child)
                    data = {
                        **data,
                        "_workflow_parent_job_id": source["id"],
                        "_workflow_from_step": 1,
                    }
                task = asyncio.create_task(maestro.execute_plan(service, row, data, plan))
                gate_id = await pending(service, task)
                assert publish.await_count == number
                keys.append(service.effects.for_job(row["id"])[0]["effect_id"])
                service.gates.resolve(gate_id, identity, {"choice": "approve"})
                await task
            assert keys[0] != keys[1]
            assert publish.await_count == 2
        await service.effects.close()
        service.db.close()

    asyncio.run(scenario())


def test_changed_input_during_publication_wait_invalidates_approval(tmp_path):
    async def scenario():
        service, identity, row, data, plan = effect_run(tmp_path)
        data["workflow_inputs"] = {"version": 1}
        with (
            patch.object(service, "infer", AsyncMock(return_value={"answer": "prepared"})),
            patch.object(
                service.effects.driver, "create", AsyncMock(return_value=("done", {}))
            ) as publish,
        ):
            task = asyncio.create_task(maestro.execute_plan(service, row, data, plan))
            gate_id = await pending(service, task)
            data["workflow_inputs"]["version"] = 2
            service.gates.resolve(gate_id, identity, {"choice": "approve"})
            with pytest.raises(ToolError, match="workflow_effect_not_completed"):
                await task
            assert service.effects.for_job(row["id"])[0]["status"] == "invalidated"
            publish.assert_not_called()
        await service.effects.close()
        service.db.close()

    asyncio.run(scenario())


def test_unknown_effect_blocks_recovery(tmp_path):
    async def scenario():
        service, identity, row, data, plan = effect_run(tmp_path)
        with (
            patch.object(service, "infer", AsyncMock(return_value={"answer": "prepared"})),
            patch.object(
                service.effects.driver, "create", AsyncMock(return_value=("unknown", None))
            ),
        ):
            task = asyncio.create_task(maestro.execute_plan(service, row, data, plan))
            service.gates.resolve(await pending(service, task), identity, {"choice": "approve"})
            with pytest.raises(ToolError):
                await task
        with pytest.raises(ToolError, match="workflow_effect_outcome_unknown"):
            maestro.ensure_recovery_safe(service, row["id"])
        await service.effects.close()
        service.db.close()

    asyncio.run(scenario())


def test_incomplete_step_never_prepares_or_publishes(tmp_path):
    service, identity, row, data, plan = effect_run(tmp_path)
    with (
        patch.object(
            service, "infer", AsyncMock(return_value={"answer": "partial", "incomplete": True})
        ),
        patch.object(service.effects, "prepare", AsyncMock()) as prepare,
    ):
        with pytest.raises(ToolError, match="maestro_step_incomplete"):
            asyncio.run(maestro.execute_plan(service, row, data, plan))
    prepare.assert_not_called()
    service.db.close()


@pytest.mark.parametrize("source", ["workflow", "workspace"])
def test_source_changed_during_publication_wait_invalidates_gate(tmp_path, source):
    async def scenario():
        service, identity, row, data, plan = effect_run(tmp_path)
        if source == "workflow":
            root = tmp_path / "project"
            folder = root / "workflows"
            folder.mkdir(parents=True)
            path = folder / "publish.json"
            path.write_text(json.dumps(plan))
            service.config["projects"]["p"]["root"] = str(root)
            from agent_service import resources, workflows

            item = next(
                item
                for item in resources.discover(service.config, "p", "codex")["items"]
                if item["kind"] == "workflow"
            )
            plan = workflows.resolve_workflow(service.config, "p", item["resource_id"])
        else:
            folder = service.root / "workspaces" / "test-workspace" / "work"
            folder.mkdir(parents=True)
            path = folder / "input.txt"
            path.write_text("input")
            with service.db:
                service.project_repository.add_workspace(
                    "test-workspace", "p", "a", "test", 1, json.dumps([{"path": "input.txt"}])
                )
            data["workspace_id"] = "test-workspace"
        with (
            patch.object(service, "infer", AsyncMock(return_value={"answer": "prepared"})),
            patch.object(
                service.effects.driver, "create", AsyncMock(return_value=("done", {}))
            ) as publish,
        ):
            task = asyncio.create_task(maestro.execute_plan(service, row, data, plan))
            gate_id = await pending(service, task)
            path.unlink()
            service.gates.resolve(gate_id, identity, {"choice": "approve"})
            with pytest.raises(ToolError, match="workflow_effect_not_completed"):
                await task
            assert service.effects.for_job(row["id"])[0]["status"] == "invalidated"
            publish.assert_not_called()
        await service.effects.close()
        service.db.close()

    asyncio.run(scenario())


def test_restart_after_approval_before_dispatch_requires_new_gate(tmp_path):
    from agent_service.app import Service

    async def scenario():
        service, identity, row, data, plan = effect_run(tmp_path)
        cfg, job_id = service.config, row["id"]
        with (
            patch.object(service, "infer", AsyncMock(return_value={"answer": "prepared"})),
            patch.object(
                service.effects.driver, "create", AsyncMock(return_value=("done", {}))
            ) as publish,
        ):
            task = asyncio.create_task(maestro.execute_plan(service, row, data, plan))
            old_gate = await pending(service, task)
            old_effect = service.effects.for_job(job_id)[0]["effect_id"]
            service.gates.resolve(old_gate, identity, {"choice": "approve"})
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            publish.assert_not_called()
        await service.effects.close()
        service.db.close()
        service = Service(cfg)
        job_id = service.recover_workflow(identity, job_id, {})["job_id"]
        with service.db:
            service.db.execute("UPDATE jobs SET state='running' WHERE id=?", (job_id,))
        row = service.job(identity, job_id)
        data = json.loads(row["payload"])
        with (
            patch.object(service, "infer", AsyncMock(return_value={"answer": "prepared"})),
            patch.object(
                service.effects.driver,
                "create",
                AsyncMock(return_value=("done", {"issue_key": "TEST-1"})),
            ) as publish,
        ):
            task = asyncio.create_task(
                maestro.execute_plan(service, row, {**data, "_workflow_resume": True}, plan)
            )
            new_gate = await pending(service, task)
            assert new_gate != old_gate
            assert service.effects.for_job(job_id)[-1]["effect_id"] != old_effect
            publish.assert_not_called()
            service.gates.resolve(new_gate, identity, {"choice": "approve"})
            await task
            assert publish.await_count == 1
        await service.effects.close()
        service.db.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("incomplete", [False, True])
def test_provider_prepared_effect_waits_for_successful_step_completion(tmp_path, incomplete):
    async def scenario():
        service, identity, row, data, plan = effect_run(tmp_path)
        plan["steps"][0].pop("effect")
        plan["steps"][0]["publish"] = False
        finish = asyncio.Event()

        async def infer(row, payload):
            await service.effects.prepare(
                row["id"], request(), execution_id=payload["_execution_id"]
            )
            await finish.wait()
            return {"answer": "prepared", "incomplete": incomplete}

        with (
            patch.object(service, "infer", infer),
            patch.object(
                service.effects.driver,
                "create",
                AsyncMock(return_value=("done", {"issue_key": "TEST-1"})),
            ) as publish,
        ):
            task = asyncio.create_task(maestro.execute_plan(service, row, data, plan))
            gate = await pending(service, task)
            service.gates.resolve(gate, identity, {"choice": "approve"})
            for _ in range(3):
                await asyncio.sleep(0)
            publish.assert_not_called()
            finish.set()
            if incomplete:
                with pytest.raises(ToolError, match="maestro_step_incomplete"):
                    await task
                publish.assert_not_called()
                assert service.effects.for_job(row["id"])[0]["status"] == "invalidated"
            else:
                await task
                assert publish.await_count == 1
        await service.effects.close()
        service.db.close()

    asyncio.run(scenario())


def test_media_bytes_changed_with_unchanged_metadata_invalidate_publication(tmp_path):
    async def scenario():
        service, identity, row, data, plan = effect_run(tmp_path)
        folder = service.root / "files" / "p" / "image"
        folder.mkdir(parents=True)
        path = folder / "source"
        path.write_bytes(b"original-image-bytes")
        with service.db:
            service.message_repository.add_file(
                "image",
                "p",
                "image.png",
                20,
                "same-db-hash",
                json.dumps([{"media_type": "image/png"}]),
                "a",
            )
        data["file_ids"] = ["image"]
        with (
            patch.object(service, "infer", AsyncMock(return_value={"answer": "prepared"})),
            patch.object(
                service.effects.driver, "create", AsyncMock(return_value=("done", {}))
            ) as publish,
        ):
            task = asyncio.create_task(maestro.execute_plan(service, row, data, plan))
            gate = await pending(service, task)
            path.write_bytes(b"replacement-image-bytes")
            service.gates.resolve(gate, identity, {"choice": "approve"})
            with pytest.raises(ToolError, match="workflow_effect_not_completed"):
                await task
            assert service.effects.for_job(row["id"])[0]["status"] == "invalidated"
            publish.assert_not_called()
        await service.effects.close()
        service.db.close()

    asyncio.run(scenario())
