"""Owner-authorized, privacy-default event-derived execution spans."""

from starlette.responses import JSONResponse

from ..errors import APIError
from ..spans import events_to_spans
from . import api_route


async def job_spans(request, service, identity):
    job_id = request.path_params["job"]
    row = service.job(identity, job_id)
    row["conversation_id"] = service.conversation_id(row)
    include_content = request.query_params.get("include_content", "false")
    if include_content not in {"true", "false"}:
        raise APIError("invalid_include_content")
    spans = events_to_spans(row, service.message_repository.all_events(job_id))
    if include_content != "true":
        for span in spans:
            span.pop("content", None)
    return JSONResponse(
        {"schema_version": 1, "job_id": job_id, "spans": spans},
        headers={"Cache-Control": "no-store"},
    )


ROUTES = [api_route("/v1/jobs/{job}/spans", job_spans)]
