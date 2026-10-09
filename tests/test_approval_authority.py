"""Only owner-enrolled browser sessions may resolve native approvals."""

import asyncio
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from urllib.parse import urlsplit

import httpx
import pytest

from agent_service.app import create_app

ORIGIN = "http://127.0.0.1:18095"
# Tailscale Serve's origin: the only Host on which the login header names a client.
REMOTE = "http://owner-host.example.ts.net:8093"
TAILNET = {
    "Tailscale-User-Login": "owner@example.com",
    "Host": "owner-host.example.ts.net:8093",
    # What Serve adds when it proxies a tailnet peer (ipn/ipnlocal/serve.go).
    "X-Forwarded-Host": "owner-host.example.ts.net:8093",
    "X-Forwarded-For": "100.101.102.103",
}
TAILNET_OWNER = "tailnet-" + hashlib.sha256(b"owner@example.com").hexdigest()[:16]


@pytest.fixture
def approval_app(make_harness_config):
    clients = {
        owner: {"sha256": hashlib.sha256(token.encode()).hexdigest(), "projects": ["sem-projeto"]}
        for owner, token in (("local", "local-token"), (TAILNET_OWNER, "tailnet-token"))
    }
    app = create_app(
        make_harness_config(
            clients=clients,
            tailscale_logins={"owner@example.com": "local"},
            browser_url=REMOTE + "/",
            origins=[ORIGIN, REMOTE],
        )
    )
    # The socket proof has its own tests (tests/test_serve_proof.py); here Host and headers count.
    app.state.service.serve_peer_check = lambda client, port: True
    yield app
    app.state.service.db.close()


def pending_approval(app, owner, job="job", parent=None):
    service = app.state.service
    with service.db:
        service.conversation_repository.insert(
            job,
            "sem-projeto",
            owner,
            "running",
            1,
            json.dumps({"parent_job_id": parent}),
            None,
            job,
            job,
        )
    pending = asyncio.get_running_loop().create_future()
    service.approvals[job] = (job, pending)
    return pending


def client_for(app, **kwargs):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 42000)),
        base_url=ORIGIN,
        **kwargs,
    )


@pytest.mark.parametrize("credential", ["localhost", "bearer", "login", "tailnet"])
def test_worker_credentials_cannot_resolve(approval_app, credential):
    async def scenario():
        pending = pending_approval(approval_app, "local")
        async with client_for(approval_app) as client:
            if credential == "bearer":
                client.headers["Authorization"] = "Bearer local-token"
            elif credential == "login":
                login = await client.post(
                    "/v1/login", json={"token": "local-token"}, headers={"Origin": ORIGIN}
                )
                assert login.status_code == 200
                assert "harness_session" not in client.cookies
            elif credential == "tailnet":
                client.headers.update(TAILNET)
            response = await client.post("/v1/approvals/job", json={"approved": True})
            assert response.status_code == 403
            assert response.json()["code"] == "approval_session_required"
            assert not pending.done()

    asyncio.run(scenario())


@pytest.mark.parametrize("owner", ["local", TAILNET_OWNER])
def test_enrolled_owner_preserves_history_and_approves_across_lineages(approval_app, owner):
    from agent_service.approval_sessions import issue_enrollment

    async def scenario():
        first = pending_approval(approval_app, owner, "ancestor")
        child = pending_approval(approval_app, owner, "child", "ancestor")
        unrelated = pending_approval(approval_app, owner, "unrelated")
        nonce = issue_enrollment(approval_app.state.service.config, owner)
        async with client_for(approval_app) as client:
            url = "/approve-device?nonce=" + nonce
            page = await client.get(url)
            assert page.status_code == 200
            assert "harness_session" not in client.cookies
            response = await client.post(url, headers={"Origin": ORIGIN})
            assert response.status_code == 303
            assert "HttpOnly" in response.headers["set-cookie"]
            assert "SameSite=strict" in response.headers["set-cookie"]
            for job in ("ancestor", "child", "unrelated"):
                assert (await client.get("/v1/jobs/" + job)).status_code == 200
                response = await client.post("/v1/approvals/" + job, json={"approved": True})
                assert response.status_code == 200
            assert all(item.result()["approved"] for item in (first, child, unrelated))
            replay = await client.post(url, headers={"Origin": ORIGIN})
            assert replay.status_code == 403

    asyncio.run(scenario())


def test_enrolled_owner_cannot_approve_another_owner(approval_app):
    from agent_service.approval_sessions import issue_enrollment

    async def scenario():
        pending = pending_approval(approval_app, TAILNET_OWNER)
        nonce = issue_enrollment(approval_app.state.service.config, "local")
        async with client_for(approval_app) as client:
            await client.post("/approve-device?nonce=" + nonce, headers={"Origin": ORIGIN})
            response = await client.post("/v1/approvals/job", json={"approved": True})
            assert response.status_code == 404
            assert not pending.done()

    asyncio.run(scenario())


def test_enrollment_rejects_tokens_and_cross_site_without_consuming_nonce(approval_app):
    from agent_service.approval_sessions import issue_enrollment

    async def scenario():
        nonce = issue_enrollment(approval_app.state.service.config, "local")
        async with client_for(approval_app) as client:
            for headers in (
                {},
                {"Origin": "https://evil.invalid"},
                {"Origin": ORIGIN, "Sec-Fetch-Site": "cross-site"},
            ):
                response = await client.post("/approve-device?nonce=" + nonce, headers=headers)
                assert response.status_code == 403
            for token in ("local-token", "tailnet-token", "", "fake"):
                response = await client.post(
                    "/approve-device?nonce=" + token, headers={"Origin": ORIGIN}
                )
                assert response.status_code == 403
            assert (
                await client.post("/approve-device?nonce=" + nonce, headers={"Origin": ORIGIN})
            ).status_code == 303

    asyncio.run(scenario())


def test_nonce_is_atomic_and_secrets_are_not_stored_plaintext(approval_app):
    from pathlib import Path

    from agent_service.approval_sessions import consume_enrollment, issue_enrollment
    from agent_service.errors import APIError

    config = approval_app.state.service.config
    nonce = issue_enrollment(config, "local")

    def redeem():
        try:
            return consume_enrollment(config, nonce)
        except APIError:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: redeem(), range(2)))
    tokens = [value for value in results if value]
    assert len(tokens) == 1
    files = list(Path(config["state_dir"]).glob("approval_sessions.sqlite3*"))
    assert files
    for path in files:
        assert path.stat().st_mode & 0o077 == 0
        assert nonce.encode() not in path.read_bytes()
        assert tokens[0].encode() not in path.read_bytes()


def test_mcp_cannot_resolve_any_lineage(monkeypatch):
    from agent_service import mcp_bridge

    async def forbidden(*args, **kwargs):
        raise AssertionError("MCP must not send approval resolution to HTTP")

    monkeypatch.setattr(mcp_bridge, "call", forbidden)
    for job in ("self", "ancestor", "unrelated"):
        result = asyncio.run(
            mcp_bridge.mcp.call_tool("resolve_approval", {"approval_id": job, "approved": True})
        )
        assert result.isError is True
        body = json.loads(result.content[0].text)
        assert body["http_status"] == 403
        assert body["error"]["code"] == "approval_session_required"


def test_cli_enrolls_existing_owner_only(approval_app, tmp_path, capsys):
    from control.cli import main

    config = approval_app.state.service.config
    (tmp_path / "runtime.json").write_text(json.dumps(config))
    main(["--state", str(tmp_path), "approve-device", "--owner", "owner@example.com", "--yes"])
    link = capsys.readouterr().out.strip().splitlines()[-1]
    assert urlsplit(link).path == "/approve-device"

    async def scenario():
        pending = pending_approval(approval_app, "local")
        async with client_for(approval_app) as client:
            # With tailnet sharing on, the CLI prints the link on the remote browser origin.
            assert urlsplit(link).netloc == urlsplit(REMOTE).netloc
            assert (await client.post(link, headers={"Origin": REMOTE})).status_code == 303
            assert (
                await client.post(REMOTE + "/v1/approvals/job", json={"approved": True})
            ).status_code == 200
            assert pending.result()["approved"]

    asyncio.run(scenario())
    with pytest.raises(SystemExit):
        main(["--state", str(tmp_path), "approve-device", "--owner", "unknown"])


def test_expired_session_row_survives_enrollment_for_the_grace_period(approval_app, monkeypatch):
    from types import SimpleNamespace

    from agent_service import approval_sessions

    now = approval_sessions.time.time()
    monkeypatch.setattr(approval_sessions.time, "time", lambda: now)
    config = approval_app.state.service.config
    token = approval_sessions.consume_enrollment(
        config, approval_sessions.issue_enrollment(config, "local")
    )
    request = SimpleNamespace(cookies={approval_sessions.SESSION_COOKIE: token})
    now += approval_sessions.SESSION_SECONDS + 1
    approval_sessions.consume_enrollment(
        config, approval_sessions.issue_enrollment(config, "local")
    )
    assert approval_sessions.session_expired(request, config, "local")  # not purged yet
    now += approval_sessions.EXPIRED_GRACE_SECONDS
    approval_sessions.consume_enrollment(
        config, approval_sessions.issue_enrollment(config, "local")
    )
    assert not approval_sessions.session_expired(request, config, "local")  # purged after grace


def test_expired_enrollment_and_session_fail_closed(approval_app, monkeypatch):
    from agent_service import approval_sessions
    from agent_service.errors import APIError

    now = approval_sessions.time.time()
    monkeypatch.setattr(approval_sessions.time, "time", lambda: now)
    config = approval_app.state.service.config
    expired_nonce = approval_sessions.issue_enrollment(config, "local")
    valid_nonce = approval_sessions.issue_enrollment(config, "local")
    token = approval_sessions.consume_enrollment(config, valid_nonce)
    now += approval_sessions.ENROLLMENT_SECONDS + 1
    with pytest.raises(APIError, match="approval_enrollment_invalid"):
        approval_sessions.consume_enrollment(config, expired_nonce)
    now += approval_sessions.SESSION_SECONDS

    async def scenario():
        pending = pending_approval(approval_app, "local")
        async with client_for(approval_app, cookies={"harness_session": token}) as client:
            response = await client.post("/v1/approvals/job", json={"approved": True})
            assert response.status_code == 403
            assert not pending.done()

    asyncio.run(scenario())


def test_https_cookie_is_secure_and_owner_removal_revokes_session(approval_app):
    from agent_service.approval_sessions import issue_enrollment

    config = approval_app.state.service.config
    config["origins"].append("https://harness.invalid")
    nonce = issue_enrollment(config, TAILNET_OWNER)

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=approval_app, client=("203.0.113.1", 42000)),
            base_url="https://harness.invalid",
        ) as client:
            response = await client.post(
                "/approve-device?nonce=" + nonce, headers={"Origin": "https://harness.invalid"}
            )
            assert response.status_code == 303
            assert "Secure" in response.headers["set-cookie"]
            assert (await client.get("/v1/conversations")).status_code == 200
            del config["clients"][TAILNET_OWNER]
            assert (await client.get("/v1/conversations")).status_code == 401

    asyncio.run(scenario())


def test_forged_cookie_and_session_bearer_cannot_approve(approval_app):
    from agent_service.approval_sessions import consume_enrollment, issue_enrollment

    config = approval_app.state.service.config
    token = consume_enrollment(config, issue_enrollment(config, "local"))

    async def scenario():
        pending = pending_approval(approval_app, "local")
        async with client_for(approval_app, cookies={"harness_session": "local-token"}) as client:
            response = await client.post(
                "/v1/approvals/job", json={"approved": True, "approval_capable": True}
            )
            assert response.status_code == 403
            client.cookies.clear()
            client.headers["Authorization"] = "Bearer " + token
            response = await client.post("/v1/approvals/job", json={"approved": True})
            assert response.status_code == 401
            assert not pending.done()

    asyncio.run(scenario())


def test_enrollment_repairs_existing_permissive_state(approval_app):
    import os
    from pathlib import Path

    from agent_service.approval_sessions import issue_enrollment

    config = approval_app.state.service.config
    root = Path(config["state_dir"])
    database = root / "approval_sessions.sqlite3"
    issue_enrollment(config, "local")
    root.chmod(0o777)
    database.chmod(0o666)
    sidecars = [Path(str(database) + suffix) for suffix in ("-journal", "-wal", "-shm")]
    for path in sidecars:
        path.touch()
        path.chmod(0o666)
    previous_umask = os.umask(0)
    try:
        issue_enrollment(config, "local")
    finally:
        os.umask(previous_umask)
    assert root.stat().st_mode & 0o777 == 0o700
    assert database.stat().st_mode & 0o777 == 0o600
    for path in sidecars:
        if path.exists():
            assert path.stat().st_mode & 0o077 == 0


@pytest.mark.parametrize("remove_pending", [False, True])
def test_approval_expiring_during_body_read_rejects_late_reply(approval_app, remove_pending):
    from agent_service.approval_sessions import issue_enrollment

    async def scenario():
        pending = pending_approval(approval_app, "local")
        nonce = issue_enrollment(approval_app.state.service.config, "local")
        reading = asyncio.Event()
        release = asyncio.Event()

        async def delayed_body():
            reading.set()
            await release.wait()
            yield b'{"approved":true}'

        async with client_for(approval_app) as client:
            await client.post("/approve-device?nonce=" + nonce, headers={"Origin": ORIGIN})
            response_task = asyncio.create_task(
                client.post("/v1/approvals/job", content=delayed_body())
            )
            await asyncio.wait_for(reading.wait(), 1)
            pending.cancel()
            if remove_pending:
                approval_app.state.service.approvals.pop("job")
            release.set()
            response = await response_task
            assert response.status_code == 404
            assert response.json()["code"] == "approval_expired"

    asyncio.run(scenario())


@pytest.mark.parametrize("credential", ["bearer", "tailnet"])
def test_a_refused_approval_names_the_owner_to_enroll(approval_app, credential):
    owner = "local"  # an allow-listed Serve login is the owner too (D-040)

    async def scenario():
        pending_approval(approval_app, owner)
        async with client_for(approval_app) as client:
            if credential == "bearer":
                client.headers["Authorization"] = "Bearer local-token"
            else:
                client.headers.update(TAILNET)
            response = await client.post("/v1/approvals/job", json={"approved": True})
        assert response.status_code == 403
        body = response.json()
        # The id `keepharness approve-device --owner <id>` accepts, for the caller only.
        assert (body["code"], body["owner"]) == ("approval_session_required", owner)
        assert "login" not in body

    asyncio.run(scenario())


def ceiling_config(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    clients = {
        owner: {"sha256": hashlib.sha256(token.encode()).hexdigest(), "projects": ["p"]}
        for owner, token in (
            ("local", "local-token"),
            (TAILNET_OWNER, "tailnet-token"),
        )
    }
    grants = {"read": True, "write": True, "shell": True, "internet": True, "hooks": True}
    return {
        "state_dir": str(tmp_path / "state"),
        "projects": {"p": {"root": str(root)}},
        "clients": clients,
        "tailscale_logins": {"owner@example.com": "local"},
        "services": {
            "codex": {
                "enabled": True,
                "mode": "native",
                "models": ["gpt-6-astra"],
                "projects": ["p"],
                "permissions": grants,
                "integrations": ["mcp:node_repl"],
            }
        },
        "codex": {"binary": "fixture", "unrestricted": True, "integrations": ["mcp:node_repl"]},
        "codex_models": {"gpt-6-astra": ["low"]},
        "origins": [ORIGIN],
        "full_access": True,  # the owner turned Full access on (D11)
    }


SEVEN_DAYS = 7 * 24 * 60 * 60
LEGACY_SESSIONS_SCHEMA = """
    DROP TABLE sessions;
    CREATE TABLE sessions(
        digest TEXT PRIMARY KEY, owner TEXT NOT NULL, expires REAL NOT NULL,
        approval_capable INTEGER NOT NULL
    );
"""


def test_approval_session_expires_after_seven_days_absolute(approval_app, monkeypatch):
    from types import SimpleNamespace

    from agent_service import approval_sessions

    now = 1_000_000.0
    monkeypatch.setattr(approval_sessions.time, "time", lambda: now)
    config = approval_app.state.service.config
    token = approval_sessions.consume_enrollment(
        config, approval_sessions.issue_enrollment(config, "local")
    )
    request = SimpleNamespace(cookies={approval_sessions.SESSION_COOKIE: token})
    assert approval_sessions.SESSION_SECONDS == SEVEN_DAYS
    now += SEVEN_DAYS - 1
    assert approval_sessions.session_identity(request, config) == "local"
    # Use never extends the session: the limit counts from creation, with no idle timeout.
    now += 1
    assert approval_sessions.session_identity(request, config) is None


def test_legacy_thirty_day_session_clamped_at_startup(approval_app, monkeypatch):
    from agent_service import approval_sessions

    now = 1_000_000.0
    monkeypatch.setattr(approval_sessions.time, "time", lambda: now)
    config = approval_app.state.service.config
    legacy = 30 * 24 * 60 * 60
    with approval_sessions.session_database(config) as database:
        database.executescript(LEGACY_SESSIONS_SCHEMA)
        # Issued now, and issued 20 days ago: legacy rows only stored the 30-day expiry.
        database.execute("INSERT INTO sessions VALUES('fresh','local',?,1)", (now + legacy,))
        database.execute(
            "INSERT INTO sessions VALUES('old','local',?,1)", (now - 20 * 86400 + legacy,)
        )
    approval_sessions.initialize_session_database(config)
    approval_sessions.initialize_session_database(config)  # idempotent
    with approval_sessions.session_database(config, readonly=True) as database:
        expires = dict(database.execute("SELECT digest, expires FROM sessions"))
    assert expires["fresh"] == now + SEVEN_DAYS
    assert expires["old"] == now - 20 * 86400 + SEVEN_DAYS < now


def test_session_without_creation_time_is_clamped_and_a_lost_alter_race_is_tolerated(
    approval_app, monkeypatch
):
    from agent_service import approval_sessions

    now = 1_000_000.0
    monkeypatch.setattr(approval_sessions.time, "time", lambda: now)
    config = approval_app.state.service.config
    legacy = 30 * 24 * 60 * 60
    with approval_sessions.session_database(config) as database:
        database.executescript(LEGACY_SESSIONS_SCHEMA)
        database.execute("ALTER TABLE sessions ADD COLUMN created REAL")  # crash before UPDATE
        database.execute("INSERT INTO sessions VALUES('orphan','local',?,1,NULL)", (now + legacy,))

    real_database = approval_sessions.session_database

    class StaleSchema:
        """Reports the pre-ALTER columns, as a concurrent starter that lost the race sees them."""

        def __init__(self, database):
            self.database = database

        def execute(self, sql, *args):
            if sql.startswith("PRAGMA table_info"):
                return iter([(0, "digest"), (1, "owner"), (2, "expires"), (3, "approval_capable")])
            return self.database.execute(sql, *args)

        def __getattr__(self, name):
            return getattr(self.database, name)

    @contextmanager
    def stale_database(*args, **kwargs):
        with real_database(*args, **kwargs) as database:
            yield StaleSchema(database)

    monkeypatch.setattr(approval_sessions, "session_database", stale_database)
    approval_sessions.initialize_session_database(config)
    monkeypatch.setattr(approval_sessions, "session_database", real_database)
    with approval_sessions.session_database(config, readonly=True) as database:
        expires = dict(database.execute("SELECT digest, expires FROM sessions"))
    assert expires["orphan"] == now + SEVEN_DAYS


def test_expired_session_keeps_card_pending_with_expiry_message(approval_app, monkeypatch):
    import re
    from pathlib import Path

    from agent_service import approval_sessions

    now = approval_sessions.time.time()
    monkeypatch.setattr(approval_sessions.time, "time", lambda: now)
    config = approval_app.state.service.config
    token = approval_sessions.consume_enrollment(
        config, approval_sessions.issue_enrollment(config, "local")
    )
    now += SEVEN_DAYS

    async def scenario():
        pending = pending_approval(approval_app, "local")
        async with client_for(approval_app, cookies={"harness_session": token}) as client:
            response = await client.post("/v1/approvals/job", json={"approved": True})
            assert response.status_code == 403
            assert response.json()["code"] == "approval_session_expired"
            assert response.json()["owner"] == "local"
            assert not pending.done()

    asyncio.run(scenario())
    ui = (Path(approval_sessions.__file__).parent / "ui.js").read_text()
    message = re.search(r'approval_session_expired:\s*"([^"]+)"', ui)
    assert message and "expired" in message.group(1)
