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
def api(tmp_path, monkeypatch):
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
        assert client.get("/v1/jobs/" + jid + suffix, headers=other).status_code == 404
    assert client.post("/v1/jobs/" + jid + "/cancel", headers=other).status_code == 404
    assert client.get("/v1/conversations/" + jid, headers=other).status_code == 404
    assert client.delete("/v1/conversations/" + jid, headers=other).status_code == 404
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


def serve_from_tailscaled(client, port):
    """Stands in for the /proc/net/tcp proof, which tests/test_serve_proof.py covers."""
    return True


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
            for name in ("local", "tailnet-guest", "token-guest")
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


def test_version_requires_desktop_owner_session(tmp_path):
    import asyncio

    from control import local_access

    cfg = owner_config(tmp_path)
    app = create_app(cfg)

    async def scenario():
        async with loopback(app) as client:
            for cookie in (None, "invalid-session"):
                headers = {"Cookie": f"{local_access.COOKIE}={cookie}"} if cookie else {}
                response = await client.get("/v1/version", headers=headers)
                assert response.status_code == 401
                assert response.json()["code"] == "authentication_required"
            session = owner_cookie(cfg)[local_access.COOKIE]
            headers = {"Cookie": f"{local_access.COOKIE}={session}"}
            response = await client.get("/v1/version", headers=headers)
            assert response.status_code == 200
            assert response.json()["product"] == "keepharness"
            local_access.revoke_sessions(Path(cfg["control_state_dir"]))
            assert (await client.get("/v1/version", headers=headers)).status_code == 401

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
    app.state.service.serve_peer_check = serve_from_tailscaled
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
            bearer = {"Authorization": "Bearer token-guest", "Host": "attacker.test"}
            assert await who(client, headers=bearer) == (200, ["token-guest-job"])

    try:
        asyncio.run(scenario())
    finally:
        app.state.service.db.close()

    unshared = seeded_owner_app(owner_config(tmp_path / "unshared", browser_url=None))
    unshared.state.service.serve_peer_check = serve_from_tailscaled

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
    app.state.service.serve_peer_check = serve_from_tailscaled
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
    guest = {"Authorization": "Bearer token-guest"}

    async def scenario():
        async with loopback(app) as client:
            created = await client.post(
                "/v1/projects", json={"root": str(root), "label": "Host"}, cookies=owner
            )
            assert created.status_code == 201, created.text
            pid = created.json()["project_id"]
            # Shared on purpose here, so only the new gate stands between a guest and the folder.
            assert pid in (await client.get("/v1/projects", headers=guest)).json()["projects"]
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
                response = await client.request(method, path, params=params, json=data, headers=guest)
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
                await client.get("/v1/projects", headers={"Authorization": "Bearer token-guest"})
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
        assert pid not in clients["token-guest"]["projects"]
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
                ("guest", {"headers": {"Authorization": "Bearer token-guest"}}),
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
                "/v1/project-directories", headers={"Authorization": "Bearer token-guest"}
            )
            assert guest_dirs.status_code == 403

    try:
        asyncio.run(scenario())
    finally:
        app.state.service.db.close()


def test_funnel_request_is_refused(tmp_path):
    import asyncio

    import httpx

    from control import local_access
    from control.server import create_app as create_admin_app

    cfg = owner_config(tmp_path)
    app = seeded_owner_app(cfg)
    app.state.service.serve_peer_check = serve_from_tailscaled
    funnel = {"Tailscale-Funnel-Request": "?1"}
    serve = {"Tailscale-User-Login": GUEST_LOGIN, **SERVE_HEADERS}

    async def scenario():
        async with loopback(app) as client:
            assert await who(client, headers=serve) == (200, ["tailnet-guest-job"])
            # Funnel traffic reaches the harness through the same proxy; whatever else it
            # carries, it never authenticates (not by login, owner session or token).
            for headers, cookies in (
                ({**serve, **funnel}, None),
                (funnel, owner_cookie(cfg)),
                ({**funnel, "Authorization": "Bearer token-guest"}, None),
            ):
                assert await who(client, headers=headers, cookies=cookies) == (
                    403,
                    "funnel_denied",
                )

    try:
        asyncio.run(scenario())
    finally:
        app.state.service.db.close()

    admin = create_admin_app(tmp_path / "admin", 8094)

    async def admin_scenario():
        transport = httpx.ASGITransport(app=admin, client=("127.0.0.1", 4321))
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8094") as c:
            session = {local_access.COOKIE: local_access.issue_session(admin.state.manager.state)}
            assert (await c.get("/", cookies=session)).status_code == 200
            assert (await c.get("/", cookies=session, headers=funnel)).status_code == 403
            assert (await c.get("/api/state", cookies=session, headers=funnel)).status_code == 403

    asyncio.run(admin_scenario())


def peer(app, address="127.0.0.1"):
    import httpx

    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=(address, 4321)),
        base_url="http://127.0.0.1:8095",
    )


def test_tokens_never_substitute_for_a_tailnet_identity(tmp_path):
    """A bearer header or harness_token cookie identifies only a direct loopback peer (F4/F5)."""
    import asyncio

    cfg = owner_config(tmp_path)
    app = seeded_owner_app(cfg)
    app.state.service.serve_peer_check = serve_from_tailscaled
    bearer = {"Authorization": "Bearer tailnet-guest"}
    cookie = {"harness_token": "tailnet-guest"}
    refused = (401, "authentication_required")
    forwarded = (
        SERVE_HEADERS,
        {"X-Forwarded-For": "100.101.102.103"},
        {"Tailscale-User-Login": GUEST_LOGIN},
        {"Tailscale-Name": "Guest"},
    )

    async def scenario():
        async with loopback(app) as client:
            assert await who(client, headers=bearer) == (200, ["tailnet-guest-job"])
            assert await who(client, cookies=cookie) == (200, ["tailnet-guest-job"])
            for extra in forwarded:
                assert await who(client, headers={**bearer, **extra}) == refused, extra
                assert await who(client, cookies=cookie, headers=extra) == refused, extra
            # The retired shared key: no client carries it any more.
            assert await who(client, headers={"Authorization": "Bearer vpn"}) == refused
        for address in ("100.101.102.103", "192.168.1.20", "::2"):
            async with peer(app, address) as client:
                assert await who(client, headers=bearer) == refused, address
                assert await who(client, cookies=cookie) == refused, address

    try:
        asyncio.run(scenario())
    finally:
        app.state.service.db.close()


def test_login_accepts_a_token_only_from_a_direct_loopback_peer(tmp_path):
    import asyncio

    app = seeded_owner_app(owner_config(tmp_path))
    app.state.service.serve_peer_check = serve_from_tailscaled
    origin = {"Origin": "http://127.0.0.1:8095"}
    token = {"token": "tailnet-guest"}

    async def scenario():
        async with loopback(app) as client:
            ok = await client.post("/v1/login", json=token, headers=origin)
            assert ok.status_code == 200 and "harness_token" in ok.headers["set-cookie"]
            for extra in (SERVE_HEADERS, {"Tailscale-User-Login": GUEST_LOGIN}):
                response = await client.post("/v1/login", json=token, headers={**origin, **extra})
                assert (response.status_code, "set-cookie" in response.headers) == (401, False)
            retired = await client.post("/v1/login", json={"token": "vpn"}, headers=origin)
            assert retired.status_code == 401
        async with peer(app, "100.101.102.103") as client:
            response = await client.post("/v1/login", json=token, headers=origin)
            assert (response.status_code, "set-cookie" in response.headers) == (401, False)

    try:
        asyncio.run(scenario())
    finally:
        app.state.service.db.close()


def test_direct_loopback_is_one_helper_for_the_harness_and_the_admin(tmp_path, monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from starlette.datastructures import Headers

    from control import local_access

    def request(host, **headers):
        return SimpleNamespace(client=SimpleNamespace(host=host), headers=Headers(headers))

    assert local_access.direct_loopback(request("127.0.0.1"))
    assert local_access.direct_loopback(request("::1", Accept="*/*"))
    assert not local_access.direct_loopback(SimpleNamespace(client=None, headers=Headers({})))
    assert not local_access.direct_loopback(request("100.101.102.103"))
    for name in ("X-Forwarded-For", "Forwarded", "X-Real-IP", "Tailscale-Foo", "TAILSCALE-APP"):
        assert not local_access.direct_loopback(request("127.0.0.1", **{name: "x"})), name

    app = seeded_owner_app(owner_config(tmp_path))
    origin = {"Origin": "http://127.0.0.1:8095"}

    async def scenario():
        async with loopback(app) as client:
            monkeypatch.setattr(local_access, "direct_loopback", lambda request: False)
            response = await client.post(
                "/v1/login", json={"token": "tailnet-guest"}, headers=origin
            )
            assert response.status_code == 401

    try:
        asyncio.run(scenario())
    finally:
        app.state.service.db.close()


def test_remote_gate_is_owner_only_and_fails_closed(tmp_path):
    import asyncio

    login = {"Tailscale-User-Login": GUEST_LOGIN, **SERVE_HEADERS}
    refused = (401, "authentication_required")
    cfg = owner_config(tmp_path / "listed")
    app = seeded_owner_app(cfg)
    empty = seeded_owner_app(owner_config(tmp_path / "empty", tailscale_logins={}))
    foreign = seeded_owner_app(owner_config(tmp_path / "foreign"))
    off_cfg = owner_config(tmp_path / "off", local_access=False)
    off = seeded_owner_app(off_cfg)
    for served in (app, empty, off):
        served.state.service.serve_peer_check = serve_from_tailscaled
    foreign.state.service.serve_peer_check = lambda client, port: False

    async def scenario():
        owner = owner_cookie(cfg)
        async with loopback(app) as client:
            # An allow-listed login, through Serve, on a socket tailscaled owns.
            assert await who(client, headers=login) == (200, ["tailnet-guest-job"])
            unlisted = {**login, "Tailscale-User-Login": "stranger@example.test"}
            assert await who(client, headers=unlisted) == refused
            # `local` needs the owner's session, a loopback peer and a loopback Host.
            assert await who(client, cookies=owner) == (200, ["local-job"])
            assert await who(client) == refused
        async with peer(app, "100.101.102.103") as client:
            assert await who(client, cookies=owner) == refused
        async with loopback(empty) as client:
            assert await who(client, headers=login) == refused  # empty allow list
        async with loopback(foreign) as client:
            assert await who(client, headers=login) == refused  # headers from a foreign socket
        async with loopback(off) as client:
            assert await who(client, cookies=owner_cookie(off_cfg)) == refused  # local_access off

    try:
        asyncio.run(scenario())
    finally:
        for served in (app, empty, foreign, off):
            served.state.service.db.close()


def test_a_stale_bearer_through_serve_still_gets_the_serve_identity(tmp_path):
    import asyncio

    stale = {"Authorization": "Bearer old-vpn-key"}
    login = {"Tailscale-User-Login": GUEST_LOGIN, **SERVE_HEADERS}
    refused = (401, "authentication_required")
    app = seeded_owner_app(owner_config(tmp_path))
    app.state.service.serve_peer_check = serve_from_tailscaled

    async def scenario():
        async with loopback(app) as client:
            assert await who(client, headers={**stale, **login}) == (200, ["tailnet-guest-job"])
            unlisted = {**login, "Tailscale-User-Login": "stranger@example.test"}
            assert await who(client, headers={**stale, **unlisted}) == refused

    try:
        asyncio.run(scenario())
    finally:
        app.state.service.db.close()


def test_guest_cannot_browse_or_attach_host_files(tmp_path, monkeypatch):
    import asyncio

    home = tmp_path / "home"
    home.mkdir()
    (home / "notes.txt").write_text("the owner's file")
    monkeypatch.setenv("HOME", str(home))
    cfg = owner_config(
        tmp_path,
        uploads_enabled=True,
        services={
            "codex": {
                "enabled": True,
                "models": ["fixture"],
                "projects": ["sem-projeto"],
                "permissions": {"read": True, "upload": True},
            }
        },
        codex_models={"fixture": ["low"]},
    )
    app = create_app(cfg)
    owner = owner_cookie(cfg)
    guest = {"Authorization": "Bearer token-guest"}
    attach = "/v1/project-files/attach?project_id=sem-projeto&backend=codex&model=fixture"
    from_root = (home / "notes.txt").relative_to("/").as_posix()

    async def scenario():
        async with loopback(app) as client:
            tree = "/v1/project-files?view=tree&root_id=home"
            attempts = (
                await client.get(tree, headers=guest),
                await client.get("/v1/project-files?view=tree", headers=guest),
                await client.post(
                    attach, json={"root_id": "home", "paths": ["notes.txt"]}, headers=guest
                ),
                # The attach root used to default to the filesystem root, which reaches the same file.
                await client.post(attach, json={"paths": [from_root]}, headers=guest),
                await client.post(
                    attach, json={"root_id": "system", "paths": [from_root]}, headers=guest
                ),
            )
            for response in attempts:
                assert (response.status_code, response.json()["code"]) == (
                    403,
                    "host_files_owner_only",
                ), response.text
            listing = await client.get(tree, cookies=owner)
            assert [entry["name"] for entry in listing.json()["entries"]] == ["notes.txt"]
            owned = await client.post(
                attach, json={"root_id": "home", "paths": ["notes.txt"]}, cookies=owner
            )
            assert owned.status_code == 200, owned.text
            assert [item["name"] for item in owned.json()["attachments"]] == ["notes.txt"]
            default = await client.post(attach, json={"paths": ["notes.txt"]}, cookies=owner)
            assert [item["name"] for item in default.json()["attachments"]] == ["notes.txt"]
            # Not even the owner browses the filesystem root any more.
            gone = await client.post(
                attach, json={"root_id": "system", "paths": [from_root]}, cookies=owner
            )
            assert (gone.status_code, gone.json()["code"]) == (422, "system_root_denied")

    try:
        asyncio.run(scenario())
    finally:
        app.state.service.db.close()


def test_cross_site_login_refused_before_limit(api):
    client, _, _ = api
    hostile = {"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"}
    for _ in range(25):
        response = client.post("/v1/login", content='{"token":"x"}', headers=hostile)
        assert response.status_code == 403, response.text
        assert response.json()["code"] == "origin_denied"
    # The hostile loop spent none of the shared budget a real device needs.
    ok = client.post(
        "/v1/login", json={"token": "alice"}, headers={"Origin": "http://testserver"}
    )
    assert ok.status_code == 200, ok.text


def test_v1_no_store_and_foreign_404(api, monkeypatch):
    monkeypatch.setattr("agent_service.harness_agents.LOCAL_CLIENT", "alice")  # usage is owner-only (D-032)
    client, service, _ = api
    job = seed_job(service)
    for path in ("/v1/projects", "/v1/conversations", "/v1/models", "/v1/usage"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert response.headers["cache-control"] == "no-store", path
    missing = client.get("/v1/jobs/does-not-exist", headers={"Authorization": "Bearer bob"})
    foreign = client.get("/v1/jobs/" + job, headers={"Authorization": "Bearer bob"})
    assert missing.status_code == foreign.status_code == 404
    assert foreign.json()["code"] == missing.json()["code"] == "job_not_found"
    assert foreign.headers["cache-control"] == "no-store"
    assert client.get("/v1/jobs/" + job).status_code == 200
