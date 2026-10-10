"""DeepSeek key presence: 'key saved' is claimed only for a regular, single-link key (issue #82)."""

import os
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from adapters.deepseek import account
from control import local_access
from control.server import create_app

ADMIN = {"X-Harness-Admin": "1"}
KEY = "fixture-presence-key-123"
KINDS = ["regular", "symlink", "hardlink", "directory", "missing"]


def _place(kind: str, state: Path, host: Path) -> None:
    """Put a deepseek.key of the given kind under state; host is a fixture file outside it."""
    key = state / "deepseek.key"
    host.write_text(KEY)
    placers = {
        "regular": lambda: key.write_text(KEY),
        "symlink": lambda: key.symlink_to(host),
        "hardlink": lambda: os.link(host, key),
        "directory": key.mkdir,
        "missing": lambda: None,
    }
    placers[kind]()


@pytest.fixture
def client(tmp_path):
    app = create_app(str(tmp_path / "state"))
    manager = app.state.manager
    manager.inventory = {"services": []}
    test_client = TestClient(app, base_url="http://127.0.0.1:8094", headers=ADMIN)
    test_client.cookies.set(local_access.COOKIE, local_access.issue_session(manager.state))
    test_client.get("/")
    return test_client, manager


@pytest.mark.parametrize("kind", KINDS)
def test_has_safe_key_is_true_only_for_a_regular_single_link_file(tmp_path, kind):
    from adapters.shared.private_files import has_safe_key

    state = tmp_path / "state"
    state.mkdir()
    _place(kind, state, tmp_path / "host.key")
    assert has_safe_key(state, "deepseek.key") == (kind == "regular")


def test_has_safe_key_follows_an_aliased_root_like_the_readers(tmp_path):
    from adapters.shared.private_files import has_safe_key

    real = tmp_path / "real"
    real.mkdir()
    (real / "deepseek.key").write_text(KEY)
    (tmp_path / "alias").symlink_to(real, target_is_directory=True)
    assert has_safe_key(tmp_path / "alias", "deepseek.key") is True


def test_has_safe_key_on_missing_root_creates_nothing(tmp_path):
    from adapters.shared.private_files import has_safe_key

    assert has_safe_key(tmp_path / "absent" / "state", "deepseek.key") is False
    assert not (tmp_path / "absent").exists()


@pytest.mark.parametrize("kind", KINDS)
def test_first_run_claims_saved_only_for_a_regular_key(client, kind):
    test_client, manager = client
    _place(kind, manager.state, manager.state.parent / "host.key")
    response = test_client.post("/api/first-run/scan", json={})
    assert response.status_code == 200, response.text
    row = {item["id"]: item for item in response.json()["providers"]}["deepseek"]
    expected = (
        (True, None, "key_saved_unverified") if kind == "regular" else (False, False, "signed_out")
    )
    assert (row["found"], row["signed_in"], row["detail"]) == expected


@pytest.mark.parametrize("kind", KINDS)
def test_state_reports_deepseek_credential_only_for_a_regular_key(client, kind):
    test_client, manager = client
    _place(kind, manager.state, manager.state.parent / "host.key")
    response = test_client.get("/api/state")
    assert response.status_code == 200, response.text
    assert response.json()["credentials"]["deepseek"] == (kind == "regular")


@pytest.mark.parametrize("kind", KINDS)
def test_ensure_private_home_creates_a_home_only_for_a_regular_key(tmp_path, kind):
    state = tmp_path / "state"
    state.mkdir()
    _place(kind, state, tmp_path / "host.key")
    account.ensure_private_home(state)
    assert (state / "providers" / "deepseek").exists() == (kind == "regular")
