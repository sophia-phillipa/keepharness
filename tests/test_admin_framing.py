"""Settings › System frames the local admin, and only the harness's own page may do it."""

from unittest.mock import AsyncMock, patch

import pytest
from starlette.testclient import TestClient

from agent_service.routes.system import admin_frame_source
from control.server import create_app, frame_ancestors


def test_admin_allows_only_the_local_harness_origin(tmp_path):
    inventory = {"services": [], "binaries": {}, "network": {}}
    with patch("control.discovery.scan", AsyncMock(return_value=inventory)):
        with TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8094") as client:
            port = client.app.state.manager.settings["port"]
            policy = client.get("/").headers["Content-Security-Policy"]
    assert f"frame-ancestors http://127.0.0.1:{port} http://localhost:{port};" in policy
    assert "frame-ancestors 'none'" not in policy


@pytest.mark.parametrize("port", [None, "8095", True])
def test_admin_without_a_valid_harness_port_cannot_be_framed(port):
    assert frame_ancestors(port) == "frame-ancestors 'none'"


@pytest.mark.parametrize(
    ("admin_url", "expected"),
    [
        ("http://127.0.0.1:8094/", "frame-src http://127.0.0.1:8094; "),
        ("http://localhost:8094/", "frame-src http://localhost:8094; "),
        ("https://127.0.0.1:8094/", ""),
        ("http://example.com:8094/", ""),
        ("http://127.0.0.1/", ""),
        ("http://127.0.0.1:99999/", ""),
        (None, ""),
    ],
)
def test_harness_frames_only_the_local_admin(admin_url, expected):
    assert admin_frame_source(admin_url) == expected
