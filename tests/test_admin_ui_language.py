"""Admin read of the owner's saved interface language: behind the admin guard, read-only, one key only."""

from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

from agent_service import ui_state
from control import local_access
from control.server import create_app


@pytest.fixture
def admin(tmp_path):
    app = create_app(str(tmp_path / "state"))
    manager = app.state.manager
    client = TestClient(app, base_url="http://127.0.0.1:8094")
    client.cookies.set(local_access.COOKIE, local_access.issue_session(manager.state))
    client.get("/")
    return client, manager


def save(manager, **values):
    ui_state.update({"state_dir": str(manager.state / "runs")}, "local", values)


def test_returns_the_saved_language_and_no_other_key(admin):
    client, manager = admin
    save(manager, language="pt-BR", theme="graphite")
    response = client.get("/api/ui-language")
    assert response.status_code == 200
    assert response.json() == {"language": "pt-BR"}
    assert response.headers["Cache-Control"] == "no-store"


def test_unset_language_is_null_and_the_read_writes_nothing(admin):
    client, manager = admin
    assert client.get("/api/ui-language").json() == {"language": None}
    assert not (manager.state / "runs" / ui_state.FOLDER).exists()


def test_unparsable_store_is_null_not_an_error(admin):
    client, manager = admin
    save(manager, language="pt-BR")
    store = ui_state.repository({"state_dir": str(manager.state / "runs")}, "local")
    (store.folder / (ui_state.ENTRY + ".json")).write_text("{not json", encoding="utf-8")
    response = client.get("/api/ui-language")
    assert (response.status_code, response.json()) == (200, {"language": None})


def test_unreadable_store_is_null_not_an_error(admin, monkeypatch):
    client, manager = admin
    save(manager, language="pt-BR")

    def unreadable(config, owner):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(ui_state, "read", unreadable)
    response = client.get("/api/ui-language")
    assert (response.status_code, response.json()) == (200, {"language": None})


def test_the_route_sits_behind_the_admin_guard_and_has_no_write(admin):
    client, manager = admin
    save(manager, language="pt-BR")
    anonymous = TestClient(client.app, base_url="http://127.0.0.1:8094")
    assert anonymous.get("/api/ui-language").status_code == 401
    crossed = client.get("/api/ui-language", headers={"Origin": "https://evil.test"})
    assert crossed.status_code == 403
    spoofed = client.get("/api/ui-language", headers={"Host": "evil.test:8094"})
    assert spoofed.status_code == 403
    with patch.object(local_access, "direct_loopback", lambda request: False):
        assert client.get("/api/ui-language").status_code == 403
    write = client.post(
        "/api/ui-language", json={"language": "en"}, headers={"X-Harness-Admin": "1"}
    )
    assert write.status_code == 404
    assert ui_state.read({"state_dir": str(manager.state / "runs")}, "local")["values"] == {
        "language": "pt-BR"
    }
