"""Conversations, history, admission, jobs and their event streams, approvals."""

import asyncio
import hashlib
import json
import time
import unicodedata

from starlette.responses import JSONResponse, Response

from ..approval_sessions import require_approval_session
from ..config import TERMINAL
from ..errors import APIError
from ..persistence.db import encoded
from ..resources import conversation_title
from ..secret_vault import redact_secrets
from ..services import retention
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
    service.job(identity, pending[0])
    data = await body(request)
    require_approval_session(request, service.config, identity, revalidate=True)
    service.job(identity, pending[0])
    if service.approvals.get(aid) is not pending or pending[1].cancelled():
        raise APIError("approval_expired", 404)
    scope = data.get("scope", "once")
    if scope not in ("once", "conversation"):
        raise APIError("invalid_approval_scope")
    if pending[1].done():
        raise APIError("approval_already_resolved", 409)
    if time.time() >= service.approval_deadlines.get(aid, float("inf")):
        raise APIError("approval_expired", 404)
    pending[1].set_result(
        {
            "approved": data.get("approved") is True,
            "answers": data.get("answers", {}),
            "scope": scope,
            "resolved_by": identity[0],
            "resolved_at": time.time(),
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


SEARCH_MIN = 2
# A search reads at most this many of the newest conversations and this much stored text.
SEARCH_MAX_CONVERSATIONS = 500
SEARCH_MAX_BYTES = 16 * 1024 * 1024
SEARCHES_PER_MINUTE = 60
SNIPPET_BEFORE, SNIPPET_AFTER = 40, 60


def fold(text):
    """Lower-cased text without accents, and where each folded character came from."""
    folded, origin = [], []
    for index, char in enumerate(text):
        for piece in unicodedata.normalize("NFD", char):
            if unicodedata.combining(piece):
                continue
            for lowered in piece.casefold():
                folded.append(lowered)
                origin.append(index)
    return "".join(folded), origin


def snippet(text, needle):
    """A short excerpt of ``text`` around the first accent- and case-insensitive match, or ''."""
    folded, origin = fold(text)
    at = folded.find(needle)
    if at < 0:
        return ""
    start = max(0, origin[at] - SNIPPET_BEFORE)
    end = min(len(text), origin[at + len(needle) - 1] + SNIPPET_AFTER)
    excerpt = " ".join(text[start:end].split())
    return ("…" if start else "") + excerpt + ("…" if end < len(text) else "")


def turn_text(row):
    """What a turn says: the prompt that was sent and the answer that came back."""
    texts = [json.loads(row["payload"]).get("prompt")]
    try:
        texts.append(json.loads(row["result"] or "null").get("answer"))
    except (AttributeError, ValueError):
        pass
    return [text for text in texts if isinstance(text, str)]


def search_snippets(candidates, needle):
    """The first snippet per conversation, newest first, until the byte budget runs out.

    ``candidates`` pairs a conversation id with its turn rows; the result says whether the
    budget stopped the scan early.
    """
    found, scanned = {}, 0
    for cid, rows in candidates:
        for row in rows:
            scanned += len(row["payload"]) + len(row["result"] or "")
            if scanned > SEARCH_MAX_BYTES:
                return found, True
            hit = next((hit for text in turn_text(row) if (hit := snippet(text, needle))), "")
            if hit:
                found[cid] = hit
                break
    return found, False


async def conversations(request, service, identity):
    """The owner's conversations; with ``?q=`` only those whose prompts or answers contain it."""
    if request.headers.get("x-keepharness-temporary"):
        return JSONResponse({"conversations": []})
    searching = "q" in request.query_params
    needle = fold(request.query_params.get("q", "").strip())[0]
    if searching:
        if len(needle) < SEARCH_MIN:
            raise APIError("search_query_too_short", 400)
        service.limit(
            (identity[0], "conversation_search"), SEARCHES_PER_MINUTE, "search_rate_limit"
        )
    groups, turns = {}, {}
    # ``?archived=true`` lists the Archived chats instead of the live ones.
    listing_archived = request.query_params.get("archived") == "true"
    archived = service.conversation_repository.archived()
    titles = service.conversation_repository.titles()
    for r in service.conversation_rows(identity):
        cid = service.conversation_id(r)
        if (cid in archived) != listing_archived:
            continue
        if cid not in groups:
            root = json.loads(r["payload"])
            groups[cid] = {
                "id": cid,
                "project": r["project"],
                "title": titles.get(cid) or conversation_title(root.get("prompt", "")),
                **{key: root[key] for key in ("schedule_id", "schedule_title") if key in root},
            }
        groups[cid].update(
            last_job_id=r["id"],
            state=r["state"],
            updated=r["created"],
            execution=service.execution(r),
        )
        turns.setdefault(cid, []).append(r)
    newest = sorted(groups.values(), key=lambda c: c["updated"], reverse=True)
    if not searching:
        return JSONResponse({"conversations": newest})
    candidates = [(c["id"], turns[c["id"]]) for c in newest[:SEARCH_MAX_CONVERSATIONS]]
    # Text matching runs off the event loop; the rows were read above, on it.
    snippets, limited = await asyncio.to_thread(search_snippets, candidates, needle)
    found = [{**c, "snippet": snippets[c["id"]]} for c in newest if c["id"] in snippets]
    response = {"conversations": found}
    if limited or len(newest) > SEARCH_MAX_CONVERSATIONS:
        response["limited"] = True
    return JSONResponse(response)


def gate_records(service, job_id):
    return redact_secrets(
        [
            {
                **json.loads(gate["public_spec"]),
                **(
                    service.effects.public(json.loads(gate["public_spec"])["effect_id"])
                    if json.loads(gate["public_spec"]).get("effect_id")
                    else {}
                ),
                "state": gate["state"],
                "resolved_by": gate["resolved_by"],
                "at": gate["resolved_at"],
            }
            for gate in service.gates.repository.for_job(job_id)
        ]
    )


async def conversation(request, service, identity):
    cid = request.path_params["conversation"]
    if request.method == "DELETE":
        return await purge_conversation(service, identity, cid)
    if request.method == "PATCH":
        data = await body(request)
        if "archived" in data:
            if type(data["archived"]) is not bool:
                raise APIError("invalid_archived")
            return JSONResponse(retention.set_archived(service, identity, cid, data["archived"]))
        service.conversation(identity, cid)
        title = data.get("title")
        if not isinstance(title, str) or not (title := title.strip()) or len(title) > 100:
            raise APIError("invalid_conversation_title")
        with service.db:
            service.conversation_repository.set_title(cid, title)
        return JSONResponse({"id": cid, "title": title})
    rows = service.conversation(identity, cid)
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
                    "workflow_completed_steps": service.workflow_completed_steps(r),
                    "request": json.loads(r["payload"]),
                    "result": json.loads(r["result"] or "{}"),
                }
                for r in rows
            ],
        }
    )


async def purge_conversation(service, identity, cid):
    """Delete permanently (decision D31): every turn's job routes answer 404 afterwards."""
    rows, files = retention.begin_purge(service, identity, cid)
    # Disk work runs off the event loop; the shared database connection stays on it.
    paths = await asyncio.to_thread(retention.purge_paths, service, cid, rows, files)
    await asyncio.to_thread(retention.remove_paths, paths)
    turns = retention.finish_purge(service, identity, cid, files)
    return JSONResponse({"id": cid, "deleted": True, "turns": turns})


async def storage(request, service, identity):
    project = request.query_params.get("project_id", "")
    return JSONResponse(retention.storage(service, identity, project))


async def history(request, service, identity):
    if request.headers.get("x-keepharness-temporary"):
        return JSONResponse({"jobs": []})
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


INCLUDE_PATHS = {"1": True, "true": True, "yes": True, "0": False, "false": False, "no": False}


async def continuation(request, service, identity):
    """A handoff text to paste into another assistant: ``?target=claude|chatgpt``."""
    include_paths = INCLUDE_PATHS.get(request.query_params.get("include_paths", "1").lower())
    if include_paths is None:
        raise APIError("invalid_include_paths", 400)
    return JSONResponse(
        service.continuation(
            identity,
            request.path_params["conversation"],
            request.query_params.get("target", ""),
            include_paths=include_paths,
        )
    )


async def assess(request, service, identity):
    return JSONResponse(service.assess(identity, await body(request)))


async def submit_job(request, service, identity):
    data = await body(request)
    return JSONResponse(
        await service.submit_async(identity, data, request.headers.get("idempotency-key")),
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


async def retry_turn(request, service, identity):
    return JSONResponse(
        await service.retry_turn(identity, request.path_params["job"]), status_code=202
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
    row["workflow_completed_steps"] = service.workflow_completed_steps(row)
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
            "retry_of",
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
        newest = request.query_params.get("order", "oldest") == "newest"
        before = request.query_params.get("before")
        try:
            before = int(before) if before is not None else None
        except ValueError:
            raise APIError("invalid_event_id")
        if before is not None and before < 0:
            raise APIError("invalid_event_id")
        if newest:
            rows = service.message_repository.events_before(job, before, after, limit + 1)
            has_more = len(rows) > limit
            rows = rows[:limit]
        else:
            rows = service.message_repository.events_after(job, after)[:limit]
            has_more = (
                bool(service.message_repository.events_after(job, rows[-1]["id"]))
                if rows
                else False
            )
        next_after = max((row["id"] for row in rows), default=after)
        next_before = min((row["id"] for row in rows), default=before)
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
                "next_before": next_before,
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
            if getattr(service, "temporary_closed", False):
                break
            try:
                service.job(service.identity(request, revalidate=True), job)
            except APIError:
                break
            rows = service.message_repository.events_after(job, cursor)
            for event in rows:
                try:
                    service.job(service.identity(request, revalidate=True), job)
                except APIError:
                    return
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


async def run_queued(request, service, identity):
    return JSONResponse(service.run_queued(identity, request.path_params["job"]))


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
    api_route("/v1/conversations/{conversation}/continuation", continuation),
    api_route("/v1/conversations/{conversation}", conversation, methods=["GET", "DELETE", "PATCH"]),
    api_route("/v1/history", history),
    api_route("/v1/storage", storage),
    api_route("/v1/assess", assess, methods=["POST"]),
    api_route("/v1/jobs", submit_job, methods=["POST"]),
    api_route("/v1/jobs/{job}", job),
    api_route("/v1/jobs/{job}/resume", recover_workflow, methods=["POST"]),
    api_route("/v1/jobs/{job}/rerun", recover_workflow, methods=["POST"]),
    api_route("/v1/jobs/{job}/retry", retry_turn, methods=["POST"]),
    api_route("/v1/jobs/{job}/save-workflow", save_workflow, methods=["POST"]),
    api_route("/v1/jobs/{job}/events", job_events),
    api_route("/v1/jobs/{job}/cancel", cancel_job, methods=["POST"]),
    api_route("/v1/jobs/{job}/run-queued", run_queued, methods=["POST"]),
    api_route("/v1/jobs/{job}/artifacts/result.json", job_result),
]
