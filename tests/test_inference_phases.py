"""Ordering contract of Service.infer: prepare, run, finalize."""

import asyncio
import json
from unittest.mock import patch

import pytest
from test_workspaces import config

from agent_service import conversation_context
from agent_service.app import APIError, Service


def service(tmp_path):
    cfg = config(tmp_path)
    cfg["codex"] = {"binary": "fixture"}
    return Service(cfg), ("a", cfg["clients"]["a"])


def submitted(instance, identity, **extra):
    job = instance.submit(
        identity,
        {"project_id": "p", "backend": "codex", "model": "gpt-6-astra", "prompt": "hi", **extra},
    )["job_id"]
    row = instance.job(identity, job)
    return row, json.loads(row["payload"])


def events(instance, job):
    return [
        row["type"]
        for row in instance.db.execute("SELECT type FROM events WHERE job=? ORDER BY id", (job,))
    ]


def recorder(order):
    async def quota(refresh=False):
        order.append("quota")
        return {"available": True}

    def cursor(session, job, result, mode, started=False):
        order.append("cursor_started" if started else "cursor")

    def panel(project, thinking="", answer="", finished=False, **kwargs):
        order.append("panel_finished" if finished else "panel")

    return quota, cursor, panel


def test_admission_errors_precede_executor_registration(tmp_path):
    instance, identity = service(tmp_path)
    try:
        row, data = submitted(instance, identity)
        with pytest.raises(APIError) as error:
            asyncio.run(instance.infer(row, {**data, "prompt": "   "}))
        assert error.value.code == "prompt_required"
        assert row["id"] not in instance.active_executors
        with pytest.raises(APIError) as error:
            asyncio.run(instance.infer(row, {**data, "backend": "unknown"}))
        assert error.value.code == "backend_unavailable"
        assert row["id"] not in instance.active_executors
        assert not instance.provider_inflight
    finally:
        instance.db.close()


def test_native_turn_reports_quota_around_the_adapter_and_saves_the_cursor_last(tmp_path):
    instance, identity = service(tmp_path)
    order = []
    quota, cursor, panel = recorder(order)

    async def run(config, prompt, progress, *args):
        order.append("adapter")
        progress("answer_delta", {"text": "done"})
        return {"answer": "done"}

    try:
        row, data = submitted(instance, identity)
        assert data["execution_mode"] == "native"
        with (
            patch("adapters.run_native", side_effect=run),
            patch.object(instance, "quota", side_effect=quota),
            patch.object(instance, "panel", side_effect=panel),
            patch.object(conversation_context, "save_cursor", side_effect=cursor),
        ):
            result = asyncio.run(instance.infer(row, data))
        assert order == ["quota", "adapter", "panel", "quota", "cursor"]
        assert events(instance, row["id"]) == [
            "queued",
            "publication_policy",
            "quota_before",
            "answer_delta",
            "quota_after",
        ]
        assert {"quota_before", "quota_after"} <= set(result)
    finally:
        instance.db.close()


def test_scoped_turn_finishes_the_panel_before_quota_after(tmp_path):
    instance, identity = service(tmp_path)
    order = []
    quota, cursor, panel = recorder(order)

    async def run(config, prompt, progress, *args, **kwargs):
        order.append("adapter")
        return {"answer": "done"}

    try:
        row, data = submitted(instance, identity, execution_mode="scoped")
        with (
            patch("adapters.run_scoped", side_effect=run),
            patch.object(instance, "quota", side_effect=quota),
            patch.object(instance, "panel", side_effect=panel),
            patch.object(conversation_context, "save_cursor", side_effect=cursor),
        ):
            result = asyncio.run(instance.infer(row, data))
        assert order == ["quota", "adapter", "panel_finished", "quota", "cursor"]
        assert events(instance, row["id"]) == [
            "queued",
            "publication_policy",
            "quota_before",
            "quota_after",
        ]
        assert result["quota_before"] == result["quota_after"] == {"available": True}
    finally:
        instance.db.close()
