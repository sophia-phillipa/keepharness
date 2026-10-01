"""Conversations, history, admission, jobs and their event streams, approvals."""

import asyncio
import hashlib
import json

from starlette.responses import JSONResponse, Response

from ..approval_sessions import require_approval_session
from ..config import TERMINAL
from ..errors import APIError
from ..persistence.db import encoded
from ..secret_vault import redact_secrets
from . import LimitedStream, api_route, body


async def approval(request, service, identity):
    require_approval_session(request, service.config, identity)
    aid = request.path_params["approval"]
    if service.gates.repository.get(aid):
        data = await body(request)
        require_approval_session(request, service.config, identity, revalidate=True)
        return JSONResponse(service.gates.resolve(aid, identity, data))
    pending = service.approvals.get(aid)
    if not pending:
        raise APIError("approval_expired", 404)
    row = service.job(identity, pending[0])
    if row["owner"] != identity[0]:
        raise APIError("approval_owner_denied", 403)
    data = await body(request)
    require_approval_session(request, service.config, identity, revalidate=True)
    service.job(identity, pending[0])
    if service.approvals.get(aid) is not pending or pending[1].cancelled():
        raise APIError("approval_expired", 404)
    scope = data.get("scope", "once")
    if scope not in ("once", "conversation"):
        raise APIError("invalid_approval_scope")
    if not pending[1].done():
        pending[1].set_result(
            {
                "approved": data.get("approved") is True,
                "answers": data.get("answers", {}),
                "scope": scope,
            }
        )
    return JSONResponse({"resolved": True})


async def approval_rules(request, service, identity):
    data = await body(request)
    cid = data.get("conversation_id")
    self_rows = service.conversation(identity, cid)
    if not self_rows:
        raise APIError("conversation_not_found", 404)
    with service.db:
        service.conversation_repository.clear_approval_rules(identity[0], cid)
    return JSONResponse({"cleared": True})


async def conversations(request, service, identity):
    groups = {}
    deleted = service.conversation_repository.deleted()
    titles = service.conversation_repository.titles()
    for r in service.conversation_rows(identity):
        cid = service.conversation_id(r)
        if cid in deleted:
            continue
        if cid not in groups:
            groups[cid] = {
                "id": cid,
                "project": r["project"],
                "title": titles.get(
                    cid, json.loads(r["payload"]).get("prompt", "Conversation")[:100]
                ),
            }
        groups[cid].update(
            last_job_id=r["id"],
            state=r["state"],
            updated=r["created"],
            execution=service.execution(r),
        )
    return JSONResponse(
        {"conversations": sorted(groups.values(), key=lambda c: c["updated"], reverse=True)}
    )


def gate_records(service, job_id):
    return redact_secrets(
        [
            {
                **json.loads(gate["spec"]),
                **(
                    service.effects.public(json.loads(gate["spec"])["effect_id"])
                    if json.loads(gate["spec"]).get("effect_id")
                    else {}
                ),
                "state": gate["state"],
                "choice": json.loads(gate["choice"]) if gate["choice"] else None,
                "resolved_by": gate["resolved_by"],
                "at": gate["resolved_at"],
            }
            for gate in service.gates.repository.for_job(job_id)
        ]
    )


async def conversation(request, service, identity):
    cid = request.path_params["conversation"]
    rows = service.conversation(identity, cid)
    if request.method == "PATCH":
        data = await body(request)
        service.conversation(identity, cid)
        title = data.get("title")
        if not isinstance(title, str) or not (title := title.strip()) or len(title) > 100:
            raise APIError("invalid_conversation_title")
        with service.db:
            service.conversation_repository.set_title(cid, title)
        return JSONResponse({"id": cid, "title": title})
    if request.method == "DELETE":
        if any(r["state"] not in TERMINAL for r in rows):
            raise APIError("conversation_busy", 409)
        with service.db:
            service.conversation_repository.mark_deleted(cid)
        return JSONResponse({"deleted": True, "retention": "hidden; execution records retained"})
    return JSONResponse(
        {
            "id": cid,
            "execution_mode": service.conversation_execution_mode(rows[-1]),
            "turns": [
                {
                    "id": r["id"],
                    "project": r["project"],
                    "state": r["state"],
                    "attachments": service.message_attachments(r),
                    "gates": gate_records(service, r["id"]),
                    "workflow_checkpoint": service.has_workflow_checkpoint(r),
                    "request": json.loads(r["payload"]),
                    "result": json.loads(r["result"] or "{}"),
                }
                for r in rows
            ],
        }
    )


async def history(request, service, identity):
    rows = service.conversation_repository.history(identity[0], identity[1]["projects"])
    return JSONResponse(
        {
            "jobs": [
                {
                    "id": r["id"],
                    "project": r["project"],
                    "state": r["state"],
                    "created": r["created"],
                    "title": json.loads(r["payload"]).get(
                        "prompt", json.loads(r["payload"]).get("kind", "Execution")
                    )[:100],
                }
                for r in rows
            ]
        }
    )


async def assess(request, service, identity):
    return JSONResponse(service.assess(identity, await body(request)))


async def submit_job(request, service, identity):
    return JSONResponse(
        service.submit(identity, await body(request), request.headers.get("idempotency-key")),
        status_code=202,
    )


async def recover_workflow(request, service, identity):
    return JSONResponse(
        service.recover_workflow(
            identity,
            request.path_params["job"],
            await body(request),
            rerun=request.url.path.endswith("/rerun"),
            idem=request.headers.get("idempotency-key"),
        ),
        status_code=202,
    )


async def save_workflow(request, service, identity):
    data = await body(request)
    return JSONResponse(
        service.save_workflow(identity, request.path_params["job"], data.get("id")), status_code=201
    )


async def job(request, service, identity):
    row = service.job(identity, request.path_params["job"])
    row["gates"] = gate_records(service, row["id"])
    row["workflow_checkpoint"] = service.has_workflow_checkpoint(row)
    row["attachments"] = service.message_attachments(row)
    public_request = json.loads(row["payload"])
    row["request"] = {
        k: public_request.get(k)
        for k in (
            "prompt",
            "backend",
            "model",
            "effort",
            "parent_job_id",
            "task_label",
            "invocations",
            "resource_selections",
            "release_persona",
            "kind",
            "access_mode",
            "execution_mode",
        )
    }
    return JSONResponse(
        {
            k: (json.loads(v) if v and k == "result" else v)
            for k, v in row.items()
            if k not in ("payload", "digest", "idem", "owner")
        }
    )


async def job_events(request, service, identity):
    job = request.path_params["job"]
    service.job(identity, job)
    try:
        after = int(request.headers.get("last-event-id", request.query_params.get("after", "0")))
    except ValueError:
        raise APIError("invalid_event_id")
    if after < 0:
        raise APIError("invalid_event_id")
    if request.query_params.get("format") == "json":
        try:
            limit = int(request.query_params.get("limit", "200"))
        except ValueError:
            raise APIError("invalid_event_limit")
        if not 1 <= limit <= 200:
            raise APIError("invalid_event_limit")
        rows = service.message_repository.events_after(job, after)[:limit]
        next_after = rows[-1]["id"] if rows else after
        has_more = bool(service.message_repository.events_after(job, next_after)) if rows else False
        return JSONResponse(
            {
                "events": [
                    {
                        "id": event["id"],
                        "job_id": job,
                        "timestamp": event["time"],
                        "type": event["type"],
                        "data": json.loads(event["data"]),
                    }
                    for event in rows
                ],
                "next_after": next_after,
                "has_more": has_more,
            },
            headers={"Cache-Control": "no-store"},
        )
    if service.streams.get(identity[0], 0) >= 4:
        raise APIError("stream_limit", 429, 5)
    service.streams[identity[0]] = service.streams.get(identity[0], 0) + 1

    async def events():
        cursor = after
        while True:
            rows = service.message_repository.events_after(job, cursor)
            for event in rows:
                cursor = event["id"]
                envelope = {
                    "id": cursor,
                    "job_id": job,
                    "timestamp": event["time"],
                    "type": event["type"],
                    "data": json.loads(event["data"]),
                }
                yield f"id: {cursor}\nevent: {event['type']}\ndata: {encoded(envelope)}\n\n"
            state = service.conversation_repository.state(job)[0]
            if state in TERMINAL and len(rows) < 200:
                break
            if await request.is_disconnected():
                break
            if not rows:
                yield ": heartbeat\n\n"
            await asyncio.sleep(0.25 if rows else 1)

    def release_stream():
        service.streams[identity[0]] -= 1

    return LimitedStream(
        events(),
        release=release_stream,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


async def cancel_job(request, service, identity):
    job = request.path_params["job"]
    service.job(identity, job)
    return JSONResponse(service.cancel(identity, job))


async def job_result(request, service, identity):
    row = service.job(identity, request.path_params["job"])
    if not row["result"]:
        raise APIError("result_not_ready", 409)
    result = row["result"].encode()
    return Response(
        result,
        media_type="application/json",
        headers={"X-Content-SHA256": hashlib.sha256(result).hexdigest()},
    )


ROUTES = [
    api_route("/v1/approvals/{approval}", approval, methods=["POST"]),
    api_route("/v1/approval-rules", approval_rules, methods=["POST"]),
    api_route("/v1/conversations", conversations),
    api_route("/v1/conversations/{conversation}", conversation, methods=["GET", "DELETE", "PATCH"]),
    api_route("/v1/history", history),
    api_route("/v1/assess", assess, methods=["POST"]),
    api_route("/v1/jobs", submit_job, methods=["POST"]),
    api_route("/v1/jobs/{job}", job),
    api_route("/v1/jobs/{job}/resume", recover_workflow, methods=["POST"]),
    api_route("/v1/jobs/{job}/rerun", recover_workflow, methods=["POST"]),
    api_route("/v1/jobs/{job}/save-workflow", save_workflow, methods=["POST"]),
    api_route("/v1/jobs/{job}/events", job_events),
    api_route("/v1/jobs/{job}/cancel", cancel_job, methods=["POST"]),
    api_route("/v1/jobs/{job}/artifacts/result.json", job_result),
]
