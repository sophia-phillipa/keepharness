"""Read-only operational dashboard; host metrics and execution observation."""

import json
import math
import sqlite3
import time
from pathlib import Path


def number(value):
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def metric(result):
    metrics = result.get("metrics") or {}
    if result.get("context_usage") and metrics.get("usage_scope") != "turn":
        metrics = {}  # Older CLI results contain session totals, not per-job consumption.
    orchestration = result.get("orchestration")
    if orchestration:
        # The top-level result repeats the final step: do not count it twice.
        parts = [orchestration.get("coordinator", {}), *orchestration.get("steps", [])]
        metrics = {
            key: sum(v for v in values if v is not None)
            if any(v is not None for v in values)
            else None
            for key in ("input_tokens", "output_tokens")
            for values in [[number((p.get("metrics") or {}).get(key)) for p in parts]]
        }
    incoming = number(metrics.get("input_tokens"))
    outgoing = number(metrics.get("output_tokens"))
    seconds = number(metrics.get("inference_seconds"))
    if orchestration:
        total = number(result.get("total_seconds"))
        queue = number(result.get("queue_seconds"))
        seconds = max(0, total - (queue or 0)) if total is not None else None
    # This is end-to-end execution throughput, not GPU decoder speed.
    rate = round(outgoing / seconds, 2) if outgoing is not None and seconds else None
    return incoming, outgoing, rate


def snapshot(state, now=None):
    now = now if now is not None else time.time()
    response = {
        "available": True,
        "checked_at": now,
        "window_hours": 24,
        "requests": 0,
        "active": 0,
        "queued": 0,
        "input_tokens": None,
        "output_tokens": None,
        "measured_jobs": 0,
        "latest_output_tokens_per_second": None,
        "rate_basis": "output_tokens / inference_seconds; includes tool and provider waits",
        "hourly_requests": [0] * 24,
        "recent": [],
        "recent_count": 0,
        "recent_limit": 100,
        "requests_per_second": 0,
        "hardware": hardware(),
    }
    path = (Path(state) / "runs/jobs.sqlite3").resolve()
    if not path.exists():
        return response
    try:
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0.25) as db:
            db.row_factory = sqlite3.Row
            for row in db.execute(
                "SELECT state,count(*) AS n FROM jobs WHERE state IN ('running','queued') GROUP BY state"
            ):
                response["active" if row["state"] == "running" else "queued"] = row["n"]
            response["requests_per_second"] = (
                db.execute(
                    "SELECT count(*) FROM jobs WHERE created>=? AND created<=?", (now - 60, now)
                ).fetchone()[0]
                / 60
            )
            rows = db.execute(
                "SELECT id,project,state,created,json_object('backend',json_extract(payload,'$.backend'),'model',json_extract(payload,'$.model')) AS payload,json_object('metrics',json_extract(result,'$.metrics'),'context_usage',json_extract(result,'$.context_usage'),'orchestration',json_extract(result,'$.orchestration'),'total_seconds',json_extract(result,'$.total_seconds'),'queue_seconds',json_extract(result,'$.queue_seconds')) AS result,(SELECT max(time) FROM events WHERE job=jobs.id AND type IN ('completed','failed','cancelled','interrupted')) AS ended FROM jobs WHERE created>=? OR state IN ('running','queued') OR EXISTS (SELECT 1 FROM events WHERE job=jobs.id AND type IN ('completed','failed','cancelled','interrupted') AND time>=?) ORDER BY created DESC",
                (now - 86400, now - 1800),
            )
            for row in rows:
                if row["created"] >= now - 86400:
                    response["requests"] += 1
                    slot = min(23, max(0, int((row["created"] - (now - 86400)) / 3600)))
                    response["hourly_requests"][slot] += 1
                try:
                    payload = json.loads(row["payload"] or "{}")
                    result = json.loads(row["result"] or "{}")
                    if not isinstance(payload, dict) or not isinstance(result, dict):
                        continue
                    incoming, outgoing, rate = metric(result)
                except (ValueError, TypeError, AttributeError):
                    continue
                if incoming is not None and row["created"] >= now - 86400:
                    response["input_tokens"] = (response["input_tokens"] or 0) + incoming
                if outgoing is not None and row["created"] >= now - 86400:
                    response["output_tokens"] = (response["output_tokens"] or 0) + outgoing
                if (incoming is not None or outgoing is not None) and row["created"] >= now - 86400:
                    response["measured_jobs"] += 1
                if response["latest_output_tokens_per_second"] is None and rate is not None:
                    response["latest_output_tokens_per_second"] = rate
                if (
                    row["state"] in ("running", "queued")
                    or row["ended"] is None
                    or row["ended"] >= now - 1800
                ):
                    response["recent_count"] += 1
                    if len(response["recent"]) >= response["recent_limit"]:
                        continue
                    response["recent"].append(
                        {
                            "id": row["id"],
                            "project": row["project"],
                            "state": row["state"],
                            "created": row["created"],
                            "ended": row["ended"],
                            "backend": payload.get("backend"),
                            "model": payload.get("model"),
                            "input_tokens": incoming,
                            "output_tokens": outgoing,
                            "output_tokens_per_second": rate,
                        }
                    )
    except sqlite3.Error:
        return {**response, "available": False, "reason": "dashboard_temporarily_unavailable"}
    return response


_cpu_previous = None


def hardware():
    """Read host counters only; unsupported sensors remain unavailable."""
    global _cpu_previous
    result = {"cpu_percent": None, "memory_used": None, "memory_total": None, "gpus": []}
    try:
        values = [int(v) for v in Path("/proc/stat").read_text().splitlines()[0].split()[1:9]]
        total = sum(values)
        idle = values[3] + values[4]
        if _cpu_previous and total > _cpu_previous[0]:
            result["cpu_percent"] = round(
                100 * (1 - (idle - _cpu_previous[1]) / (total - _cpu_previous[0])), 1
            )
        _cpu_previous = (total, idle)
        mem = {
            line.split(":")[0]: int(line.split()[1]) * 1024
            for line in Path("/proc/meminfo").read_text().splitlines()
        }
        result.update(
            memory_total=mem["MemTotal"], memory_used=mem["MemTotal"] - mem["MemAvailable"]
        )
    except (OSError, ValueError, KeyError, IndexError):
        pass
    for device in sorted(Path("/sys/class/drm").glob("card[0-9]*/device")):

        def read(path, scale=1):
            try:
                return round(int(path.read_text()) / scale, 1)
            except (OSError, ValueError):
                return None

        busy = read(device / "gpu_busy_percent")
        vram = read(device / "mem_info_vram_used")
        if busy is None and vram is None:
            continue
        temp = next(iter(device.glob("hwmon/hwmon*/temp1_input")), None)
        result["gpus"].append(
            {
                "name": device.parent.name,
                "percent": busy,
                "vram_used": vram,
                "vram_total": read(device / "mem_info_vram_total"),
                "temperature": read(temp, 1000) if temp else None,
            }
        )
    return result


def _execution(state, job):
    """Administrative observation only: no mutation or session control."""
    path = (Path(state) / "runs/jobs.sqlite3").resolve()
    if not path.exists():
        return None
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0.25) as db:
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT payload,result FROM jobs WHERE id=?", (job,)).fetchone()
        if not row:
            return None
        payload = json.loads(row["payload"] or "{}")
        result = json.loads(row["result"] or "{}")
        if not isinstance(payload, dict) or not isinstance(result, dict):
            raise ValueError("invalid_execution_record")
        events = [
            {"time": r["time"], "type": r["type"], "data": json.loads(r["data"])}
            for r in db.execute(
                "SELECT time,type,data FROM events WHERE job=? ORDER BY id DESC LIMIT 500", (job,)
            )
        ][::-1]
        return {
            "prompt": payload.get("prompt", ""),
            "answer": result.get("answer", ""),
            "metrics": result.get("metrics"),
            "orchestration": result.get("orchestration"),
            "events": events,
            "event_limit": 500,
        }


class DashboardReader:
    """Share a short-lived snapshot across viewers without blocking the event loop."""

    def __init__(self, state):
        self.state = state
        self.task = None
        self.cached = None
        self.cached_at = 0

    async def read(self):
        import asyncio

        if self.cached is not None and time.monotonic() - self.cached_at < 2:
            return self.cached
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(asyncio.to_thread(snapshot, self.state))
        value = await asyncio.shield(self.task)
        self.cached = value
        self.cached_at = time.monotonic()
        return value


def execution(state, job):
    try:
        return _execution(state, job)
    except (sqlite3.Error, OSError, ValueError, TypeError):
        return {"available": False, "reason": "execution_temporarily_unavailable"}
