"""Cross-identity API regressions using isolated state and no inference worker."""

import hashlib
import json
import sqlite3

import pytest
from starlette.testclient import TestClient

from agent_service.app import APIError, Service, create_app


@pytest.fixture
def api(tmp_path):
    cfg = {
        "state_dir": str(tmp_path),
        "origins": ["http://testserver"],
        "projects": {"shared": {}},
        "uploads_enabled": True,
        "clients": {
            name: {"sha256": hashlib.sha256(name.encode()).hexdigest(), "projects": ["shared"]}
            for name in ("alice", "bob")
        },
        "services": {
            "codex": {
                "enabled": True,
                "models": ["fixture"],
                "projects": ["shared"],
                "permissions": {"upload": True, "read": True},
            }
        },
        "codex_models": {"fixture": ["low"]},
    }
    app = create_app(cfg)
    # No lifespan means no background worker or provider CLI can execute.
    client = TestClient(app, headers={"Authorization": "Bearer alice"})
    yield client, app.state.service, cfg
    client.close()
    app.state.service.db.close()


def seed_job(service):
    with service.db:
        service.db.execute(
            "INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?)",
            (
                "alice-job",
                "shared",
                "alice",
                "completed",
                1,
                json.dumps({"prompt": "Alice private prompt"}),
                json.dumps({"answer": "Alice private answer"}),
                None,
                "fixture",
            ),
        )
    return "alice-job"


def test_job_events_result_cancel_and_conversation_require_owner(api):
    client, service, _ = api
    jid = seed_job(service)
    other = {"Authorization": "Bearer bob"}
    for suffix in ("", "/events", "/artifacts/result.json"):
        assert client.get("/v1/jobs/" + jid + suffix, headers=other).status_code == 403
    assert client.post("/v1/jobs/" + jid + "/cancel", headers=other).status_code == 403
    assert client.get("/v1/conversations/" + jid, headers=other).status_code == 403
    assert client.delete("/v1/conversations/" + jid, headers=other).status_code == 403
    assert client.get("/v1/conversations", headers=other).json()["conversations"] == []


def test_legacy_history_does_not_disclose_other_identity_prompts(api):
    client, service, _ = api
    seed_job(service)
    response = client.get("/v1/history", headers={"Authorization": "Bearer bob"})
    assert response.status_code == 200
    assert response.json()["jobs"] == []


def test_uploaded_attachment_cannot_be_used_by_another_identity(api):
    client, _, _ = api
    uploaded = client.post(
        "/v1/files?project_id=shared",
        headers={"X-Filename": "private.txt"},
        content=b"Alice private attachment",
    )
    assert uploaded.status_code == 201
    response = client.post(
        "/v1/jobs",
        headers={"Authorization": "Bearer bob"},
        json={
            "project_id": "shared",
            "backend": "codex",
            "model": "fixture",
            "effort": "low",
            "prompt": "Read attachment",
            "file_ids": [uploaded.json()["file_id"]],
        },
    )
    assert response.status_code in (403, 404), response.text
    own_response = client.post(
        "/v1/jobs",
        json={
            "project_id": "shared",
            "backend": "codex",
            "model": "fixture",
            "effort": "low",
            "prompt": "Read own attachment",
            "file_ids": [uploaded.json()["file_id"]],
        },
    )
    assert own_response.status_code == 202, own_response.text


def test_file_upload_requires_a_provider_with_upload_permission(api):
    client, _, cfg = api
    cfg["services"]["codex"]["permissions"]["upload"] = False
    response = client.post(
        "/v1/files?project_id=shared",
        headers={"X-Filename": "blocked.txt"},
        content=b"not permitted",
    )
    assert response.status_code == 403, response.text


def test_bad_auth_and_cross_origin_are_rejected(api):
    client, _, _ = api
    assert client.get("/v1/projects", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert (
        client.get("/v1/projects", headers={"Origin": "https://untrusted.example"}).status_code
        == 403
    )


def test_legacy_attachment_owner_migration_is_fail_closed(api, tmp_path):
    _, _, cfg = api
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    with sqlite3.connect(legacy / "jobs.sqlite3") as db:
        db.execute(
            "CREATE TABLE files(id TEXT PRIMARY KEY, project TEXT, name TEXT, size INTEGER, hash TEXT, pages TEXT)"
        )
        db.execute(
            "INSERT INTO files VALUES(?,?,?,?,?,?)",
            ("legacy-file", "shared", "old.txt", 3, "fixture", "[]"),
        )
    service = Service({**cfg, "state_dir": str(legacy)})
    try:
        assert "owner" in {column[1] for column in service.db.execute("PRAGMA table_info(files)")}
        assert service.db.execute("SELECT count(*) FROM files").fetchone()[0] == 1
        for owner in ("alice", "bob"):
            with pytest.raises(APIError) as error:
                service.file("shared", "legacy-file", owner)
            assert error.value.status == 404
    finally:
        service.db.close()


@pytest.mark.parametrize("control", ["‮", "‪", "⁦", "⁩", "؜"])
def test_bidi_control_characters_in_filenames_are_refused(api, control):
    client, _, _ = api
    from urllib.parse import quote

    response = client.post(
        "/v1/files?project_id=shared",
        headers={"X-Filename": quote("invoice" + control + "txt.exe")},
        content=b"fixture",
    )
    assert response.status_code == 422, response.text
    assert "invalid_filename" in response.text


def test_ui_csp_allows_data_images_for_the_select_chevron(api):
    client, _, _ = api
    policy = client.get("/").headers["content-security-policy"]
    assert "img-src 'self' data:" in policy
    assert "default-src 'self'" in policy


@pytest.mark.parametrize("path", ["/", "/ui.js", "/ui.css", "/assets/tabler.min.css"])
def test_static_files_are_compressed_and_revalidated(api, path):
    client, _, _ = api
    first = client.get(path, headers={"Accept-Encoding": "gzip"})
    assert first.status_code == 200
    assert first.headers["content-encoding"] == "gzip"
    assert first.headers["cache-control"] == "no-cache"
    again = client.get(path, headers={"If-None-Match": first.headers["etag"]})
    assert again.status_code == 304
    assert again.content == b""


def test_api_json_is_neither_compressed_nor_cached(api):
    client, _, _ = api
    response = client.get("/v1/projects", headers={"Accept-Encoding": "gzip"})
    assert response.status_code == 200
    assert "content-encoding" not in response.headers
    assert response.headers.get("cache-control", "no-store") == "no-store"
