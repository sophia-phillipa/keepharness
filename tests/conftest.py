"""Shared pytest infrastructure: media-sandbox skip gate and config fixtures."""

import hashlib
import shutil
import subprocess

import pytest

MEDIA_SANDBOX_MARKER = "requires_media_sandbox"
MEDIA_SANDBOX_TOOLS = ("ffmpeg", "bwrap", "prlimit")


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        f"{MEDIA_SANDBOX_MARKER}: skip when ffmpeg/bwrap/prlimit or the bwrap "
        "user-namespace probe are unavailable in this sandbox",
    )


def _media_sandbox_unavailable_reason():
    """Return a reason string if the media sandbox cannot run here, else None."""
    missing = [tool for tool in MEDIA_SANDBOX_TOOLS if shutil.which(tool) is None]
    if missing:
        return "missing tool(s): " + ", ".join(missing)
    try:
        probe = subprocess.run(
            ["bwrap", "--ro-bind", "/", "/", "true"],
            capture_output=True,
            timeout=10,
            check=False,
        )
    except OSError as exc:
        return f"bwrap probe failed to start: {exc}"
    if probe.returncode != 0:
        return f"bwrap --ro-bind probe exited {probe.returncode} (user namespaces likely blocked)"
    return None


def pytest_collection_modifyitems(config, items):
    reason = _media_sandbox_unavailable_reason()
    if reason is None:
        return
    skip = pytest.mark.skip(reason=f"media sandbox unavailable: {reason}")
    for item in items:
        if MEDIA_SANDBOX_MARKER in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def tmp_state(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    return state_dir


@pytest.fixture
def make_harness_config(tmp_state):
    def factory(**overrides):
        cfg = {
            "state_dir": str(tmp_state),
            "bind": "127.0.0.1",
            "port": 18095,
            "local_access": True,
            "clients": {"local": {"sha256": "0" * 64, "projects": ["sem-projeto"]}},
            "projects": {"sem-projeto": {}},
            "services": {},
            "origins": ["http://127.0.0.1:18095"],
        }
        cfg.update(overrides)
        return cfg

    return factory


@pytest.fixture
def harness_config(make_harness_config):
    return make_harness_config()


@pytest.fixture
def client(tmp_path):
    from starlette.testclient import TestClient

    from agent_service.app import create_app

    cfg = {
        "state_dir": str(tmp_path),
        "origins": [],
        "projects": {"p": {}},
        "clients": {"a": {"sha256": hashlib.sha256(b"a").hexdigest(), "projects": ["p"]}},
        "services": {},
    }
    app = create_app(cfg)
    with TestClient(app, headers={"Authorization": "Bearer a"}) as test_client:
        yield test_client
