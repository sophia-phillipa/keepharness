"""Characterization tests for every route served by ``agent_service.app.create_app``
and ``control.server.create_app`` (module-split commit 0, dossier design
``module-split.md`` section 2).

The route tables below were derived by reading the ``endpoint`` closures directly
(``agent_service/app.py``, ``control/server.py``) rather than copied blindly from the
design doc's table: where the two disagreed, the table here was corrected to match
the code, per the design's own rule ("if a line diverges, fix the table, not the
code"). These tests must pass unmodified today; the module-split refactor uses them
to confirm route-for-route behavior is preserved.

Heavy-setup cases (a real multipart/streaming upload, a fully configured executor)
are marked with a comment and only the cheap admission-time contract is asserted.
"""

import asyncio
import hashlib
import re
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from starlette.testclient import TestClient

from agent_service.app import create_app
from control.server import ADMIN_BODY_LIMIT, ADMIN_OPERATION_LIMIT
from control.server import create_app as create_admin_app
from tests.owner_session import sign_in

REQUEST_ID = re.compile(r"[0-9a-f]{32}")


def assert_api_error(response, status, code):
    """Shared error shape for APIError/ToolError responses (design section 2, line 39)."""
    assert response.status_code == status, response.text
    payload = response.json()
    assert payload["code"] == code
    assert payload["message"] == code
    assert payload["retryable"] == (status in (429, 503))
    assert REQUEST_ID.fullmatch(payload["request_id"])


# ---------------------------------------------------------------------------
# Agent app (agent_service.app.create_app): /v1/* + capabilities route table.
# Fixture is the shared `client` fixture from conftest.py: Bearer token "a",
# project "p", client "a" scoped to "p", origins=[], services={} (no executor
# configured, so write routes hit their cheapest admission-time rejection).
# ---------------------------------------------------------------------------

AGENT_VALID_TABLE = [
    # (id, method, path, query, json_body, status, code_or_checker)
    ("history", "GET", "/v1/history", None, None, 200, lambda r: r.json() == {"jobs": []}),
    (
        "conversations-list",
        "GET",
        "/v1/conversations",
        None,
        None,
        200,
        lambda r: r.json() == {"conversations": []},
    ),
    (
        "capabilities",
        "GET",
        "/.well-known/agent-capabilities.json",
        None,
        None,
        200,
        lambda r: "ETag" in r.headers and isinstance(r.json(), dict),
    ),
    (
        "version",
        "GET",
        "/v1/version",
        None,
        None,
        200,
        lambda r: (
            {"version", "build", "config_revision", "config_reload_error"} <= set(r.json())
            and r.json()["config_revision"] is None
            and r.json()["config_reload_error"] is None
        ),
    ),
    (
        "models",
        "GET",
        "/v1/models",
        None,
        None,
        200,
        lambda r: (
            r.json()
            == {
                "models": [],
                "project_id": "p",
                "providers": {},
                "uploads_enabled": False,
                "full_access": False,
                "local_owner": False,
                "admin_url": None,
            }
        ),
    ),
    (
        "usage-gemini",
        "GET",
        "/v1/usage",
        {"backend": "gemini"},
        None,
        200,
        lambda r: (
            r.json() == {"provider": "gemini", "available": False, "reason": "quota_not_reported"}
        ),
    ),
    (
        "usage-default",
        "GET",
        "/v1/usage",
        None,
        None,
        200,
        lambda r: r.json().get("reason") == "usage_unavailable",
    ),
    (
        "catalog-with-project",
        "GET",
        "/v1/catalog",
        {"project_id": "p"},
        None,
        200,
        lambda r: (
            r.json()["agents"] == [] and r.json()["skills"] == [] and r.json()["warnings"] == []
        ),
    ),
    ("catalog-no-project", "GET", "/v1/catalog", None, None, 403, "project_denied"),
    (
        "resources",
        "GET",
        "/v1/resources",
        {"project_id": "p", "backend": "codex", "model": "m"},
        None,
        403,
        "service_project_denied",
    ),
    (
        "integrations",
        "GET",
        "/v1/integrations",
        {"project_id": "p", "backend": "codex", "model": "m"},
        None,
        403,
        "service_project_denied",
    ),
    (
        "projects-get",
        "GET",
        "/v1/projects",
        None,
        None,
        200,
        lambda r: (
            r.json()["projects"] == ["p"]
            and set(r.json()["details"]["p"])
            >= {"label", "root", "additional_roots", "apply_changes", "canonical_id", "icon"}
        ),
    ),
    (
        "harness-agents-list",
        "GET",
        "/v1/harness-agents",
        None,
        None,
        200,
        lambda r: r.json() == {"agents": []},
    ),
    # Client "a" is authenticated but not the local browser, so every write is refused first.
    (
        "harness-agents-post",
        "POST",
        "/v1/harness-agents",
        None,
        {},
        403,
        "harness_agent_local_only",
    ),
    (
        "harness-agents-put",
        "PUT",
        "/v1/harness-agents/ghost-agent",
        None,
        {},
        403,
        "harness_agent_local_only",
    ),
    (
        "harness-agents-delete",
        "DELETE",
        "/v1/harness-agents/ghost-agent",
        None,
        {"revision": "r"},
        403,
        "harness_agent_local_only",
    ),
    (
        "ui-state-get",
        "GET",
        "/v1/ui-state",
        None,
        None,
        403,
        "ui_state_local_only",
    ),
    (
        "ui-state-patch",
        "PATCH",
        "/v1/ui-state",
        None,
        {"values": {}},
        403,
        "ui_state_local_only",
    ),
    (
        "pages-list",
        "GET",
        "/v1/pages",
        {"project_id": "p"},
        None,
        200,
        lambda r: r.json() == {"pages": []},
    ),
    ("pages-list-no-project", "GET", "/v1/pages", None, None, 422, "page_invalid"),
    (
        "pages-list-other-project",
        "GET",
        "/v1/pages",
        {"project_id": "x"},
        None,
        403,
        "project_denied",
    ),
    (
        "pages-post",
        "POST",
        "/v1/pages",
        None,
        {"project_id": "p", "title": "Plan", "body": "# Plan"},
        201,
        lambda r: (
            r.json()["title"] == "Plan"
            and len(r.json()["id"]) == 32
            and r.headers["Cache-Control"] == "no-store"
        ),
    ),
    ("pages-post-invalid", "POST", "/v1/pages", None, {"project_id": "p"}, 422, "page_invalid"),
    ("pages-get", "GET", "/v1/pages/" + "0" * 32, {"project_id": "p"}, None, 404, "page_not_found"),
    (
        "pages-put",
        "PUT",
        "/v1/pages/" + "0" * 32,
        None,
        {"project_id": "p", "title": "Plan", "body": "", "revision": "r"},
        404,
        "page_not_found",
    ),
    (
        "pages-delete",
        "DELETE",
        "/v1/pages/" + "0" * 32,
        None,
        {"project_id": "p", "revision": "r"},
        404,
        "page_not_found",
    ),
    (
        "schedules-list",
        "GET",
        "/v1/schedules",
        None,
        None,
        200,
        lambda r: r.json() == {"schedules": []} and r.headers["Cache-Control"] == "no-store",
    ),
    ("schedules-post-invalid", "POST", "/v1/schedules", None, {}, 422, "schedule_invalid"),
    (
        "schedules-post-other-project",
        "POST",
        "/v1/schedules",
        None,
        {"project_id": "x"},
        403,
        "project_denied",
    ),
    (
        "schedules-post-no-provider",
        "POST",
        "/v1/schedules",
        None,
        {
            "title": "Digest",
            "prompt": "Summarize.",
            "project_id": "p",
            "backend": "codex",
            "model": "m",
            "effort": "low",
            "cadence": {"kind": "daily", "time": "09:00"},
        },
        422,
        "schedule_invalid",
    ),
    (
        "schedules-put",
        "PUT",
        "/v1/schedules/" + "0" * 32,
        None,
        {"revision": "r", "title": "Digest"},
        404,
        "schedule_not_found",
    ),
    (
        "schedules-delete",
        "DELETE",
        "/v1/schedules/" + "0" * 32,
        None,
        {"revision": "r"},
        404,
        "schedule_not_found",
    ),
    (
        "schedules-run",
        "POST",
        "/v1/schedules/" + "0" * 32 + "/run",
        None,
        None,
        404,
        "schedule_not_found",
    ),
    # Client "a" is not the local owner: project folders are refused before anything else.
    ("projects-post", "POST", "/v1/projects", None, {}, 403, "project_management_local_only"),
    ("projects-patch", "PATCH", "/v1/projects", None, {}, 403, "project_management_local_only"),
    (
        "project-folder-get",
        "GET",
        "/v1/project-folder",
        {"project_id": "p"},
        None,
        422,
        "project_directory_required",
    ),
    (
        "project-folder-delete",
        "DELETE",
        "/v1/project-folder",
        {"project_id": "p"},
        {},
        403,
        "project_management_local_only",
    ),
    (
        "project-git",
        "GET",
        "/v1/project-git",
        {"project_id": "p"},
        None,
        200,
        lambda r: r.json() == {"revision": None},
    ),
    (
        "project-directories",
        "GET",
        "/v1/project-directories",
        None,
        None,
        403,
        "project_management_local_only",
    ),
    (
        "services",
        "POST",
        "/v1/services",
        None,
        {"project_id": "p"},
        403,
        "service_control_denied",
    ),
    (
        "workspaces-get",
        "GET",
        "/v1/workspaces",
        None,
        None,
        200,
        lambda r: r.json() == {"workspaces": []},
    ),
    # Heavy setup (real multipart upload) skipped: uploads_denied is checked
    # before any streaming, so the cheap admission-time contract still holds.
    (
        "workspaces-post",
        "POST",
        "/v1/workspaces",
        {"project_id": "p"},
        None,
        403,
        "uploads_denied",
    ),
    (
        "workspace-get",
        "GET",
        "/v1/workspaces/x",
        None,
        None,
        404,
        "workspace_not_found",
    ),
    (
        "workspace-download",
        "GET",
        "/v1/workspaces/x/download",
        None,
        None,
        404,
        "workspace_not_found",
    ),
    (
        "project-files-get",
        "GET",
        "/v1/project-files",
        {"project_id": "p"},
        None,
        403,
        "read_denied",
    ),
    (
        "project-files-attach",
        "POST",
        "/v1/project-files/attach",
        {"project_id": "p"},
        {},
        403,
        "read_denied",
    ),
    # Heavy setup (real streaming upload with x-filename) skipped: uploads_denied
    # is checked before the filename/stream is read, so this is the cheap contract.
    ("files-post", "POST", "/v1/files", {"project_id": "p"}, None, 403, "uploads_denied"),
    (
        "file-preview",
        "GET",
        "/v1/files/x/preview",
        None,
        None,
        404,
        "file_not_found",
    ),
    (
        "assess-with-project",
        "POST",
        "/v1/assess",
        None,
        {"project_id": "p"},
        422,
        "no_enabled_executor_for_task",
    ),
    ("assess-no-project", "POST", "/v1/assess", None, {}, 403, "project_denied"),
    # Heavy setup (a fully configured executor) skipped: with services={} the
    # cheap admission-time rejection is the only reachable outcome.
    ("jobs-post", "POST", "/v1/jobs", None, {}, 422, "no_enabled_executor_for_task"),
    (
        "conversation-get",
        "GET",
        "/v1/conversations/x",
        None,
        None,
        404,
        "job_not_found",
    ),
    (
        "conversation-patch",
        "PATCH",
        "/v1/conversations/x",
        None,
        {"title": "t"},
        404,
        "job_not_found",
    ),
    (
        "conversation-delete",
        "DELETE",
        "/v1/conversations/x",
        None,
        None,
        404,
        "job_not_found",
    ),
    (
        "approval-rules",
        "POST",
        "/v1/approval-rules",
        None,
        {},
        404,
        "job_not_found",
    ),
    ("approval", "POST", "/v1/approvals/x", None, {}, 403, "approval_session_required"),
    ("job-get", "GET", "/v1/jobs/x", None, None, 404, "job_not_found"),
    ("job-events", "GET", "/v1/jobs/x/events", None, None, 404, "job_not_found"),
    ("job-cancel", "POST", "/v1/jobs/x/cancel", None, {}, 404, "job_not_found"),
    (
        "job-result",
        "GET",
        "/v1/jobs/x/artifacts/result.json",
        None,
        None,
        404,
        "job_not_found",
    ),
]


def _agent_request(test_client, method, path, query, json_body):
    return test_client.request(method, path, params=query, json=json_body)


@pytest.mark.parametrize(
    "case_id,method,path,query,json_body,status,expected",
    AGENT_VALID_TABLE,
    ids=[row[0] for row in AGENT_VALID_TABLE],
)
def test_agent_valid_minimal_request(
    client, case_id, method, path, query, json_body, status, expected
):
    response = _agent_request(client, method, path, query, json_body)
    if callable(expected):
        assert response.status_code == status, response.text
        assert expected(response), response.text
    else:
        assert_api_error(response, status, expected)


@pytest.mark.parametrize(
    "case_id,method,path,query,json_body,status,expected",
    AGENT_VALID_TABLE,
    ids=[row[0] for row in AGENT_VALID_TABLE],
)
def test_agent_unauthenticated_requires_login(
    client, case_id, method, path, query, json_body, status, expected
):
    client.headers.pop("Authorization", None)
    response = _agent_request(client, method, path, query, json_body)
    assert_api_error(response, 401, "authentication_required")


@pytest.mark.parametrize(
    "case_id,method,path,query,json_body,status,expected",
    AGENT_VALID_TABLE,
    ids=[row[0] for row in AGENT_VALID_TABLE],
)
def test_agent_bad_origin_denied_before_token(
    client, case_id, method, path, query, json_body, status, expected
):
    # Origin is checked before the bearer token, even for an otherwise-valid one.
    response = client.request(
        method, path, params=query, json=json_body, headers={"Origin": "http://evil.test"}
    )
    assert_api_error(response, 403, "origin_denied")


@pytest.mark.parametrize(
    "case_id,method,path,query,json_body,status,expected",
    AGENT_VALID_TABLE,
    ids=[row[0] for row in AGENT_VALID_TABLE],
)
def test_agent_cookie_auth_behaves_like_header_auth(
    client, case_id, method, path, query, json_body, status, expected
):
    client.headers.pop("Authorization", None)
    client.cookies.set("harness_token", "a")
    response = _agent_request(client, method, path, query, json_body)
    assert response.status_code == status, response.text


# ---------------------------------------------------------------------------
# Agent app: static/UI routes (never auth-checked) and unknown path/method.
# ---------------------------------------------------------------------------

STATIC_ROUTES = [
    "/",
    "/guide",
    "/ui.js",
    "/ui-prefs.js",
    "/run-console.js",
    "/tour.js",
    "/ui.css",
    "/tour.css",
    "/vendor/markdown-it.min.js",
    "/mcp_bridge.py",
    "/setup-mcp.sh",
    "/bridge-requirements.txt",
]


@pytest.mark.parametrize("path", STATIC_ROUTES)
def test_agent_static_ui_routes_never_require_auth(client, path):
    client.headers.pop("Authorization", None)
    response = client.get(path)
    assert response.status_code == 200


def test_agent_bridge_lock_is_served_as_plain_text_never_cached(client):
    client.headers.pop("Authorization", None)
    response = client.get("/bridge-requirements.txt")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert response.headers["cache-control"] == "no-store"
    lock = Path(__file__).resolve().parents[1] / "agent_service/bridge-requirements.txt"
    assert response.content == lock.read_bytes()


def test_agent_public_asset_is_served_without_auth(client):
    client.headers.pop("Authorization", None)
    response = client.get("/assets/file-icons.svg")
    assert response.status_code == 200


def test_agent_unknown_asset_is_404_empty(client):
    client.headers.pop("Authorization", None)
    response = client.get("/assets/does-not-exist.svg")
    assert response.status_code == 404
    assert response.content == b""


def test_agent_unknown_path_is_404_before_auth(client):
    client.headers.pop("Authorization", None)
    response = client.get("/v1/does-not-exist")
    assert response.status_code == 404


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/v1/jobs"),
        ("PUT", "/v1/projects"),
        ("PATCH", "/v1/pages"),
        ("PATCH", "/v1/schedules"),
        ("GET", "/v1/schedules/x/run"),
    ],
)
def test_agent_wrong_method_is_405_before_auth(client, method, path):
    client.headers.pop("Authorization", None)
    response = client.request(method, path)
    assert response.status_code == 405


def test_agent_capabilities_etag_304(client):
    first = client.get("/.well-known/agent-capabilities.json")
    etag = first.headers["ETag"]
    second = client.get("/.well-known/agent-capabilities.json", headers={"If-None-Match": etag})
    assert second.status_code == 304


# ---------------------------------------------------------------------------
# Agent app: /v1/login (its own auth flow, checked ahead of `service.identity`).
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def owner_for_usage_rows(request, monkeypatch):
    """/v1/usage is owner-only (D-032); only its rows treat the table's client "a" as the owner."""
    callspec = getattr(request.node, "callspec", None)
    if callspec and str(callspec.id).startswith("usage"):
        monkeypatch.setattr("agent_service.harness_agents.LOCAL_CLIENT", "a")


@pytest.fixture
def login_client(tmp_path):
    cfg = {
        "state_dir": str(tmp_path),
        "origins": ["http://testserver"],
        "projects": {"p": {}},
        "clients": {
            "a": {"sha256": hashlib.sha256(b"a").hexdigest(), "projects": ["p"]},
        },
        "services": {},
    }
    app = create_app(cfg)
    with TestClient(app) as test_client:
        yield test_client


def test_login_success_sets_cookie(login_client):
    response = login_client.post(
        "/v1/login", json={"token": "a"}, headers={"Origin": "http://testserver"}
    )
    assert response.status_code == 200
    assert response.json() == {"authenticated": True}
    cookie = next(c for c in login_client.cookies.jar if c.name == "harness_token")
    assert cookie.value == "a"
    assert not cookie.secure


def test_login_without_origin_is_denied(login_client):
    response = login_client.post("/v1/login", json={"token": "a"})
    assert_api_error(response, 403, "origin_denied")


def test_login_without_origin_is_denied_before_the_token_and_the_limit(login_client):
    # 403 for a good and a bad token alike (no token oracle); none of it spends the budget.
    for token in ["a", "wrong"] * 12:
        response = login_client.post("/v1/login", json={"token": token})
        assert_api_error(response, 403, "origin_denied")
    response = login_client.post(
        "/v1/login", json={"token": "wrong"}, headers={"Origin": "http://testserver"}
    )
    assert_api_error(response, 401, "authentication_required")


def test_login_with_wrong_token_is_unauthenticated(login_client):
    response = login_client.post(
        "/v1/login", json={"token": "wrong"}, headers={"Origin": "http://testserver"}
    )
    assert_api_error(response, 401, "authentication_required")


def test_login_with_non_object_body_is_object_required(login_client):
    response = login_client.post(
        "/v1/login", content=b"[]", headers={"Origin": "http://testserver"}
    )
    assert_api_error(response, 422, "object_required")


def test_login_rate_limited_after_twenty_per_minute(login_client):
    for _ in range(20):
        login_client.post(
            "/v1/login", json={"token": "wrong"}, headers={"Origin": "http://testserver"}
        )
    response = login_client.post(
        "/v1/login", json={"token": "wrong"}, headers={"Origin": "http://testserver"}
    )
    assert_api_error(response, 429, "login_rate_limit")
    assert "Retry-After" in response.headers


# ---------------------------------------------------------------------------
# Agent app: local-machine identity bypass (client 127.0.0.1, local_access).
# ---------------------------------------------------------------------------


def test_local_access_identity_reaches_history(make_harness_config):
    cfg = make_harness_config(origins=[])
    app = create_app(cfg)

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 54321)),
            base_url="http://127.0.0.1",
        ) as test_client:
            return await test_client.get("/v1/history")

    response = asyncio.run(scenario())
    assert response.status_code == 200
    assert response.json() == {"jobs": []}


# ---------------------------------------------------------------------------
# Agent app: seeded completed job (row exercises the shapes that need real data).
# ---------------------------------------------------------------------------


def test_seeded_completed_job_routes(client):
    app = client.app
    with app.state.service.db:
        app.state.service.db.execute(
            "INSERT INTO jobs(id,project,owner,state,created,payload,result,idem,digest) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                "job-1",
                "p",
                "a",
                "completed",
                time.time(),
                '{"prompt":"hi","backend":"codex"}',
                '{"ok":true}',
                None,
                "digest-1",
            ),
        )

    job = client.get("/v1/jobs/job-1")
    assert job.status_code == 200
    body = job.json()
    assert body["id"] == "job-1"
    assert not ({"payload", "digest", "idem", "owner"} & set(body))

    result = client.get("/v1/jobs/job-1/artifacts/result.json")
    assert result.status_code == 200
    assert result.json() == {"ok": True}
    assert "X-Content-SHA256" in result.headers

    events = client.get("/v1/jobs/job-1/events")
    assert events.status_code == 200

    cancel = client.post("/v1/jobs/job-1/cancel", json={})
    assert cancel.status_code == 200
    assert cancel.json() == {"job_id": "job-1", "cancel_requested": False}

    conversation_get = client.get("/v1/conversations/job-1")
    assert conversation_get.status_code == 200

    conversation_patch = client.patch("/v1/conversations/job-1", json={"title": "Renamed"})
    assert conversation_patch.status_code == 200
    assert conversation_patch.json() == {"id": "job-1", "title": "Renamed"}

    conversation_delete = client.delete("/v1/conversations/job-1")
    assert conversation_delete.status_code == 200


# ---------------------------------------------------------------------------
# Admin app (control.server.create_app): guard checks (design section 2, rows
# 79-86) that apply uniformly ahead of any specific route logic.
# ---------------------------------------------------------------------------


@pytest.fixture
def admin_pair(tmp_path):
    """(app, manager) with a cheap, patched-out inventory, per design section 2."""
    app = create_admin_app(str(tmp_path), 8094)
    manager = app.state.manager
    manager.inventory = {
        "services": [],
        "binaries": {"tailscale": None},
        "network": {"online": False, "hostname": None},
    }
    manager.refresh = AsyncMock(return_value=manager.inventory)
    manager.integrations = lambda: {}
    return app, manager


async def _admin_client(app):
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8094"
    )
    await sign_in(client, app).get("/")  # acquire the admin cookie, as every admin test needs it
    return client


def test_admin_guard_host_client_and_tailscale_login(admin_pair):
    app, _manager = admin_pair

    async def scenario():
        client = await _admin_client(app)
        try:
            for headers in (
                {"Host": "evil.test:8094"},
                {"Tailscale-User-Login": "person@example.test"},
            ):
                response = await client.post(
                    "/api/settings-export",
                    json={},
                    headers={"X-Harness-Admin": "1", **headers},
                )
                assert response.status_code == 403
                assert response.json() == {"error": "Management is only available on this machine."}
            remote = httpx.ASGITransport(app=app, client=("10.0.0.2", 1234))
            async with httpx.AsyncClient(
                transport=remote, base_url="http://127.0.0.1:8094"
            ) as other:
                response = await other.get("/")
                assert response.status_code == 403
        finally:
            await client.aclose()

    asyncio.run(scenario())


def test_admin_guard_origin_and_sec_fetch(admin_pair):
    app, _manager = admin_pair

    async def scenario():
        client = await _admin_client(app)
        try:
            for headers in (
                {"Origin": "https://evil.test"},
                {"Sec-Fetch-Site": "cross-site"},
            ):
                response = await client.post(
                    "/api/settings-export",
                    json={},
                    headers={"X-Harness-Admin": "1", **headers},
                )
                assert response.status_code == 403
                assert response.json() == {"error": "Unauthorized origin."}
            # A cross-site top-level navigation GET / is allowed (a clicked link).
            navigation = await client.get(
                "/",
                headers={
                    "Sec-Fetch-Site": "cross-site",
                    "Sec-Fetch-Mode": "navigate",
                    "Sec-Fetch-Dest": "document",
                    "Sec-Fetch-User": "?1",
                },
            )
            assert navigation.status_code == 200
        finally:
            await client.aclose()

    asyncio.run(scenario())


def test_admin_guard_requires_cookie_on_api(admin_pair):
    app, _manager = admin_pair

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8094"
        ) as client:
            response = await client.post(
                "/api/settings-export", json={}, headers={"X-Harness-Admin": "1"}
            )
            assert response.status_code == 401
            assert response.json() == {
                "error": "Open KeepHarness from its app, or run `keepharness open` on this computer,"
                " to sign this browser in."
            }

    asyncio.run(scenario())


def test_admin_guard_header_size_and_body_shape(admin_pair):
    app, _manager = admin_pair

    async def scenario():
        client = await _admin_client(app)
        try:
            no_header = await client.post("/api/settings-export", json={})
            assert no_header.status_code == 400
            assert no_header.json() == {"error": "Administrative header required."}

            too_large = await client.post(
                "/api/settings",
                content=b"{}",
                headers={
                    "X-Harness-Admin": "1",
                    "Content-Length": str(ADMIN_BODY_LIMIT + 1),
                },
            )
            assert too_large.status_code == 413
            assert too_large.json() == {"error": "Request too large."}

            not_object = await client.post(
                "/api/settings", content=b"[]", headers={"X-Harness-Admin": "1"}
            )
            assert not_object.status_code == 400
            assert not_object.json() == {"error": "The request must be a JSON object."}
        finally:
            await client.aclose()

    asyncio.run(scenario())


def test_admin_responses_carry_security_headers(admin_pair):
    app, _manager = admin_pair

    async def scenario():
        client = await _admin_client(app)
        try:
            response = await client.get("/api/state", headers={"X-Harness-Admin": "1"})
        finally:
            await client.aclose()
        assert response.headers["Cache-Control"] == "no-store"
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["Referrer-Policy"] == "no-referrer"
        assert "Content-Security-Policy" in response.headers

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# Admin app: per-route table (design section 2, rows 88-103). Body defaults to
# {} for POST unless noted; every case shares the admin_pair patched fixture.
# ---------------------------------------------------------------------------

ADMIN_ROUTE_TABLE = [
    # (id, method, path, query, json_body, status, checker)
    ("root", "GET", "/", None, None, 200, None),
    ("admin-js", "GET", "/admin.js", None, None, 200, None),
    ("admin-css", "GET", "/admin.css", None, None, 200, None),
    (
        "folders-ok",
        "GET",
        "/api/folders",
        "TMP",
        None,
        200,
        lambda r: {"path", "parent", "directories", "truncated"} <= set(r.json()),
    ),
    ("folders-relative", "GET", "/api/folders", {"path": "rel"}, None, 400, None),
    ("dashboard", "GET", "/api/dashboard", None, None, 200, None),
    (
        "state",
        "GET",
        "/api/state",
        None,
        None,
        200,
        lambda r: (
            {
                "settings",
                "inventory",
                "status",
                "authentication",
                "models",
                "integrations",
                "operations",
                "local_profile",
                "local_profiles",
                "credentials",
            }
            <= set(r.json())
        ),
    ),
    ("nope-get", "GET", "/api/nope", None, None, 404, lambda r: r.json() == {"error": "Not found"}),
    ("nope-post", "POST", "/api/nope", None, {}, 404, lambda r: r.json() == {"error": "Not found"}),
    (
        "folders-create",
        "POST",
        "/api/folders/create",
        None,
        {},
        400,
        lambda r: r.json()["error"].startswith("Use a simple folder name"),
    ),
    (
        "check",
        "POST",
        "/api/check",
        None,
        {},
        400,
        lambda r: r.json() == {"error": "Unknown service."},
    ),
    (
        "provider-token",
        "POST",
        "/api/provider-token",
        None,
        {},
        400,
        lambda r: r.json() == {"error": "Unknown API provider."},
    ),
    (
        "provider-delete",
        "POST",
        "/api/provider-delete",
        None,
        {},
        400,
        lambda r: r.json() == {"error": "Unknown provider."},
    ),
    (
        "integration-catalog",
        "POST",
        "/api/integration-catalog",
        None,
        {},
        400,
        lambda r: r.json() == {"error": "Invalid provider."},
    ),
    (
        "integration",
        "POST",
        "/api/integration",
        None,
        {},
        400,
        lambda r: r.json() == {"error": "Invalid provider."},
    ),
    (
        "provider-login",
        "POST",
        "/api/provider-login",
        None,
        {},
        400,
        lambda r: r.json() == {"error": "CLI not found."},
    ),
    (
        "provider-login-code",
        "POST",
        "/api/provider-login-code",
        None,
        {},
        400,
        lambda r: r.json() == {"error": "No login is waiting for a code."},
    ),
    (
        "settings-export",
        "POST",
        "/api/settings-export",
        None,
        {},
        200,
        lambda r: r.json()["format"] == "keepharness-settings" and r.json()["version"] == 1,
    ),
    (
        "settings-import",
        "POST",
        "/api/settings-import",
        None,
        {},
        400,
        lambda r: r.json() == {"error": "Incompatible configuration format."},
    ),
    ("settings", "POST", "/api/settings", None, {}, 200, lambda r: r.json() == {"saved": True}),
    ("scan", "POST", "/api/scan", None, {}, 200, None),
    (
        "start",
        "POST",
        "/api/start",
        None,
        {},
        400,
        lambda r: r.json() == {"error": "Enable at least one service."},
    ),
    ("stop", "POST", "/api/stop", None, {}, 200, None),
    (
        "cancel-operation",
        "POST",
        "/api/cancel-operation",
        None,
        {},
        200,
        lambda r: r.json() == {"cancelled": True},
    ),
    (
        "model-install",
        "POST",
        "/api/model-install",
        None,
        {},
        400,
        lambda r: r.json() == {"error": "Unknown model."},
    ),
    (
        "local-profile",
        "POST",
        "/api/local-profile",
        None,
        {},
        400,
        lambda r: r.json() == {"error": "Provide the model and executable for this profile."},
    ),
    (
        "local-devices",
        "POST",
        "/api/local-devices",
        None,
        {},
        400,
        lambda r: r.json() == {"error": "Provide an existing llama-server executable."},
    ),
    ("local-import", "POST", "/api/local-import", None, {}, 400, None),
    (
        "local-files",
        "POST",
        "/api/local-files",
        None,
        {},
        200,
        lambda r: {"files", "servers"} <= set(r.json()),
    ),
    ("local-start", "POST", "/api/local-start", None, {}, 400, None),
    (
        "tailnet",
        "POST",
        "/api/tailnet",
        None,
        {},
        400,
        lambda r: r.json() == {"error": "Connect Tailscale first."},
    ),
]

# Routes that patch control.local_models.processes to [] (design section 2, row 100).
_PATCHED_PROCESSES_IDS = {"local-import", "local-files", "local-start"}


@pytest.mark.parametrize(
    "case_id,method,path,query,json_body,status,checker",
    ADMIN_ROUTE_TABLE,
    ids=[row[0] for row in ADMIN_ROUTE_TABLE],
)
def test_admin_route_table(
    admin_pair, tmp_path, case_id, method, path, query, json_body, status, checker
):
    app, _manager = admin_pair
    if query == "TMP":
        query = {"path": str(tmp_path)}

    async def scenario():
        client = await _admin_client(app)
        try:
            if case_id in _PATCHED_PROCESSES_IDS:
                with patch("control.local_models.processes", return_value=[]):
                    return await client.request(
                        method,
                        path,
                        params=query,
                        json=json_body,
                        headers={"X-Harness-Admin": "1"},
                    )
            return await client.request(
                method, path, params=query, json=json_body, headers={"X-Harness-Admin": "1"}
            )
        finally:
            await client.aclose()

    response = asyncio.run(scenario())
    assert response.status_code == status, response.text
    if checker:
        assert checker(response), response.text


def test_admin_operation_in_progress_rejected_with_retry_after(admin_pair):
    app, manager = admin_pair

    async def scenario():
        client = await _admin_client(app)
        try:
            manager.operations.jobs["running-1"] = {
                "id": "running-1",
                "state": "running",
                "kind": "provider-login",
                "provider": "codex",
            }
            manager.operations.jobs["running-2"] = {
                "id": "running-2",
                "state": "running",
                "kind": "provider-login",
                "provider": "codex",
            }
            manager.operations.jobs["running-3"] = {
                "id": "running-3",
                "state": "running",
                "kind": "provider-login",
                "provider": "codex",
            }
            manager.operations.jobs["running-4"] = {
                "id": "running-4",
                "state": "running",
                "kind": "provider-login",
                "provider": "codex",
            }
            response = await client.post(
                "/api/provider-login",
                json={"provider": "gemini"},
                headers={"X-Harness-Admin": "1"},
            )
            assert response.status_code == 429
            assert response.headers["Retry-After"] == "5"
        finally:
            await client.aclose()

    asyncio.run(scenario())
    assert ADMIN_OPERATION_LIMIT == 4


def test_admin_lock_held_rejects_with_retry_after_one(admin_pair):
    app, manager = admin_pair

    async def scenario():
        client = await _admin_client(app)
        try:
            async with manager.lock:
                response = await client.post("/api/scan", json={}, headers={"X-Harness-Admin": "1"})
            assert response.status_code == 429
            assert response.headers["Retry-After"] == "1"
        finally:
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("method,path", [("POST", "/"), ("PUT", "/api/anything")])
def test_admin_wrong_method_is_405(admin_pair, method, path):
    app, _manager = admin_pair

    async def scenario():
        client = await _admin_client(app)
        try:
            return await client.request(method, path, headers={"X-Harness-Admin": "1"})
        finally:
            await client.aclose()

    response = asyncio.run(scenario())
    assert response.status_code == 405


def test_admin_imports_settings_exported_before_the_rename(admin_pair):
    # A bundle exported by Tail Harness (before 0.15.0) names its format after the old slug.
    app, _manager = admin_pair

    async def scenario():
        client = await _admin_client(app)
        try:
            headers = {"X-Harness-Admin": "1"}
            bundle = (await client.post("/api/settings-export", json={}, headers=headers)).json()
            bundle["format"] = "tail-harness-settings"
            imported = await client.post(
                "/api/settings-import", json={"bundle": bundle}, headers=headers
            )
            bundle["format"] = "another-harness-settings"
            refused = await client.post(
                "/api/settings-import", json={"bundle": bundle}, headers=headers
            )
            return imported, refused
        finally:
            await client.aclose()

    imported, refused = asyncio.run(scenario())
    assert imported.status_code == 200, imported.text
    assert refused.status_code == 400
    assert refused.json() == {"error": "Incompatible configuration format."}
