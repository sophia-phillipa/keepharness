"""Unattended runs of scheduled tasks: a background loop that submits what is due.

The loop lives for as long as the app does (see ``agent_service/app.py``). Every
``TICK_SECONDS`` it submits each due schedule once, through ``ConversationService.submit``
exactly as ``POST /v1/jobs`` would, and records the outcome. Rules and storage are in
``agent_service/schedules.py``.
"""

import asyncio
import logging
import re

from .. import schedules
from ..errors import HarnessError

logger = logging.getLogger(__name__)

TICK_SECONDS = 30
BUSY_STATUS = 429
SAFE_CODE = re.compile(r"^[a-z0-9_]{1,64}$")
MISSING_CLIENT = "Paused because the client that owns this schedule no longer exists."


def run_key(record: dict) -> str:
    """One key per schedule and due time, so a run repeated after a crash is not submitted twice."""
    return f"schedule-{record['id']}-{record['next_run']}"


def submit(service, identity: tuple, record: dict, idempotency_key: str | None = None) -> dict:
    """The path of ``POST /v1/jobs``: a fresh conversation marked as started by the schedule."""
    return service.submit(
        identity, schedules.job_request(record), idempotency_key, schedule=schedules.origin(record)
    )


async def run(service) -> None:
    """Check for due schedules until cancelled; one failing check never ends the loop."""
    while True:
        await asyncio.sleep(TICK_SECONDS)
        try:
            await tick(service)
        except Exception:
            logger.exception("Scheduler tick failed")


async def tick(service, now: float | None = None) -> None:
    """Submit every schedule that is due at ``now``, at most once each."""
    now = schedules.clock() if now is None else now
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
    job_id = error = None
    try:
        job_id = submit(service, (record["owner"], client), record, run_key(record))["job_id"]
    except HarnessError as exc:
        if exc.status == BUSY_STATUS:
            logger.info("Scheduled run of %s deferred: %s", record["id"], exc.code)
            return
        error = exc.code if SAFE_CODE.fullmatch(str(exc.code)) else "submit_failed"
        logger.warning("Scheduled run of %s was refused: %s", record["id"], error)
    except Exception:
        logger.exception("Scheduled run of %s failed", record["id"])
        error = "submit_failed"
    await asyncio.to_thread(
        schedules.finish_due, service.config, record, now, job_id=job_id, error=error
    )
