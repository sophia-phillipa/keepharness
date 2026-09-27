"""A run that ends failed/interrupted/cancelled keeps its streamed answer text so a
page reload does not lose it (F-114). ``result.partial_answer`` mirrors the text
already sent as ``answer_delta`` events, capped and marked when it overflows.
"""

import asyncio
import json
from unittest.mock import AsyncMock

from agent_service.app import Service
from agent_service.services.queue_worker import PARTIAL_ANSWER_LIMIT
from agent_service.tools import ToolError
from tests.test_shared_projects import config


async def _run_to_terminal(service, identity, job_id):
    worker = asyncio.create_task(service.worker())
    try:
        async with asyncio.timeout(2):
            while service.job(identity, job_id)["state"] in ("queued", "running"):
                await asyncio.sleep(0.01)
        return service.job(identity, job_id)
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)


def _submit(service, identity):
    return service.submit(
        identity,
        {
            "project_id": "sem-projeto",
            "backend": "codex",
            "model": "codex-test",
            "effort": "low",
            "prompt": "hello",
        },
    )["job_id"]


def test_failed_run_keeps_the_streamed_answer(tmp_path):
    async def exercise():
        cfg = config(tmp_path)
        service = Service(cfg)
        identity = ("a", service.config["clients"]["a"])
        job_id = _submit(service, identity)

        async def execute(row):
            service.event(row["id"], "answer_delta", {"text": "partial "})
            service.event(row["id"], "answer_delta", {"text": "streamed text"})
            raise ToolError("codex_execution_failed: boom")

        service.execute = execute
        service.quota = AsyncMock(return_value={})
        try:
            row = await _run_to_terminal(service, identity, job_id)
            data = json.loads(row["result"])
            assert row["state"] == "failed"
            assert data["partial_answer"] == "partial streamed text"
        finally:
            service.db.close()

    asyncio.run(exercise())


def test_interrupted_run_keeps_the_streamed_answer_alongside_the_condition(tmp_path):
    async def exercise():
        cfg = config(tmp_path)
        service = Service(cfg)
        identity = ("a", service.config["clients"]["a"])
        job_id = _submit(service, identity)

        async def execute(row):
            service.event(row["id"], "answer_delta", {"text": "so far so good"})
            raise ToolError("provider_authentication_required")

        service.execute = execute
        service.quota = AsyncMock(return_value={})
        try:
            row = await _run_to_terminal(service, identity, job_id)
            data = json.loads(row["result"])
            assert row["state"] == "interrupted"
            assert data["partial_answer"] == "so far so good"
            assert data["condition"] == "provider_authentication_required"
            assert data["backend"] == "codex"
            assert "error_detail" in data
        finally:
            service.db.close()

    asyncio.run(exercise())


def test_cancelled_run_keeps_the_streamed_answer(tmp_path):
    async def exercise():
        cfg = config(tmp_path)
        service = Service(cfg)
        identity = ("a", service.config["clients"]["a"])
        job_id = _submit(service, identity)
        started = asyncio.Event()

        async def execute(row):
            service.event(row["id"], "answer_delta", {"text": "typing an answer"})
            started.set()
            await asyncio.sleep(10)

        service.execute = execute
        service.quota = AsyncMock(return_value={})
        worker = asyncio.create_task(service.worker())
        try:
            async with asyncio.timeout(2):
                await started.wait()
                while service.active != job_id:
                    await asyncio.sleep(0.01)
            service.cancel(identity, job_id)
            async with asyncio.timeout(2):
                while service.job(identity, job_id)["state"] in ("queued", "running"):
                    await asyncio.sleep(0.01)
            row = service.job(identity, job_id)
            data = json.loads(row["result"])
            assert row["state"] == "cancelled"
            assert data["partial_answer"] == "typing an answer"
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            service.db.close()

    asyncio.run(exercise())


def test_no_streamed_text_leaves_the_key_absent(tmp_path):
    async def exercise():
        cfg = config(tmp_path)
        service = Service(cfg)
        identity = ("a", service.config["clients"]["a"])
        job_id = _submit(service, identity)

        async def execute(row):
            raise ToolError("codex_execution_failed: boom")

        service.execute = execute
        service.quota = AsyncMock(return_value={})
        try:
            row = await _run_to_terminal(service, identity, job_id)
            data = json.loads(row["result"])
            assert row["state"] == "failed"
            assert "partial_answer" not in data
        finally:
            service.db.close()

    asyncio.run(exercise())


def test_partial_answer_is_bounded_with_a_truncation_marker(tmp_path):
    async def exercise():
        cfg = config(tmp_path)
        service = Service(cfg)
        identity = ("a", service.config["clients"]["a"])
        job_id = _submit(service, identity)

        async def execute(row):
            service.event(row["id"], "answer_delta", {"text": "x" * (PARTIAL_ANSWER_LIMIT + 500)})
            raise ToolError("codex_execution_failed: boom")

        service.execute = execute
        service.quota = AsyncMock(return_value={})
        try:
            row = await _run_to_terminal(service, identity, job_id)
            data = json.loads(row["result"])
            assert len(data["partial_answer"]) <= PARTIAL_ANSWER_LIMIT + len("\n\n[truncated]")
            assert data["partial_answer"].endswith("[truncated]")
        finally:
            service.db.close()

    asyncio.run(exercise())


def test_completed_run_result_is_unchanged(tmp_path):
    async def exercise():
        cfg = config(tmp_path)
        service = Service(cfg)
        identity = ("a", service.config["clients"]["a"])
        job_id = _submit(service, identity)

        async def execute(row):
            service.event(row["id"], "answer_delta", {"text": "the final answer"})
            return {"answer": "the final answer", "metrics": None}

        service.execute = execute
        service.quota = AsyncMock(return_value={})
        try:
            row = await _run_to_terminal(service, identity, job_id)
            data = json.loads(row["result"])
            assert row["state"] == "completed"
            assert "partial_answer" not in data
            assert data["answer"] == "the final answer"
        finally:
            service.db.close()

    asyncio.run(exercise())


def test_conversation_endpoint_returns_the_partial_answer(tmp_path):
    from starlette.testclient import TestClient

    from agent_service.app import create_app

    cfg = config(tmp_path)
    job_id = None

    async def exercise():
        nonlocal job_id
        service = Service(cfg)
        identity = ("a", service.config["clients"]["a"])
        job_id = _submit(service, identity)

        async def execute(row):
            service.event(row["id"], "answer_delta", {"text": "kept on reload"})
            raise ToolError("codex_execution_failed: boom")

        service.execute = execute
        service.quota = AsyncMock(return_value={})
        try:
            row = await _run_to_terminal(service, identity, job_id)
            assert row["state"] == "failed"
        finally:
            service.db.close()

    asyncio.run(exercise())
    app = create_app(cfg)
    try:
        client = TestClient(app, headers={"Authorization": "Bearer a"})
        response = client.get("/v1/conversations/" + job_id)
        assert response.status_code == 200
        turns = response.json()["turns"]
        assert turns[-1]["result"]["partial_answer"] == "kept on reload"
    finally:
        app.state.service.db.close()
