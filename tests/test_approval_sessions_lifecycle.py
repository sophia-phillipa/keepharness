"""Session lifecycle, enrollment safeguards and request-path storage boundaries."""

import asyncio
import hashlib
import json
import os
import sqlite3
from pathlib import Path

import httpx
import pytest

from agent_service import approval_sessions
from agent_service.app import create_app
from agent_service.errors import APIError

ORIGIN = "http://127.0.0.1:18095"


@pytest.fixture
def session_app(make_harness_config):
    app = create_app(
        make_harness_config(
            clients={
                owner: {
                    "sha256": hashlib.sha256(owner.encode()).hexdigest(),
                    "projects": ["sem-projeto"],
                }
                for owner in ("local", "other")
            }
        )
    )
    yield app
    app.state.service.db.close()


def browser(app, token=None):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 12345)),
        base_url=ORIGIN,
        cookies={"harness_session": token} if token else {},
    )


def enroll(app, owner="local"):
    config = app.state.service.config
    return approval_sessions.consume_enrollment(
        config, approval_sessions.issue_enrollment(config, owner)
    )


def test_approval_lookup_is_one_select_without_permission_or_schema_writes(
    session_app, monkeypatch
):
    token = enroll(session_app)
    statements = []
    original = sqlite3.connect

    def traced(*args, **kwargs):
        database = original(*args, **kwargs)
        database.set_trace_callback(statements.append)
        return database

    def forbidden(*args, **kwargs):
        raise AssertionError("Request authentication must not repair or initialize storage")

    monkeypatch.setattr(sqlite3, "connect", traced)
    monkeypatch.setattr(Path, "mkdir", forbidden)
    monkeypatch.setattr(Path, "chmod", forbidden)
    monkeypatch.setattr(os, "fchmod", forbidden)
    monkeypatch.setattr(os, "open", forbidden)

    async def scenario():
        async with browser(session_app, token) as client:
            response = await client.post("/v1/approvals/missing", json={"approved": True})
            assert response.status_code == 404
            assert response.json()["code"] == "approval_expired"

    asyncio.run(scenario())
    assert statements == [
        "SELECT digest, owner, expires, approval_capable FROM sessions",
        next(
            statement
            for statement in statements
            if statement.startswith("SELECT owner FROM sessions")
        ),
    ]


def test_unknown_cookie_is_throttled_before_database_lookup(session_app, monkeypatch):
    from agent_service.services import conversation_service

    count = 0

    def lookup(*args):
        nonlocal count
        count += 1
        return None

    monkeypatch.setattr(conversation_service, "session_identity", lookup)

    async def scenario():
        async with browser(session_app, "forged") as client:
            for _ in range(260):
                response = await client.post("/v1/approvals/missing", json={"approved": True})
            assert response.status_code == 429
            assert response.json()["code"] == "session_rate_limit"

    asyncio.run(scenario())
    assert count <= 240


@pytest.mark.parametrize("error", [OSError("unsupported"), NotImplementedError("unsupported")])
def test_permission_repair_failures_are_controlled(session_app, monkeypatch, error):
    def fail(*args):
        raise error

    monkeypatch.setattr(os, "fchmod", fail)
    with pytest.raises(APIError, match="approval_storage_unsafe"):
        approval_sessions.issue_enrollment(session_app.state.service.config, "local")


def test_symlinked_journal_is_rejected_without_touching_target(session_app, tmp_path):
    config = session_app.state.service.config
    enroll(session_app)
    target = tmp_path / "unrelated.txt"
    target.write_text("keep")
    target.chmod(0o644)
    journal = Path(config["state_dir"]) / "approval_sessions.sqlite3-journal"
    journal.symlink_to(target)
    with pytest.raises(APIError, match="approval_storage_unsafe"):
        approval_sessions.issue_enrollment(config, "local")
    assert target.read_text() == "keep"
    assert target.stat().st_mode & 0o777 == 0o644


@pytest.mark.parametrize("all_owners", [False, True])
def test_cli_revocation_removes_sessions_and_pending_enrollments(
    session_app, tmp_path, capsys, all_owners
):
    from control.cli import main

    config = session_app.state.service.config
    tokens = {owner: enroll(session_app, owner) for owner in ("local", "other")}
    nonces = {owner: approval_sessions.issue_enrollment(config, owner) for owner in tokens}
    (tmp_path / "runtime.json").write_text(json.dumps(config))
    target = ["--all"] if all_owners else ["--owner", "local"]
    main(["--state", str(tmp_path), "approve-device", "--revoke", *target])
    assert "Revoked" in capsys.readouterr().out

    async def scenario():
        config["local_access"] = False
        for owner, token in tokens.items():
            revoked = all_owners or owner == "local"
            async with browser(session_app, token) as client:
                response = await client.get("/v1/conversations")
                assert response.status_code == (401 if revoked else 200)
            if revoked:
                with pytest.raises(APIError, match="approval_enrollment_invalid"):
                    approval_sessions.consume_enrollment(config, nonces[owner])
            else:
                assert approval_sessions.consume_enrollment(config, nonces[owner])

    asyncio.run(scenario())


def test_logout_revokes_only_current_session_and_expires_cookies(session_app):
    first = enroll(session_app)
    second = enroll(session_app)

    async def scenario():
        async with browser(session_app, first) as client:
            response = await client.post("/v1/logout", headers={"Origin": ORIGIN})
            assert response.status_code == 200
            cookies = response.headers.get_list("set-cookie")
            assert any("harness_session=" in cookie and "Max-Age=0" in cookie for cookie in cookies)
            assert any("harness_token=" in cookie and "Max-Age=0" in cookie for cookie in cookies)
        session_app.state.service.config["local_access"] = False
        for token, status in ((first, 401), (second, 200)):
            async with browser(session_app, token) as client:
                assert (await client.get("/v1/conversations")).status_code == status

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "tty,answer,yes,allowed",
    [
        (False, None, False, False),
        (False, None, True, True),
        (True, "no", False, False),
        (True, "yes", False, True),
    ],
)
def test_cli_enrollment_requires_tty_confirmation_or_yes(
    session_app, tmp_path, monkeypatch, capsys, tty, answer, yes, allowed
):
    import sys

    from control.cli import main

    config = session_app.state.service.config
    (tmp_path / "runtime.json").write_text(json.dumps(config))
    monkeypatch.setattr(sys.stdin, "isatty", lambda: tty)
    prompts = []

    def confirmation(prompt):
        prompts.append(prompt)
        return answer

    monkeypatch.setattr("builtins.input", confirmation)
    args = ["--state", str(tmp_path), "approve-device", "--owner", "local"]
    if yes:
        args.append("--yes")
    if allowed:
        main(args)
    else:
        with pytest.raises(SystemExit):
            main(args)
    output = capsys.readouterr().out
    assert ("/approve-device?nonce=" in output) is allowed
    with approval_sessions.session_database(config, readonly=True) as database:
        assert database.execute("SELECT COUNT(*) FROM enrollments").fetchone()[0] == int(allowed)
    if tty and not yes:
        assert "local" in prompts[0]


@pytest.mark.parametrize(
    "browser_url,forwarded,secure",
    [
        ("https://harness.invalid", "http", True),
        (ORIGIN, "https", False),
    ],
)
def test_proxy_cookies_use_configured_browser_scheme(session_app, browser_url, forwarded, secure):
    config = session_app.state.service.config
    config["browser_url"] = browser_url
    config["origins"].append(browser_url)
    nonce = approval_sessions.issue_enrollment(config, "local")

    async def scenario():
        async with browser(session_app) as client:
            headers = {"Origin": browser_url, "X-Forwarded-Proto": forwarded}
            responses = [
                await client.post("/v1/login", json={"token": "local"}, headers=headers),
                await client.post("/approve-device?nonce=" + nonce, headers=headers),
                await client.post("/v1/logout", headers=headers),
            ]
            assert [response.status_code for response in responses] == [200, 303, 200]
            for response in responses:
                for cookie in response.headers.get_list("set-cookie"):
                    assert ("Secure" in cookie) is secure

    asyncio.run(scenario())
