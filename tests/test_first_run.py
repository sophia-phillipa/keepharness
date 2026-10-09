"""First-run wizard backend (issue #69, D-052): status-only scan, marker, legacy rule and guard.

Stub CLIs stand in for Codex, Claude and Gemini; nothing here reaches the network or the real home.
"""

import asyncio
import json
import time
from pathlib import Path

import httpx
import pytest
from starlette.testclient import TestClient

from agent_service import ui_state
from control import discovery, local_access
from control.server import create_app

ADMIN = {"X-Harness-Admin": "1"}


def stub(folder: Path, name: str, body: str) -> str:
    """An executable that logs its argv to ``<folder>/calls.log`` and then runs ``body``."""
    path = folder / name
    path.write_text(f'#!/bin/sh\necho "{name} $*" >> "{folder}/calls.log"\n{body}\n')
    path.chmod(0o755)
    return str(path)


def calls(folder: Path) -> list[str]:
    log = folder / "calls.log"
    return log.read_text().splitlines() if log.exists() else []


@pytest.fixture
def stubs(tmp_path):
    folder = tmp_path / "bin"
    folder.mkdir()
    return folder


@pytest.fixture
def admin(tmp_path):
    app = create_app(str(tmp_path / "state"))
    manager = app.state.manager
    client = TestClient(app, base_url="http://127.0.0.1:8094", headers=ADMIN)
    client.cookies.set(local_access.COOKIE, local_access.issue_session(manager.state))
    client.get("/")
    return client, manager


def inventory(manager, **binaries):
    """An inventory whose binaries are the stubs: found = a path was given."""
    manager.inventory = {
        "services": [
            {"id": name, "found": bool(binaries.get(name)), "binary": binaries.get(name)}
            for name in ("codex", "claude", "gemini", "local", "deepseek")
        ]
    }


def scan(client):
    response = client.post("/api/first-run/scan", json={})
    assert response.status_code == 200, response.text
    return response, {row["id"]: row for row in response.json()["providers"]}


def test_scan_reads_status_through_the_clis_and_leaks_nothing(admin, stubs):
    client, manager = admin
    codex = stub(stubs, "codex", "exit 0")
    claude = stub(stubs, "claude", """echo '{"loggedIn":false,"email":"leak@example.test"}'""")
    inventory(manager, codex=codex, claude=claude)
    response, rows = scan(client)
    assert set(rows) == {"codex", "claude", "gemini", "local", "deepseek"}
    assert (rows["codex"]["found"], rows["codex"]["signed_in"], rows["codex"]["detail"]) == (
        True,
        True,
        "signed_in",
    )
    assert (rows["claude"]["signed_in"], rows["claude"]["detail"]) == (False, "signed_out")
    assert rows["gemini"] == {
        "id": "gemini",
        "found": False,
        "signed_in": False,
        "detail": "not_installed",
    }
    for row in rows.values():
        assert set(row) == {"id", "found", "signed_in", "detail"}
    assert "identity" not in response.text and "leak@example.test" not in response.text
    # `codex login status` is the status read; a bare `login` or any `logout` would be an action.
    assert sorted(calls(stubs)) == ["claude auth status --json", "codex login status"]
    assert manager.auth == {} and manager.provider_models == {}


def test_a_slow_cli_is_a_timeout_not_signed_out(admin, stubs, monkeypatch):
    client, manager = admin
    monkeypatch.setattr(discovery, "COMMAND_SECONDS", 0.3)
    inventory(manager, codex=stub(stubs, "codex", "exec sleep 5"))
    _, rows = scan(client)
    assert (rows["codex"]["found"], rows["codex"]["signed_in"], rows["codex"]["detail"]) == (
        True,
        None,
        "timeout",
    )


def test_a_failing_probe_does_not_fail_the_scan(admin, stubs, monkeypatch):
    client, manager = admin
    inventory(manager, codex=stub(stubs, "codex", "exit 0"))

    async def broken(provider, binary):
        raise RuntimeError("secret detail")

    monkeypatch.setattr(manager, "signed_in", broken)
    response, rows = scan(client)
    assert (rows["codex"]["signed_in"], rows["codex"]["detail"]) == (None, "error")
    assert "secret detail" not in response.text


def test_probes_run_concurrently_and_outside_the_manager_lock(admin, stubs, monkeypatch):
    client, manager = admin
    inventory(manager, codex=stub(stubs, "codex", "exit 0"), claude=stub(stubs, "claude", "exit 0"))
    locked = []

    async def slow(provider, binary):
        locked.append(manager.lock.locked())
        await asyncio.sleep(0.4)
        return {"signed_in": True, "identity": "must-not-leak", "timed_out": False}

    monkeypatch.setattr(manager, "signed_in", slow)
    started = time.monotonic()
    response, rows = scan(client)
    assert time.monotonic() - started < 0.7
    assert locked == [False, False]
    assert rows["claude"]["signed_in"] is True and "must-not-leak" not in response.text


def test_gemini_is_credentials_present_and_never_launched(admin, stubs, tmp_path):
    client, manager = admin
    gemini = stub(stubs, "gemini", "exit 0")
    inventory(manager, gemini=gemini)
    assert scan(client)[1]["gemini"]["detail"] == "signed_out"
    folder = Path.home() / ".gemini"
    folder.mkdir()
    (folder / "settings.json").write_text(
        json.dumps({"security": {"auth": {"selectedType": "oauth-personal"}}})
    )
    (folder / "oauth_creds.json").write_text("{}")
    row = scan(client)[1]["gemini"]
    assert (row["found"], row["signed_in"], row["detail"]) == (
        True,
        None,
        "credential_present_unverified",
    )
    assert calls(stubs) == []


def test_deepseek_key_is_saved_not_verified(admin):
    client, manager = admin
    inventory(manager, codex="/bin/true")
    assert scan(client)[1]["deepseek"]["detail"] == "signed_out"
    (manager.state / "deepseek.key").write_text("k" * 20)
    row = scan(client)[1]["deepseek"]
    assert (row["found"], row["signed_in"], row["detail"]) == (True, None, "key_saved_unverified")


def test_fresh_state_is_not_completed_and_reading_writes_nothing(admin):
    client, manager = admin
    before = manager.path.read_text() if manager.path.exists() else None
    for _ in range(2):
        body = client.get("/api/first-run").json()
        assert body["completed"] is False and body["completed_at"] is None and body["version"]
    assert (manager.path.read_text() if manager.path.exists() else None) == before


def reopen(manager, **services):
    """A new app on the same state folder, as after a restart; ``services`` set ``enabled``."""
    app = create_app(str(manager.state))
    client = TestClient(app, base_url="http://127.0.0.1:8094", headers=ADMIN)
    client.cookies.set(local_access.COOKIE, local_access.issue_session(app.state.manager.state))
    client.get("/")
    return client, app.state.manager


def test_legacy_install_with_an_enabled_service_counts_once_at_startup(admin):
    _, manager = admin
    legacy = json.loads(manager.path.read_text()) if manager.path.exists() else manager.settings
    legacy = {k: v for k, v in legacy.items() if k != "first_run"}
    legacy["services"]["codex"]["enabled"] = True
    manager.path.write_text(json.dumps(legacy))
    client, started = reopen(manager)
    first = started.settings["first_run"]
    assert first["completed_at"]
    assert json.loads(started.path.read_text())["first_run"] == first  # persisted at init
    assert client.get("/api/first-run").json()["completed_at"] == first["completed_at"]
    # Later the service is turned off and the app restarts: the answer is never recomputed.
    stored = json.loads(started.path.read_text())
    stored["services"]["codex"]["enabled"] = False
    started.path.write_text(json.dumps(stored))
    assert reopen(manager)[1].settings["first_run"] == first


def test_legacy_install_without_a_service_is_not_completed(admin):
    _, manager = admin
    manager.settings.pop("first_run")
    manager.state_repository.save_settings(manager.settings)
    client, started = reopen(manager)
    assert started.settings["first_run"] == {"completed_at": None}
    assert json.loads(started.path.read_text())["first_run"] == {"completed_at": None}
    assert client.get("/api/first-run").json()["completed"] is False


async def two_scans(client):
    """Two scan requests at once, as two open wizards or a double click would send."""
    transport = httpx.ASGITransport(app=client.app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://127.0.0.1:8094",
        headers=ADMIN,
        cookies=client.cookies,
    ) as session:
        return await asyncio.gather(
            session.post("/api/first-run/scan", json={}),
            session.post("/api/first-run/scan", json={}),
        )


def test_concurrent_scans_share_one_run_and_one_result(admin, stubs, monkeypatch):
    client, manager = admin
    inventory(manager, codex=stub(stubs, "codex", "exit 0"))
    started = []

    async def slow(provider, binary):
        started.append(provider)
        await asyncio.sleep(0.3)
        return {"signed_in": True, "identity": None, "timed_out": False}

    monkeypatch.setattr(manager, "signed_in", slow)

    first, second = asyncio.run(two_scans(client))
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert started == ["codex"]  # each probe ran once, not once per caller


def test_the_inventory_refresh_is_shared_too(admin, monkeypatch):
    client, manager = admin
    manager.inventory = None
    refreshes = []

    async def refresh():
        refreshes.append(1)
        await asyncio.sleep(0.2)
        inventory(manager)

    monkeypatch.setattr(manager, "refresh", refresh)

    assert [r.status_code for r in asyncio.run(two_scans(client))] == [200, 200]
    assert refreshes == [1]


def test_finish_writes_prefs_then_the_marker_and_repeats_idempotently(admin):
    client, manager = admin
    prefs = {
        "theme": "graphite",
        "chat_selection": {"model": "gpt-5.6-sol", "effort": "medium"},
        "always_on_top": True,
    }
    done = client.post("/api/first-run", json={"prefs": prefs})
    assert done.status_code == 200 and done.json()["completed"] is True
    stored = ui_state.read({"state_dir": str(manager.state / "runs")}, "local")["values"]
    assert stored == prefs
    assert client.post("/api/first-run", json={"prefs": prefs}).json() == done.json()
    assert client.get("/api/first-run").json() == done.json()


def test_skip_completes_without_prefs(admin):
    client, manager = admin
    assert client.post("/api/first-run", json={}).json()["completed"] is True
    assert not (manager.state / "runs" / ui_state.FOLDER).exists()


@pytest.mark.parametrize(
    ("prefs", "code"),
    [
        ({"theme": "Not A Theme!"}, "ui_state_invalid_value"),
        ({"theme": "graphite", "always_on_top": "yes"}, "ui_state_invalid_value"),
        ({"tour_seen": "0.16.0"}, "ui_state_unknown_key"),
        ({"nope": 1}, "ui_state_unknown_key"),
        ("theme", "ui_state_invalid_value"),
    ],
)
def test_invalid_prefs_write_nothing_and_do_not_complete(admin, prefs, code):
    client, manager = admin
    response = client.post("/api/first-run", json={"prefs": prefs})
    assert (response.status_code, response.json()) == (422, {"error": code})
    assert client.get("/api/first-run").json()["completed"] is False
    assert not (manager.state / "runs" / ui_state.FOLDER).exists()


def test_reset_reopens_the_wizard(admin):
    client, _ = admin
    client.post("/api/first-run", json={})
    body = client.post("/api/first-run:reset", json={}).json()
    assert (body["completed"], body["completed_at"]) == (False, None)
    assert client.get("/api/first-run").json()["completed"] is False


def test_saving_settings_keeps_the_marker(admin):
    client, manager = admin
    done = client.post("/api/first-run", json={}).json()
    assert client.post("/api/settings", json=manager.settings).status_code == 200
    assert client.get("/api/first-run").json() == done
    assert json.loads(manager.path.read_text())["first_run"]["completed_at"] == done["completed_at"]


def test_the_routes_sit_behind_the_admin_guard(admin):
    client, manager = admin
    paths = ("/api/first-run", "/api/first-run/scan", "/api/first-run:reset")
    anonymous = TestClient(client.app, base_url="http://127.0.0.1:8094")
    for path in paths:
        assert anonymous.get(path).status_code == 401
        assert anonymous.post(path, json={}).status_code == 401
    bare = TestClient(client.app, base_url="http://127.0.0.1:8094", cookies=client.cookies)
    for path in paths:
        assert bare.post(path, json={}).status_code == 400  # x-harness-admin header required
        crossed = client.post(path, json={}, headers={**ADMIN, "Origin": "https://evil.test"})
        assert crossed.status_code == 403
