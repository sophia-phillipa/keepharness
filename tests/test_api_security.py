"""Cross-identity API regressions using isolated state and no inference worker."""

import copy
import hashlib
import json
import sqlite3
from pathlib import Path

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
            "INSERT INTO jobs(id,project,owner,state,created,payload,result,idem,digest) VALUES(?,?,?,?,?,?,?,?,?)",
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


# The local owner, the Host allow-list and the views other clients get (D09, SEC-R5 RC-01/02/04/05).
LOCAL_SECRET = "fixture-install-secret"
REMOTE = "http://machine.example.ts.net:8093"
GUEST_LOGIN = "guest@example.test"
# What Tailscale Serve adds when it proxies a tailnet peer to 127.0.0.1 (ipn/ipnlocal/serve.go).
SERVE_HEADERS = {
    "Host": "machine.example.ts.net:8093",
    "X-Forwarded-Host": "machine.example.ts.net:8093",
    "X-Forwarded-For": "100.101.102.103",
}


def owner_config(tmp_path, **overrides):
    from control import local_access

    cfg = {
        "state_dir": str(tmp_path / "runs"),
        "port": 8095,
        "local_access": True,
        "local_secret_sha256": local_access.digest(LOCAL_SECRET),
        "control_state_dir": str(tmp_path / "control"),
        "origins": ["http://127.0.0.1:8095", "http://localhost:8095", REMOTE],
        "browser_url": REMOTE + "/",
        "tailscale_logins": {GUEST_LOGIN: "tailnet-guest"},
        "projects": {"sem-projeto": {}},
        "clients": {
            name: {"sha256": hashlib.sha256(name.encode()).hexdigest(), "projects": ["sem-projeto"]}
            for name in ("local", "tailnet-guest", "vpn")
        },
        "services": {},
    }
    cfg.update(overrides)
    return cfg


def owner_cookie(cfg):
    """A browser session the admin issued, as ``keepharness open`` or the desktop app gets."""
    from control import local_access

    state = Path(cfg["control_state_dir"])
    state.mkdir(parents=True, exist_ok=True)
    return {local_access.COOKIE: local_access.issue_session(state)}


def seeded_owner_app(cfg):
    app = create_app(cfg)
    with app.state.service.db as db:
        for owner in cfg["clients"]:
            db.execute(
                "INSERT INTO jobs(id,project,owner,state,created,payload,result,idem,digest) VALUES(?,?,?,?,?,?,?,?,?)",
                (owner + "-job", "sem-projeto", owner, "completed", 1, "{}", "{}", None, owner),
            )
    return app


def loopback(app, **kwargs):
    import httpx

    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 4321)),
        base_url="http://127.0.0.1:8095",
        **kwargs,
    )


async def who(client, **kwargs):
    response = await client.get("/v1/conversations", **kwargs)
    if response.status_code != 200:
        return response.status_code, response.json()["code"]
    return 200, [row["id"] for row in response.json()["conversations"]]


def test_local_identity_needs_install_secret(tmp_path):
    import asyncio

    from control import local_access

    cfg = owner_config(tmp_path)
    app = seeded_owner_app(cfg)

    async def scenario():
        async with loopback(app) as client:
            # Loopback alone is any account on this computer: no owner without the secret.
            assert await who(client) == (401, "authentication_required")
            wrong = {local_access.COOKIE: "guess"}
            assert await who(client, cookies=wrong) == (401, "authentication_required")
            # A cookie holding the install secret itself (issued before 0.15.0) is refused.
            raw = {local_access.COOKIE: LOCAL_SECRET}
            assert await who(client, cookies=raw) == (401, "authentication_required")
            owner = owner_cookie(cfg)
            assert await who(client, cookies=owner) == (200, ["local-job"])
            assert await who(client, cookies=owner, headers={"Host": "localhost:8095"}) == (
                200,
                ["local-job"],
            )
            # The secret is not a bearer token: it never authenticates from elsewhere.
            bearer = {"Authorization": "Bearer " + LOCAL_SECRET}
            assert await who(client, headers=bearer) == (401, "authentication_required")

    try:
        asyncio.run(scenario())
    finally:
        app.state.service.db.close()


def test_runtime_config_carries_only_the_install_secret_digest(tmp_path):
    import stat

    from control import local_access
    from control.runtime_config import base_config

    settings = {"services": {}, "uploads_enabled": False, "projects": [], "port": 8095}
    cfg = base_config(settings, tmp_path, 8094, None, {})
    key = tmp_path / local_access.KEY_FILE
    assert stat.S_IMODE(key.stat().st_mode) == 0o600
    secret = key.read_text()
    assert cfg["local_secret_sha256"] == local_access.digest(secret)
    assert secret not in json.dumps(cfg)
    # The same install keeps its secret across rebuilds.
    assert (
        base_config(settings, tmp_path, 8094, None, {})["local_secret_sha256"]
        == cfg["local_secret_sha256"]
    )
    assert (cfg["shared_projects"], cfg["project_registration"]) == (False, True)


def test_tailscale_login_requires_remote_host(tmp_path):
    import asyncio

    cfg = owner_config(tmp_path)
    app = seeded_owner_app(cfg)
    login = {"Tailscale-User-Login": GUEST_LOGIN}

    async def scenario():
        async with loopback(app) as client:
            # A rebinding page: same-origin to its own name, which resolves to 127.0.0.1.
            rebinding = {**login, "Host": "attacker.test:8095", "Sec-Fetch-Site": "same-origin"}
            assert await who(client, headers=rebinding) == (403, "host_denied")
            # Listed hosts that are not the configured remote origin do not map the login.
            assert await who(client, headers=login) == (401, "authentication_required")
            # Only the remote origin's Host, as Tailscale Serve forwards it, maps the login.
            remote = {**login, **SERVE_HEADERS}
            assert await who(client, headers=remote) == (200, ["tailnet-guest-job"])
            # Without Serve's forwarding headers, or with a peer outside the tailnet ranges or a
            # forwarded Host that differs, the request did not come through Serve.
            for name, value in (
                ("X-Forwarded-For", ""),
                ("X-Forwarded-For", "203.0.113.7"),
                ("X-Forwarded-For", "100.101.102.103, 100.101.102.104"),
                ("X-Forwarded-Host", ""),
                ("X-Forwarded-Host", "attacker.test:8095"),
            ):
                assert await who(client, headers={**remote, name: value}) == (
                    401,
                    "authentication_required",
                ), (name, value)
            ipv6 = {**remote, "X-Forwarded-For": "fd7a:115c:a1e0::1"}
            assert await who(client, headers=ipv6) == (200, ["tailnet-guest-job"])
            # An unlisted Host never reaches the owner, even with the owner's cookie (which a
            # browser would not send to that name anyway).
            owner = owner_cookie(cfg)
            for path in ("/v1/projects", "/v1/conversations", "/v1/harness-agents"):
                response = await client.get(path, cookies=owner, headers={"Host": "attacker.test"})
                assert response.status_code == 401, path
            for path in ("/v1/projects", "/v1/harness-agents"):
                response = await client.get(path, headers=rebinding)
                assert (response.status_code, response.json()["code"]) == (403, "host_denied")
            # An explicit bearer token cannot be planted by another site, so it keeps working.
            bearer = {"Authorization": "Bearer vpn", "Host": "attacker.test"}
            assert await who(client, headers=bearer) == (200, ["vpn-job"])

    try:
        asyncio.run(scenario())
    finally:
        app.state.service.db.close()

    unshared = seeded_owner_app(owner_config(tmp_path / "unshared", browser_url=None))

    async def without_remote_origin():
        async with loopback(unshared) as client:
            remote = {**login, **SERVE_HEADERS}
            assert await who(client, headers=remote) == (401, "authentication_required")

    try:
        asyncio.run(without_remote_origin())
    finally:
        unshared.state.service.db.close()


@pytest.mark.parametrize(
    "browser_url", ["http://127.0.0.1:8095/", "http://localhost:8095/", "http://[::1]:8095/"]
)
def test_tailscale_login_never_maps_on_a_loopback_browser_url(tmp_path, browser_url):
    import asyncio
    from urllib.parse import urlsplit

    host = urlsplit(browser_url).netloc
    cfg = owner_config(tmp_path, browser_url=browser_url, origins=[browser_url.rstrip("/")])
    app = seeded_owner_app(cfg)
    forged = {
        "Tailscale-User-Login": GUEST_LOGIN,
        **SERVE_HEADERS,
        "Host": host,
        "X-Forwarded-Host": host,
    }

    async def scenario():
        async with loopback(app) as client:
            assert await who(client, headers=forged) == (401, "authentication_required")

    try:
        asyncio.run(scenario())
    finally:
        app.state.service.db.close()


def test_project_management_local_only(tmp_path):
    import asyncio

    root = tmp_path / "host-folder"
    root.mkdir()
    (root / "keep.txt").write_text("fixture")
    cfg = owner_config(tmp_path, project_registration=True, shared_projects=True)
    app = create_app(cfg)
    owner = owner_cookie(cfg)
    vpn = {"Authorization": "Bearer vpn"}

    async def scenario():
        async with loopback(app) as client:
            created = await client.post(
                "/v1/projects", json={"root": str(root), "label": "Host"}, cookies=owner
            )
            assert created.status_code == 201, created.text
            pid = created.json()["project_id"]
            # Shared on purpose here, so only the new gate stands between a guest and the folder.
            assert pid in (await client.get("/v1/projects", headers=vpn)).json()["projects"]
            preview = (
                await client.get("/v1/project-folder", params={"project_id": pid}, cookies=owner)
            ).json()
            attempts = (
                ("POST", "/v1/projects", None, {"root": str(tmp_path), "label": "Other"}),
                ("PATCH", "/v1/projects", None, {"project_id": pid, "root": str(tmp_path)}),
                (
                    "DELETE",
                    "/v1/project-folder",
                    {"project_id": pid},
                    {**preview, "confirmed": True},
                ),
                ("GET", "/v1/project-directories", None, None),
            )
            for method, path, params, data in attempts:
                response = await client.request(method, path, params=params, json=data, headers=vpn)
                assert (response.status_code, response.json()["code"]) == (
                    403,
                    "project_management_local_only",
                ), (method, path)
            assert (root / "keep.txt").read_text() == "fixture"
            assert app.state.service.config["projects"][pid]["root"] == str(root)

    try:
        asyncio.run(scenario())
    finally:
        app.state.service.db.close()


def test_registered_projects_stay_with_the_owner_unless_shared(tmp_path):
    import asyncio

    root = tmp_path / "owner-folder"
    root.mkdir()
    cfg = owner_config(tmp_path, project_registration=True)
    app = create_app(cfg)
    owner = owner_cookie(cfg)

    async def scenario():
        async with loopback(app) as client:
            created = await client.post("/v1/projects", json={"root": str(root)}, cookies=owner)
            pid = created.json()["project_id"]
            assert pid in (await client.get("/v1/projects", cookies=owner)).json()["projects"]
            guest = (
                await client.get("/v1/projects", headers={"Authorization": "Bearer vpn"})
            ).json()
            assert guest["projects"] == ["sem-projeto"]
            return pid

    try:
        pid = asyncio.run(scenario())
    finally:
        app.state.service.db.close()
    restarted = create_app(copy.deepcopy(cfg))
    try:
        clients = restarted.state.service.config["clients"]
        assert pid in clients["local"]["projects"]
        assert pid not in clients["vpn"]["projects"]
        assert pid not in clients["tailnet-guest"]["projects"]
    finally:
        restarted.state.service.db.close()


def test_non_local_views_redacted(tmp_path, monkeypatch):
    import asyncio

    home = tmp_path / "home"
    skill = home / ".claude" / "skills" / "private-skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: private-skill\ndescription: Fixture\n---\nBody\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    control = tmp_path / "control"
    control.mkdir()
    service = {
        "enabled": True,
        "mode": "native",
        "models": ["sonnet"],
        "projects": ["sem-projeto"],
        "permissions": {"read": True},
    }
    # The owner's user-scope skills are offered only with the personal-setup opt-in (D01), a
    # top-level setting; non-local identities must never see them or their home paths.
    cfg = owner_config(
        tmp_path,
        control_state_dir=str(control),
        services={"claude": service},
        project_registration=True,
        personal_setup=True,
    )
    app = create_app(cfg)
    owner = owner_cookie(cfg)
    persona = {
        "name": "release-checker",
        "purpose": "Checks releases",
        "instructions": "PRIVATE-INSTRUCTIONS",
        "tasks": ["PRIVATE-TASK"],
        "target_output": "PRIVATE-OUTPUT",
        "backend": "claude",
        "model": "sonnet",
        "effort": "configured",
    }
    query = {"project_id": "sem-projeto", "backend": "claude", "model": "sonnet"}

    async def scenario():
        async with loopback(app) as client:
            created = await client.post("/v1/harness-agents", json=persona, cookies=owner)
            assert created.status_code == 201, created.text
            views = {}
            for name, credential in (
                ("owner", {"cookies": owner}),
                ("guest", {"headers": {"Authorization": "Bearer vpn"}}),
            ):
                views[name] = {
                    path: await client.get(path, params=params, **credential)
                    for path, params in (
                        ("/v1/harness-agents", None),
                        ("/v1/resources", query),
                        ("/v1/catalog", {"project_id": "sem-projeto"}),
                    )
                }
            # The owner still sees everything, which proves the fixture exposes paths at all.
            assert "PRIVATE-INSTRUCTIONS" in views["owner"]["/v1/harness-agents"].text
            assert str(home) in views["owner"]["/v1/resources"].text
            for path, response in views["guest"].items():
                assert response.status_code == 200, (path, response.text)
                for private in (
                    "PRIVATE-INSTRUCTIONS",
                    "PRIVATE-TASK",
                    "PRIVATE-OUTPUT",
                    str(home),
                ):
                    assert private not in response.text, (path, private)
            agent = views["guest"]["/v1/harness-agents"].json()["agents"][0]
            assert (agent["name"], agent["available"]) == ("release-checker", True)
            items = views["guest"]["/v1/resources"].json()["items"]
            assert "private-skill" not in {item["name"] for item in items}
            guest_dirs = await client.get(
                "/v1/project-directories", headers={"Authorization": "Bearer vpn"}
            )
            assert guest_dirs.status_code == 403

    try:
        asyncio.run(scenario())
    finally:
        app.state.service.db.close()
