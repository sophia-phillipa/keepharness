"""Admin origin/host/fetch-metadata boundary (spec P5-11); cases not already covered by
tests/test_admin_security.py and tests/test_folder_picker.py."""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from starlette.testclient import TestClient

from control.server import create_app
from tests.owner_session import sign_in


@pytest.fixture
def client():
    with tempfile.TemporaryDirectory() as tmp:
        with patch("control.manager.Manager.refresh", AsyncMock()):
            app = create_app(Path(tmp) / "state", 8094)
            with TestClient(app, base_url="http://127.0.0.1:8094") as test_client:
                sign_in(test_client).get("/")  # receive the admin cookie
                yield test_client


@pytest.mark.parametrize(
    "headers",
    [
        {"Host": "127.0.0.1.evil:8094"},
        {"Origin": "http://localhost:8094.evil"},
        {"Origin": "null"},
    ],
)
def test_mismatched_host_or_origin_is_rejected(client, headers):
    response = client.post(
        "/api/settings-export", json={}, headers={**headers, "X-Harness-Admin": "1"}
    )
    assert response.status_code == 403


def test_same_site_post_is_not_blocked_by_the_fetch_metadata_check(client):
    # admin_guard only special-cases "cross-site"; "same-site" reaches the cookie/header
    # checks like a plain request, so a same-site POST with a valid cookie succeeds.
    response = client.post(
        "/api/settings-export",
        json={},
        headers={"Sec-Fetch-Site": "same-site", "X-Harness-Admin": "1"},
    )
    assert response.status_code == 200


def test_cross_site_navigation_without_sec_fetch_user_is_rejected(client):
    response = client.get(
        "/",
        headers={
            "Sec-Fetch-Site": "cross-site",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Dest": "document",
        },
    )
    assert response.status_code == 403


def test_matching_localhost_host_and_origin_are_allowed(client):
    response = client.post(
        "/api/settings-export",
        json={},
        headers={
            "Host": "localhost:8094",
            "Origin": "http://localhost:8094",
            "X-Harness-Admin": "1",
        },
    )
    assert response.status_code == 200
