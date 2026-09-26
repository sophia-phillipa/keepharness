"""The single-worker job queue: fair dispatch, execution deadlines and cancellation.

Queue state (``active``, ``task``, ``wake``, ``last_served``) stays on the service,
following the ``maestro.run(service, ...)`` pattern.
"""

import asyncio
import json
import logging
import time

from .. import tools
from ..config import TERMINAL
from ..conversation_context import context_overflow
from ..errors import APIError

logger = logging.getLogger(__name__)


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
        service.task = asyncio.create_task(service.execute(row))
        try:
            request_data = json.loads(row["payload"])
            native_codex = (
                request_data.get("backend") == "codex"
                and request_data.get("execution_mode", service.configured_execution_mode("codex"))
                == "native"
            )
            async with asyncio.timeout(
                None if native_codex else 3600 if request_data.get("backend") == "maestro" else 600
            ):
                result = await service.task
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
            service.finish(
                row["id"],
                "cancelled",
                {"partial_output": "persisted_events", "error": reason, "metrics": None},
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
                else str(exc)
                if isinstance(exc, tools.ToolError)
                # The provider CLI was removed or moved after the harness started.
                else "cli_missing"
                if isinstance(exc, FileNotFoundError) and binary and exc.filename == binary
                else type(exc).__name__
            )
            if not isinstance(exc, (APIError, tools.ToolError)):
                logger.exception("Job %s failed unexpectedly", row["id"])
            condition = {
                "claude_authentication_failed": (
                    "claude_authentication_required",
                    "Renew access to Claude in the admin panel.",
                ),
                "claude_rate_limit": (
                    "claude_quota_exhausted",
                    "Wait for the Claude quota to renew, or select another provider.",
                ),
            }.get(code)
            if condition:
                service.finish(
                    row["id"], "interrupted", {"condition": condition[0], "metrics": None}
                )
                service.panel(row["project"], answer=condition[1], finished=True)
            else:
                service.finish(
                    row["id"],
                    "failed",
                    {
                        "error": "context_limit_exceeded" if context_overflow(code) else code,
                        "error_detail": code if context_overflow(code) else None,
                        "metrics": None,
                    },
                )
                service.panel(
                    row["project"], answer="Execution interrupted: " + code, finished=True
                )
        finally:
            if json.loads(row["payload"]).get("backend") == "codex":
                state = service.conversation_repository.state(row["id"])[0]
                if state != "completed":
                    service.event(row["id"], "quota_after", await service.quota(True))
            service.active = None
            service.task = None
            service.active_executors.pop(row["id"], None)


def cancel(service, identity, job):
    row = service.job(identity, job)
    if row["state"] == "queued":
        service.finish(job, "cancelled", {"metrics": None})
    elif row["state"] == "running" and service.active == job and service.task:
        service.task.cancel()
    return {"job_id": job, "cancel_requested": row["state"] not in TERMINAL}
