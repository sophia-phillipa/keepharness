import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from test_execution_modes import service


@pytest.mark.parametrize(
    "circular,persistence_error", [(False, False), (True, False), (True, True)]
)
def test_changed_root_symlink_does_not_kill_scheduler(tmp_path, circular, persistence_error):
    async def scenario():
        instance, identity = service(tmp_path / "state")
        root = tmp_path / "project"
        root.mkdir()
        instance.config["projects"]["p"]["root"] = str(root)
        instance.config["projects"]["q"] = {"root": str(tmp_path / "unrelated")}
        identity[1]["projects"].append("q")
        instance.config["services"]["codex"]["projects"].append("q")
        first = instance.submit(
            identity,
            dict(project_id="p", backend="codex", model="gpt-6-astra", prompt="synthetic first"),
        )["job_id"]
        second = instance.submit(
            identity,
            dict(
                project_id="q", backend="codex", model="gpt-6-astra", prompt="synthetic unrelated"
            ),
        )["job_id"]
        instance.execute = AsyncMock(return_value={"answer": "synthetic"})
        instance.quota = AsyncMock(return_value={})
        if circular:
            root.rmdir()
            root.symlink_to(root.name)
        original_finish = instance.finish
        failures = []

        def finish(job, state, result):
            if persistence_error and job == first and len(failures) < 2:
                failures.append(job)
                raise OSError("Synthetic terminal persistence failure")
            return original_finish(job, state, result)

        instance.finish = finish
        worker = asyncio.create_task(instance.worker())
        try:
            for _ in range(100):
                if worker.done() or (
                    instance.conversation_repository.state(second)[0] == "completed"
                    and instance.conversation_repository.state(first)[0] in ("failed", "completed")
                ):
                    break
                await asyncio.sleep(0.005)
            observed = {
                "worker_done": worker.done(),
                "error": repr(worker.exception()) if worker.done() else None,
                "first": instance.conversation_repository.state(first)[0],
                "unrelated": instance.conversation_repository.state(second)[0],
                "inferences": instance.execute.await_count,
            }
            print("ROOT_LOOP", json.dumps(observed))
            assert observed["first"] == ("failed" if circular else "completed")
            if circular:
                assert (
                    json.loads(instance.conversation_repository.get(first)["result"])["error"]
                    == "project_root_unavailable"
                )
            if circular:
                root.unlink()
                root.mkdir()
                repaired = instance.submit(
                    identity,
                    dict(
                        project_id="p", backend="codex", model="gpt-6-astra", prompt="After repair"
                    ),
                )["job_id"]
                instance.wake.set()
                await asyncio.sleep(0.05)
                observed["repaired_unrelated"] = instance.conversation_repository.state(second)[0]
                print("AFTER_ROOT_REPAIR", json.dumps(observed))
                assert instance.conversation_repository.state(repaired)[0] == "completed"
            assert not worker.done(), observed
            assert observed["unrelated"] == "completed", observed
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            instance.db.close()

    asyncio.run(scenario())
