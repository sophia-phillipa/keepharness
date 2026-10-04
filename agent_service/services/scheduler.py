"""Unattended runs of scheduled tasks: a background loop that submits what is due.

The loop lives for as long as the app does (see ``agent_service/app.py``). Every
``TICK_SECONDS`` it first reads back how the last submitted runs ended (D15: a run that did not
complete counts towards pausing the schedule), then submits each due schedule once, through
``ConversationService.submit`` exactly as ``POST /v1/jobs`` would, and records the submit. A due
time is skipped while the schedule's previous run is still queued or running. Rules and storage
are in ``agent_service/schedules.py``.
"""

import asyncio
import json
import logging
import re

from .. import schedules
from ..config import TERMINAL
from ..errors import HarnessError

logger = logging.getLogger(__name__)

TICK_SECONDS = 30
# A busy harness: the run stays due and is tried again on the next tick. Any other refusal
# (job_storage_limit included) is a failed run that counts towards pausing the schedule.
TRANSIENT_CODES = frozenset(
    {"queue_full", "owner_queue_full", "submission_rate_limit", "work_item_check_busy"}
)
SAFE_CODE = re.compile(r"^[a-z0-9_]{1,64}$")
MISSING_CLIENT = "Paused because the client that owns this schedule no longer exists."


def run_key(record: dict) -> str:
    """One key per schedule and due time, so a run repeated after a crash is not submitted twice."""
    return f"schedule-{record['id']}-{record['next_run']}"


async def submit(service, identity: tuple, record: dict, idempotency_key: str | None = None) -> dict:
    """The path of ``POST /v1/jobs``: a fresh conversation marked as started by the schedule."""
    return await service.submit_async(
        identity,
        schedules.job_request(service.config, record),
        idempotency_key,
        schedule=schedules.origin(record),
    )


def submitted_before(service, record: dict, key: str) -> str | None:
    """The job this due time already started before a crash, though the prompt was edited since."""
    row = service.conversation_repository.by_idempotency_key(
        record["owner"], record["project_id"], key
    )
    return row["id"] if row else None


async def run(service) -> None:
    """Check for due schedules until cancelled; one failing check never ends the loop."""
    while True:
        await asyncio.sleep(TICK_SECONDS)
        try:
            await tick(service)
        except Exception:
            logger.exception("Scheduler tick failed")


def outcome(service, job_id: str) -> dict | None:
    """How run ``job_id`` ended, or ``None`` while it is active (or its conversation is gone)."""
    row = service.conversation_repository.get(job_id)
    if row is None or row["state"] not in TERMINAL:
        return None
    result = json.loads(row["result"] or "{}")
    error = result.get("error") or result.get("condition") or row["state"]
    return {
        "state": row["state"],
        "error": error if SAFE_CODE.fullmatch(str(error)) else row["state"],
        "needs_you": service.message_repository.has_unattended_denial(job_id),
    }


def active(service, record: dict) -> bool:
    job_id = (record["last_run"] or {}).get("job_id")
    row = service.conversation_repository.state(job_id) if job_id else None
    return row is not None and row[0] in ("queued", "running")


async def settle_outcomes(service) -> None:
    for record in await asyncio.to_thread(schedules.awaiting_outcome, service.config):
        try:
            ended = outcome(service, record["last_run"]["job_id"])
            if ended is not None:
                await asyncio.to_thread(schedules.record_outcome, service.config, record, **ended)
        except Exception:
            logger.exception("Could not record how the scheduled run of %s ended", record["id"])


async def tick(service, now: float | None = None) -> None:
    """Read back finished runs, then submit every schedule due at ``now``, at most once each."""
    now = schedules.clock() if now is None else now
    await settle_outcomes(service)
    for record in await asyncio.to_thread(schedules.due, service.config, now):
        try:
            await run_due(service, record, now)
        except Exception:
            logger.exception("Could not record the scheduled run of %s", record["id"])


async def run_due(service, record: dict, now: float) -> None:
    client = service.config.get("clients", {}).get(record["owner"])
    if client is None:
        await asyncio.to_thread(schedules.pause, service.config, record, MISSING_CLIENT)
        return
    if active(service, record):
        logger.info("Scheduled run of %s skipped: its previous run is still active", record["id"])
        await asyncio.to_thread(schedules.skip_due, service.config, record, now)
        return
    job_id = error = None
    key = run_key(record)
    try:
        job_id = (await submit(service, (record["owner"], client), record, key))["job_id"]
    except HarnessError as exc:
        if exc.code in TRANSIENT_CODES:
            logger.info("Scheduled run of %s deferred: %s", record["id"], exc.code)
            return
        job_id = submitted_before(service, record, key) if exc.code == "idempotency_conflict" else None
        if job_id is None:
            error = exc.code if SAFE_CODE.fullmatch(str(exc.code)) else "submit_failed"
            logger.warning("Scheduled run of %s was refused: %s", record["id"], error)
    except Exception:
        logger.exception("Scheduled run of %s failed", record["id"])
        error = "submit_failed"
    await asyncio.to_thread(
        schedules.finish_due, service.config, record, now, job_id=job_id, error=error
    )
