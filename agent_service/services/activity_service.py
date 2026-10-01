"""Owner-scoped run summaries using durable jobs and live human waiters."""

import json
import math
import time

from ..errors import APIError
from ..spans import queue_wait_reason
from ..work_items import validate_reference


def summarize_activity(service, identity, project_id=None, work_item=None):
    if work_item is not None:
        validate_reference(work_item)
        if project_id is None:
            raise APIError("work_item_project_required")
    if project_id is not None:
        service.project(identity, project_id)
        projects = [project_id]
    else:
        projects = [p for p in identity[1]["projects"] if p in service.config["projects"]]
    rows = service.conversation_repository.activity(identity[0], projects, work_item)
    prepared_jobs = {
        row["job_id"]
        for row in service.db.execute(
            "SELECT DISTINCT effects.job_id FROM effects JOIN jobs ON jobs.id=effects.job_id "
            "WHERE effects.status='prepared' AND jobs.owner=?",
            (identity[0],),
        )
    }
    titles = service.conversation_repository.titles()
    jobs, needs_you = [], []
    providers = {}
    for backend, settings in service.config.get("services", {}).items():
        if settings.get("enabled") and set(projects).intersection(settings.get("projects", [])):
            for model in settings.get("models", []):
                providers[(backend, model)] = dict(
                    backend=backend, model=model, running=0, queued=0
                )
    now = time.time()
    seen_requests = set()
    for row in rows:
        data = json.loads(row["payload"])
        conversation_id = service.conversation_id(row)
        root = service.conversation_repository.get(conversation_id) or row
        title = titles.get(
            conversation_id, json.loads(root["payload"]).get("prompt", "Conversation")[:100]
        )
        job = dict(
            title=title,
            job_id=row["id"],
            conversation_id=service.conversation_id(row),
            project_id=row["project"],
            work_item=row["work_item"],
            state=row["state"],
            created=row["created"],
            backend=data.get("backend"),
            model=data.get("model"),
            wait_reason=None,
        )
        if row["state"] == "queued":
            previous = service.conversation_repository.get(data.get("parent_job_id"))
            wait = service.db.execute(
                "SELECT data FROM events WHERE job=? AND type='queue_wait' ORDER BY id DESC LIMIT 1",
                (row["id"],),
            ).fetchone()
            reason = queue_wait_reason(json.loads(wait["data"]).get("reason") if wait else None)
            job["wait_reason"] = (
                "conversation_parent"
                if previous and previous["state"] in ("queued", "running")
                else reason
            )
        # Prepared publications remain actionable after their provider turn ends.
        if row["state"] == "running" or row["id"] in prepared_jobs:
            for event in service.message_repository.requests(row["id"]):
                spec = json.loads(event["data"])
                is_gate = event["type"] == "gate_required"
                identifier = spec.get("gate_id" if is_gate else "approval_id")
                if identifier in seen_requests:
                    continue
                seen_requests.add(identifier)
                pending = service.approvals.get(identifier)
                deadline = spec.get("timeout_at" if is_gate else "expires_at")
                if (
                    not pending
                    or pending[0] != row["id"]
                    or pending[1].done()
                    or not isinstance(deadline, (int, float))
                    or not math.isfinite(deadline)
                    or deadline <= now
                ):
                    continue
                if is_gate:
                    gate = service.gates.repository.get(identifier)
                    if not gate or gate["state"] != "pending" or gate["job_id"] != row["id"]:
                        continue
                needs_you.append(
                    {
                        **spec,
                        **job,
                        "approval_kind": spec.get("kind"),
                        "kind": "gate" if is_gate else "approval",
                    }
                )
                job["wait_reason"] = "human_approval"
            active = service.active_executors.get(row["id"])
            if active:
                job["backend"], job["model"] = active
        if row["state"] in ("running", "queued"):
            key = (job["backend"], job["model"])
            provider = providers.setdefault(
                key, dict(backend=key[0], model=key[1], running=0, queued=0)
            )
            provider[row["state"]] += 1
        jobs.append(job)
    for provider in providers.values():
        provider["state"] = (
            "busy" if provider["running"] else "queued" if provider["queued"] else "idle"
        )
        if provider["backend"] == "claude":
            provider["quota"] = service.observed_claude_quota(identity[0])
        elif provider["backend"] == "codex":
            provider["quota"] = service.usage_cache
        else:
            provider["quota"] = None
    return dict(
        project_id=project_id,
        work_item=work_item,
        counts=dict(
            running=sum(j["state"] == "running" for j in jobs),
            queued=sum(j["state"] == "queued" for j in jobs),
            needs_you=len(needs_you),
        ),
        jobs=jobs,
        needs_you=needs_you,
        providers=list(providers.values()),
    )
