"""The single-worker job queue: fair dispatch, execution deadlines and cancellation.

Queue state (``active``, ``task``, ``wake``, ``last_served``) stays on the service,
following the ``maestro.run(service, ...)`` pattern.
"""

import asyncio
import json
import logging
import re
import time

from .. import tools
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
        json.loads(row["data"]).get("text", "")
        for row in service.message_repository.answer_deltas(job)
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


async def run(service):
    while True:
        row = service.next_job()
        if not row:
            service.wake.clear()
            await service.wake.wait()
            continue
        row = dict(row)
        service.active = row["id"]
        started = time.time()
        with service.db:
            service.conversation_repository.set_running(row["id"])
        service.event(row["id"], "running", {})
        budget = RuntimeBudget()
        service.runtime_budgets[row["id"]] = budget
        try:
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
                service.task = asyncio.create_task(service.execute(row))
                service.job_tasks[row["id"]] = service.task
                result = await service.task
            result.update(budget.metrics())
            result["queue_seconds"] = started - row["created"]
            result["total_seconds"] = time.time() - row["created"]
            service.finish(row["id"], "completed", result)
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
                    service.event(
                        row["id"], "deployment_failed", {"error": "restart_schedule_failed"}
                    )
        except asyncio.CancelledError:
            if service.conversation_repository.state(row["id"])[0] == "completed":
                raise
            reason = service.cancellation_reasons.pop(row["id"], None)
            settle(
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
                logger.exception("Job %s failed unexpectedly", row["id"])
            condition = provider_condition(code)
            if condition:
                settle(
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
                settle(
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
                service.panel(
                    row["project"], answer="Execution interrupted: " + code, finished=True
                )
        finally:
            # Not on shutdown (the worker itself is cancelled): the refresh can wait up to
            # 25 s on the Codex CLI and would hold the process open after SIGTERM.
            if (
                json.loads(row["payload"]).get("backend") == "codex"
                and not asyncio.current_task().cancelling()
            ):
                state = service.conversation_repository.state(row["id"])[0]
                if state != "completed":
                    service.event(row["id"], "quota_after", await service.quota(True))
            service.active = None
            service.task = None
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
    if row["state"] == "queued" or (row["state"] == "running" and service.active != job):
        settle(service, job, "cancelled", with_partial_answer(service, job, {"metrics": None}))
    for pending_job, future in list(service.approvals.values()):
        if pending_job == job and not future.done():
            future.cancel()
    return task


def cancel(service, identity, job):
    row = service.job(identity, job)
    cancel_owned(service, row)
    return {"job_id": job, "cancel_requested": row["state"] not in TERMINAL}
