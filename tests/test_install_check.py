"""The installer smoke check signs in with the local admin secret, like the other local clients."""

import pytest
from starlette.testclient import TestClient

from control import install_check
from control.server import create_app

BASE = "http://127.0.0.1:19877"


def test_check_signs_in_with_the_local_secret(tmp_path):
    state = tmp_path / "state"
    with TestClient(create_app(state, port=19877), base_url=BASE) as client:
        assert client.get("/api/state").status_code == 401  # the guard is on
        install_check.check_admin_api(client, state)


def test_check_fails_cleanly_with_the_wrong_secret(tmp_path):
    with TestClient(create_app(tmp_path / "state", port=19877), base_url=BASE) as client:
        other = tmp_path / "other"
        create_app(other, port=19878)  # another install: its own local.key
        with pytest.raises(RuntimeError, match="refused the local admin secret"):
            install_check.check_admin_api(client, other)
