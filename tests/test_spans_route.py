import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient
from test_workspaces import config

from agent_service.app import Service
from agent_service.routes.spans import ROUTES


@pytest.fixture
def span_client(tmp_path):
    service = Service(config(tmp_path))
    identity = ("a", service.config["clients"]["a"])
    submitted = service.submit(
        identity,
        {
            "project_id": "p",
            "backend": "codex",
            "model": "gpt-6-astra",
            "effort": "low",
            "prompt": "private prompt",
        },
    )
    job_id = submitted["job_id"]
    service.event(job_id, "running", {})
    service.event(
        job_id,
        "tool_start",
        {
            "tool": "Read",
            "tool_call_id": "call",
            "input": {"nested": {"secret": "private arguments"}},
        },
    )
    service.event(
        job_id,
        "tool_end",
        {
            "tool": "Read",
            "tool_call_id": "call",
            "result": {"nested": {"text": "private response"}},
            "status": "completed",
        },
    )
    app = Starlette(routes=ROUTES)
    app.state.service = service
    with TestClient(app, headers={"Authorization": "Bearer a"}) as client:
        yield client, service, job_id
    service.db.close()


def test_spans_default_redacts_recursively_and_owner_opt_in(span_client):
    client, _, job_id = span_client
    response = client.get(f"/v1/jobs/{job_id}/spans")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "private" not in response.text
    assert all("content" not in span for span in response.json()["spans"])
    response = client.get(f"/v1/jobs/{job_id}/spans?include_content=true")
    for content in ("private prompt", "private arguments", "private response"):
        assert content in response.text


def test_spans_owner_and_project_boundaries(span_client):
    client, service, job_id = span_client
    path = f"/v1/jobs/{job_id}/spans?include_content=true"
    assert client.get(path, headers={"Authorization": "Bearer b"}).status_code == 403
    service.config["clients"]["a"]["projects"] = []
    assert client.get(path).status_code == 403
    assert client.get("/v1/jobs/missing/spans").status_code == 404


def test_spans_reads_more_than_the_sse_page(span_client):
    client, service, job_id = span_client
    for index in range(250):
        service.event(job_id, "context_usage", {"input_tokens": index})
    service.finish(job_id, "completed", {"answer": "private final"})
    response = client.get(f"/v1/jobs/{job_id}/spans")
    assert response.json()["spans"][0]["status"] == "ok"
    assert client.get(f"/v1/jobs/{job_id}/spans?include_content=yes").status_code == 422


def test_json_event_log_paginates_without_truncation_or_foreign_reads(span_client):
    from agent_service.routes.conversations import ROUTES as conversation_routes

    client, service, job_id = span_client
    client.app.router.routes.extend(conversation_routes)
    for index in range(510):
        service.event(job_id, "context_usage", {"input_tokens": index})
    service.finish(job_id, "completed", {})
    cursor, collected = 0, []
    while True:
        response = client.get(f"/v1/jobs/{job_id}/events?format=json&after={cursor}&limit=100")
        assert response.status_code == 200
        page = response.json()
        collected.extend(page["events"])
        cursor = page["next_after"]
        if not page["has_more"]:
            break
    assert len(collected) == 515
    assert len({event["id"] for event in collected}) == 515
    assert (
        client.get(
            f"/v1/jobs/{job_id}/events?format=json", headers={"Authorization": "Bearer b"}
        ).status_code
        == 403
    )
    assert client.get(f"/v1/jobs/{job_id}/events?format=json&limit=999").status_code == 422


def test_third_turn_uses_conversation_root(span_client):
    client, service, root = span_client
    identity = ("a", service.config["clients"]["a"])
    service.finish(root, "completed", {})
    parent = root
    for _ in range(2):
        parent = service.submit(
            identity,
            {
                "project_id": "p",
                "backend": "codex",
                "model": "gpt-6-astra",
                "effort": "low",
                "prompt": "Continue",
                "parent_job_id": parent,
            },
        )["job_id"]
        service.finish(parent, "completed", {})
    spans = client.get(f"/v1/jobs/{parent}/spans").json()["spans"]
    assert spans[0]["attrs"]["gen_ai.conversation.id"] == root
