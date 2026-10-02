"""The single-worker job queue: fair dispatch, execution deadlines and cancellation.

Queue state (``active``, ``task``, ``wake``, ``last_served``) stays on the service,
following the ``maestro.run(service, ...)`` pattern.
"""

import asyncio
import json
import logging
import re
import sqlite3
import time
import traceback
from pathlib import Path

from .. import maestro, tools
from ..config import TERMINAL
from ..conversation_context import context_overflow
from ..errors import APIError
from .budgets import RuntimeBudget, timeout_seconds

logger = logging.getLogger(__name__)

# Provider account conditions shared with the UI: a run stopped by one ends ``interrupted``
# with ``{"condition": <code>, "backend": <provider>}`` instead of a failure.
PROVIDER_CONDITIONS = frozenset(
    {
        "provider_authentication_required",
        "provider_authentication_failed",
        "provider_quota_exhausted",
        "provider_rate_limit",
    }
)
# Older Claude adapter codes, still accepted as aliases.
CONDITION_ALIASES = {
    "claude_authentication_failed": "provider_authentication_required",
    "claude_authentication_required": "provider_authentication_required",
    "claude_rate_limit": "provider_quota_exhausted",
    "claude_quota_exhausted": "provider_quota_exhausted",
}
# Provider messages carried by "<provider>_execution_failed: <message>" (Codex).
CONDITION_PATTERNS = (
    ("provider_quota_exhausted", re.compile(r"usage limit|quota", re.I)),
    ("provider_rate_limit", re.compile(r"\b429\b|rate.?limit|too many requests", re.I)),
    (
        "provider_authentication_required",
        re.compile(r"\b401\b|unauthori[sz]ed|not logged in|log ?in again|access token", re.I),
    ),
)
CONDITION_ANSWERS = {
    "provider_authentication_required": "Renew access to {} in the admin panel.",
    "provider_authentication_failed": "Renew access to {} in the admin panel.",
    "provider_quota_exhausted": "Wait for the {} quota to renew, or select another provider.",
    "provider_rate_limit": "{} is limiting requests. Wait a moment, or select another provider.",
}
PROVIDER_NAMES = {"codex": "Codex", "claude": "Claude", "gemini": "Gemini", "deepseek": "DeepSeek"}
# F-114: a failed/interrupted/cancelled run keeps the answer text already streamed to
# the UI, so a page reload does not lose it. Bounded like the other request-side limits
# in this service (agent_service/routes/__init__.py, conversation_service.py).
PARTIAL_ANSWER_LIMIT = 200_000
TRUNCATION_MARKER = "\n\n[truncated]"


def partial_answer(service, job):
    """The job's streamed answer text so far, capped and marked when it overflows.

    Returns ``None`` when nothing was streamed, so terminal states with no partial
    text never gain the key.
    """
    text = "".join(
        data.get("text", "")
        for row in service.message_repository.answer_deltas(job)
        if not (data := json.loads(row["data"])).get("parent_tool_use_id")
    )
    if not text:
        return None
    if len(text) > PARTIAL_ANSWER_LIMIT:
        text = text[:PARTIAL_ANSWER_LIMIT] + TRUNCATION_MARKER
    return text


def with_partial_answer(service, job, result):
    """Add ``partial_answer`` to a terminal result, in place, when there is text to keep."""
    text = partial_answer(service, job)
    if text is not None:
        result["partial_answer"] = text
    return result


def provider_condition(code):
    """Map an adapter failure code to a generic provider condition, or ``None``."""
    if code in PROVIDER_CONDITIONS:
        return code
    if code in CONDITION_ALIASES:
        return CONDITION_ALIASES[code]
    prefix, separator, message = code.partition(": ")
    if separator and prefix.endswith("_execution_failed"):
        return next((name for name, pattern in CONDITION_PATTERNS if pattern.search(message)), None)
    return None


def next_job(service):
    # Queue is bounded to 32; choose the least recently served owner, FIFO within it.
    rows = service.conversation_repository.ready()
    if not rows:
        return None
    row = min(
        rows,
        key=lambda item: (service.last_served.get(item["owner"], 0), item["created"], item["id"]),
    )
    service.dispatch_sequence += 1
    service.last_served[row["owner"]] = service.dispatch_sequence
    return row


def settle(service, job, state, result):
    """Record an error path's terminal state; a failure here must not end the worker loop."""
    if job in service.runtime_budgets:
        result.update(service.runtime_budgets[job].metrics())
    for attempt in (1, 2):
        try:
            return service.finish(job, state, result)
        except Exception:
            # A transient persistence error (e.g. a full disk) is retried once; otherwise
            # startup recovery later marks the job, still ``running``, as interrupted.
            logger.exception("Could not record job %s as %s (attempt %d)", job, state, attempt)


async def finish_when_available(service, job, state, result):
    """Retain the outcome and scheduler ownership while SQLite is temporarily locked."""
    while True:
        try:
            return service.finish(job, state, result)
        except sqlite3.OperationalError as exc:
            if getattr(exc, "sqlite_errorcode", 0) & 0xFF not in (
                sqlite3.SQLITE_BUSY,
                sqlite3.SQLITE_LOCKED,
            ):
                raise
            if asyncio.current_task().cancelling():
                # Shutdown recovery owns any still-uncommitted terminal outcome.
                return
            await asyncio.sleep(0.05)


async def settle_running(service, job, state, result):
    if job in service.runtime_budgets:
        result.update(service.runtime_budgets[job].metrics())
    for attempt in (1, 2):
        try:
            return await finish_when_available(service, job, state, result)
        except Exception:
            # Preserve the bounded retry for other transient storage failures.
            logger.exception("Could not record job %s as %s (attempt %d)", job, state, attempt)


def conversation_key(service, row):
    current = dict(row)
    seen = set()
    while current["id"] not in seen:
        seen.add(current["id"])
        parent = json.loads(current["payload"]).get("parent_job_id")
        if not parent:
            return current["id"]
        ancestor = service.conversation_repository.get(parent)
        if ancestor is None:
            return parent
        current = dict(ancestor)
    return min(seen)


def ownership_roots(service, row):
    project = service.config.get("projects", {}).get(row["project"], {})
    roots = [project.get("root"), *project.get("additional_roots", [])]
    data = json.loads(row["payload"])
    if data.get("workspace_id"):
        roots.append(service.workspace_root(data["workspace_id"]))
    if data.get("backend") == "local":
        roots.extend(
            service.config.get("local", {}).get("model_roots", {}).get(data.get("model"), [])
        )
    if (
        data.get("backend") in ("maestro", "auto")
        or data.get("_declared_workflow")
        or len(data.get("invocations", [])) > 1
        or any(item.get("kind") == "workflow" for item in data.get("invocations", []))
    ):
        for candidate in maestro.candidates(service.config, row["project"]):
            if candidate["backend"] == "local":
                roots.extend(
                    service.config.get("local", {})
                    .get("model_roots", {})
                    .get(candidate["model"], [])
                )
    state = service.config.get("control_state_dir", service.config["state_dir"])
    roots.extend(
        Path(state) / "catalog_runtime" / catalog_id for catalog_id in project.get("catalogs", [])
    )
    for catalog in service.config.get("catalogs", []):
        if catalog.get("trusted") is True and catalog.get("id") in project.get("catalogs", []):
            if catalog["id"] not in project.get("catalog_pins", {}):
                roots.append(catalog["root"])
    return roots


async def run(service):
    """Schedule independent provider lanes after acquiring write ownership."""
    tasks = {}
    conversations = {}
    lanes = {}
    reasons = {}
    service.stopping = False

    def released(task, job, backend, conversation):
        service.write_ownership.release(job)
        conversations.pop(conversation, None)
        lanes[backend] -= 1
        tasks.pop(job, None)
        if service.job_tasks.get(job) is task:
            service.job_tasks.pop(job, None)
        if task.cancelled() and service.conversation_repository.state(job)[0] == "running":
            settle(service, job, "cancelled", {"metrics": None})
        service.wake.set()

    try:
        while True:
            service.wake.clear()
            row = None
            try:
                rows = list(service.conversation_repository.ready())
                while rows:
                    source = min(
                        rows,
                        key=lambda item: (
                            service.last_served.get(item["owner"], 0),
                            item["created"],
                            item["id"],
                        ),
                    )
                    rows.remove(source)
                    row = dict(source)
                    if row["id"] in tasks:
                        continue
                    backend = json.loads(row["payload"]).get("backend", "codex")
                    maximum = (
                        service.config.get("services", {}).get(backend, {}).get("max_concurrent", 1)
                    )
                    if type(maximum) is not int or maximum < 1:
                        maximum = 1  # Dispatch validates the configured capacity.
                    conversation = conversation_key(service, row)
                    reason = (
                        "conversation"
                        if conversation in conversations
                        else ("provider_capacity" if lanes.get(backend, 0) >= maximum else None)
                    )
                    if reason is None:
                        try:
                            reason = service.write_ownership.acquire(
                                row["id"],
                                row["project"],
                                row.get("work_item"),
                                ownership_roots(service, row),
                            )
                        except (OSError, RuntimeError):
                            reasons.pop(row["id"], None)
                            service.write_ownership.release(row["id"])
                            await settle_running(
                                service, row["id"], "failed", {"error": "project_root_unavailable"}
                            )
                            # Settlement can yield to cancellation and work-item edits.
                            remaining = {candidate["id"] for candidate in rows}
                            rows = [
                                candidate
                                for candidate in service.conversation_repository.ready()
                                if candidate["id"] in remaining
                            ]
                            continue
                    if reason:
                        if reasons.get(row["id"]) != reason:
                            service.event(row["id"], "queue_wait", {"reason": reason})
                            reasons[row["id"]] = reason
                        continue
                    reasons.pop(row["id"], None)
                    with service.db:
                        service.conversation_repository.set_running(row["id"])
                    conversations[conversation] = row["id"]
                    lanes[backend] = lanes.get(backend, 0) + 1
                    service.dispatch_sequence += 1
                    service.last_served[row["owner"]] = service.dispatch_sequence
                    task = asyncio.create_task(run_job(service, row))
                    tasks[row["id"]] = task
                    service.job_tasks[row["id"]] = task
                    task.add_done_callback(
                        lambda completed, job=row["id"], lane=backend, key=conversation: released(
                            completed, job, lane, key
                        )
                    )
            except sqlite3.OperationalError:
                if row is not None and row["id"] not in tasks:
                    service.write_ownership.release(row["id"])
                logger.warning("Queue dispatch database unavailable; retrying", exc_info=True)
                await asyncio.sleep(0.05)
                continue
            await service.wake.wait()
    finally:
        service.stopping = True
        remaining = list(tasks.values())
        for task in remaining:
            task.cancel()
        await asyncio.gather(*remaining, return_exceptions=True)


async def run_job(service, row):
    row = dict(row)
    started = time.time()
    execution_completed = False
    budget = RuntimeBudget()
    service.runtime_budgets[row["id"]] = budget
    try:
        with service.db:
            service.conversation_repository.set_running(row["id"])
        service.event(row["id"], "running", {})
        request_data = json.loads(row["payload"])
        native_codex = (
            request_data.get("backend") == "codex"
            and request_data.get("execution_mode", service.configured_execution_mode("codex"))
            == "native"
        )
        maximum = (
            None
            if native_codex
            else timeout_seconds(
                service.config,
                "active_timeout_seconds",
                3600 if request_data.get("backend") == "maestro" else 600,
            )
        )
        async with budget.limit(maximum):
            task = asyncio.create_task(service.execute(row))
            service.job_tasks[row["id"]] = task
            result = await task
        execution_completed = True
        result.update(budget.metrics())
        result["queue_seconds"] = started - row["created"]
        result["total_seconds"] = time.time() - row["created"]
        await finish_when_available(service, row["id"], "completed", result)
        if result.get("deployment", {}).get("restart_required"):
            unit = service.config["projects"][row["project"]]["restart_service"]
            proc = await asyncio.create_subprocess_exec(
                "systemd-run",
                "--user",
                "--on-active=3s",
                "--collect",
                "--unit=local-agent-reload-" + row["id"],
                "systemctl",
                "--user",
                "restart",
                unit,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            if await proc.wait() == 0:
                service.event(row["id"], "reload_scheduled", {})
                await asyncio.Future()
            else:
                service.event(row["id"], "deployment_failed", {"error": "restart_schedule_failed"})
    except asyncio.CancelledError:
        if execution_completed:
            # Shutdown remains responsive. Startup recovery owns an uncommitted result;
            # completed inference must not be relabeled as a cancelled execution.
            return
        if service.conversation_repository.state(row["id"])[0] == "completed":
            raise
        reason = service.cancellation_reasons.pop(row["id"], None)
        await settle_running(
            service,
            row["id"],
            "cancelled",
            with_partial_answer(
                service,
                row["id"],
                {"partial_output": "persisted_events", "error": reason, "metrics": None},
            ),
        )
        service.panel(row["project"], answer="Execution cancelled", finished=True)
        if asyncio.current_task().cancelling():
            raise
    except Exception as exc:
        backend = json.loads(row["payload"]).get("backend")
        binary = service.config.get(backend, {}).get("binary")
        code = (
            exc.code
            if isinstance(exc, APIError)
            else "active_runtime_timeout"
            if isinstance(exc, TimeoutError)
            else str(exc)
            if isinstance(exc, tools.ToolError)
            # The provider CLI was removed or moved after the harness started.
            else "cli_missing"
            if isinstance(exc, FileNotFoundError) and binary and exc.filename == binary
            else type(exc).__name__
        )
        if not isinstance(exc, (APIError, tools.ToolError)):
            from agent_service.log_config import redact

            logger.error(
                "Job %s failed unexpectedly: %s", row["id"], redact(traceback.format_exc())
            )
        condition = provider_condition(code)
        if condition:
            await settle_running(
                service,
                row["id"],
                "interrupted",
                with_partial_answer(
                    service,
                    row["id"],
                    {
                        "condition": condition,
                        "backend": backend,
                        # A provider message (e.g. Codex's reset time), never a bare code.
                        "error_detail": getattr(
                            exc, "error_detail", code if ": " in code else None
                        ),
                        "metrics": None,
                    },
                ),
            )
            name = PROVIDER_NAMES.get(backend, backend)
            service.panel(
                row["project"], answer=CONDITION_ANSWERS[condition].format(name), finished=True
            )
        else:
            await settle_running(
                service,
                row["id"],
                "failed",
                with_partial_answer(
                    service,
                    row["id"],
                    {
                        "error": "context_limit_exceeded" if context_overflow(code) else code,
                        "error_detail": getattr(
                            exc, "error_detail", code if context_overflow(code) else None
                        ),
                        "metrics": None,
                    },
                ),
            )
            service.panel(row["project"], answer="Execution interrupted: " + code, finished=True)
    finally:
        try:
            # Not on shutdown (the worker itself is cancelled): the refresh can wait up to
            # 25 s on the Codex CLI and would hold the process open after SIGTERM.
            if (
                json.loads(row["payload"]).get("backend") == "codex"
                and not asyncio.current_task().cancelling()
            ):
                state = service.conversation_repository.state(row["id"])[0]
                if state != "completed":
                    service.event(row["id"], "quota_after", await service.quota(True))
        finally:
            service.active_executors.pop(row["id"], None)
            service.job_tasks.pop(row["id"], None)
            service.runtime_budgets.pop(row["id"], None)
            service.approval_expirations.pop(row["id"], None)


def cancel_owned(service, row):
    job = row["id"]
    task = service.job_tasks.get(job)
    if task is not None:
        task.cancel()
    elif row["state"] == "running" and service.active == job and service.task:
        service.task.cancel()
    if row["state"] == "queued" or (
        row["state"] == "running" and task is None and service.active != job
    ):
        try:
            service.finish(job, "cancelled", with_partial_answer(service, job, {"metrics": None}))
        except sqlite3.OperationalError as exc:
            if getattr(exc, "sqlite_errorcode", 0) & 0xFF not in (
                sqlite3.SQLITE_BUSY,
                sqlite3.SQLITE_LOCKED,
            ):
                raise
            raise APIError("cancellation_retry_required", 503, retry_after=1) from exc
    for pending_job, future in list(service.approvals.values()):
        if pending_job == job and not future.done():
            future.cancel()
    service.wake.set()
    return task


def cancel(service, identity, job):
    row = service.job(identity, job)
    cancel_owned(service, row)
    return {"job_id": job, "cancel_requested": row["state"] not in TERMINAL}
