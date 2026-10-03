"""Shared pytest infrastructure: media-sandbox skip gate and config fixtures."""

import hashlib
import os
import shutil
import subprocess
import tempfile

import pytest

MEDIA_SANDBOX_MARKER = "requires_media_sandbox"
MEDIA_SANDBOX_TOOLS = ("ffmpeg", "bwrap", "prlimit")
LIVE_ENV_VAR = "TAIL_HARNESS_LIVE"
# CI sets this so a broken media sandbox fails the run instead of skipping tests.
REQUIRE_MEDIA_ENV_VAR = "TAIL_HARNESS_REQUIRE_MEDIA_SANDBOX"


def pytest_addoption(parser):
    parser.addoption(
        "--run-slow",
        action="store_true",
        default=False,
        help="also run tests marked 'slow' (packaging builds, the 0.5.0 upgrade path)",
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        f"{MEDIA_SANDBOX_MARKER}: skip when ffmpeg/bwrap/prlimit are missing or "
        "ffmpeg -version fails inside the tool sandbox",
    )
    config.addinivalue_line(
        "markers",
        "slow: takes tens of seconds (packaging builds, venvs, the 0.5.0 upgrade path); "
        "skipped unless --run-slow is passed",
    )
    config.addinivalue_line(
        "markers",
        f"live: spawns a real provider CLI and needs network/credentials; skipped unless "
        f"{LIVE_ENV_VAR}=1 is set",
    )
    config.addinivalue_line(
        "markers",
        "host_tools(*names): skip when one of the named host tools is missing from PATH",
    )


def _media_sandbox_unavailable_reason():
    """Return a reason string if the media sandbox cannot run here, else None."""
    missing = [tool for tool in MEDIA_SANDBOX_TOOLS if shutil.which(tool) is None]
    if missing:
        return "missing tool(s): " + ", ".join(missing)
    # Run ffmpeg through the real tool sandbox, so a sandbox that cannot load it
    # (blocked user namespaces, a library missing inside bwrap) is named here.
    from agent_service.tools import sandbox

    try:
        with tempfile.TemporaryDirectory() as work:
            probe = subprocess.run(
                sandbox(work, ["ffmpeg", "-version"]),
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"sandbox probe failed to start: {exc}"
    if probe.returncode != 0:
        detail = (probe.stderr.strip().splitlines() or ["no stderr"])[-1]
        return f"ffmpeg -version exited {probe.returncode} inside the sandbox: {detail}"
    return None


def pytest_collection_modifyitems(config, items):
    media_reason = _media_sandbox_unavailable_reason()
    if media_reason and os.environ.get(REQUIRE_MEDIA_ENV_VAR) == "1":
        raise pytest.UsageError(f"media sandbox required but unavailable: {media_reason}")
    skip_media = (
        pytest.mark.skip(reason=f"media sandbox unavailable: {media_reason}")
        if media_reason
        else None
    )
    run_slow = config.getoption("--run-slow")
    skip_slow = pytest.mark.skip(reason="slow: pass --run-slow to run")
    live_enabled = os.environ.get(LIVE_ENV_VAR) == "1"
    skip_live = pytest.mark.skip(reason=f"live: set {LIVE_ENV_VAR}=1 to run")
    for item in items:
        if skip_media is not None and MEDIA_SANDBOX_MARKER in item.keywords:
            item.add_marker(skip_media)
        if item.get_closest_marker("slow") is not None and not run_slow:
            item.add_marker(skip_slow)
        if item.get_closest_marker("live") is not None and not live_enabled:
            item.add_marker(skip_live)
        host_tools_marker = item.get_closest_marker("host_tools")
        if host_tools_marker is not None:
            missing = [name for name in host_tools_marker.args if shutil.which(name) is None]
            if missing:
                item.add_marker(
                    pytest.mark.skip(reason="missing host tool(s): " + ", ".join(missing))
                )


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


@pytest.fixture
def zone():
    """Switch the process time zone (``zone("UTC")``); the original one comes back afterwards."""
    import time

    saved = os.environ.get("TZ")

    def use(name):
        os.environ["TZ"] = name
        time.tzset()

    yield use
    if saved is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = saved
    time.tzset()
