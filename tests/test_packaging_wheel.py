"""P5-01/P5-02: the built wheel ships the right files and installs and runs standalone.

``slow`` (skipped unless ``--run-slow``): builds a real wheel with ``python -m build``
and installs it into a throwaway ``uv``-managed venv (``python -m venv`` has no
ensurepip in this container, so ``uv venv``/``uv pip`` stand in for it).
"""

import fnmatch
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.slow

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent


def _uv_binary():
    return shutil.which("uv") or str(Path.home() / ".local/bin/uv")


@pytest.fixture(scope="session")
def built_wheel(tmp_path_factory):
    out_dir = tmp_path_factory.mktemp("dist")
    subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(out_dir)],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=180,
    )
    wheels = list(out_dir.glob("*.whl"))
    assert len(wheels) == 1, wheels
    return wheels[0]


@pytest.fixture(scope="session")
def wheel_contents(built_wheel):
    with zipfile.ZipFile(built_wheel) as archive:
        return archive.namelist()


def test_wheel_contains_the_runtime_assets_every_backend_needs(wheel_contents):
    must_exist = [
        "adapters/README.md",
        "adapters/codex/specs/README.md",
        "adapters/codex/specs/models/gpt-6-astra.md",
        "adapters/codex/specs/compatibility.json",
        "adapters/claude/specs/compatibility.json",
        "agent_service/vendor/markdown-it.min.js",
        "agent_service/index.html",
        "agent_service/ui.css",
        "agent_service/ui.js",
        "agent_service/setup-mcp.sh",
        "agent_service/VERSION",
        "control/index.html",
        "control/admin.css",
        "control/admin.js",
        "tail_ui/assets/tabler.min.css",
        "tail_ui/assets/themes.css",
    ]
    missing = [path for path in must_exist if path not in wheel_contents]
    assert not missing, missing

    data_file_profiles = [
        name
        for name in wheel_contents
        if fnmatch.fnmatch(name, "*.data/data/share/tail-harness/profiles/*.json")
    ]
    assert len(data_file_profiles) >= 2, wheel_contents

    specs_markdown = [
        name for name in wheel_contents if fnmatch.fnmatch(name, "adapters/*/specs/**/*.md")
    ]
    assert specs_markdown, "expected at least one adapters/*/specs/**/*.md entry"


def test_wheel_excludes_tests_state_and_local_secrets(wheel_contents):
    forbidden_prefixes = ("tests/", "state/")
    forbidden_suffixes = (".gguf",)
    offenders = [
        name
        for name in wheel_contents
        if name.startswith(forbidden_prefixes)
        or name.endswith(forbidden_suffixes)
        or fnmatch.fnmatch(Path(name).name, ".env*")
    ]
    assert not offenders, offenders


@pytest.fixture(scope="session")
def installed_wheel_venv(tmp_path_factory, built_wheel):
    """A throwaway venv with the built wheel installed, ``uv``-managed."""
    venv_dir = tmp_path_factory.mktemp("venv") / "v"
    uv = _uv_binary()
    subprocess.run(
        [uv, "venv", str(venv_dir), "--python", sys.executable],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    python = venv_dir / "bin" / "python"
    subprocess.run(
        [uv, "pip", "install", "--python", str(python), str(built_wheel)],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return venv_dir


@pytest.mark.host_tools("uv")
def test_scan_and_install_check_pass_from_a_clean_install_outside_the_checkout(
    installed_wheel_venv, tmp_path
):
    python = installed_wheel_venv / "bin" / "python"
    tail_harness = installed_wheel_venv / "bin" / "tail-harness"
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    clean_env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    clean_env["HOME"] = str(fake_home)

    scan = subprocess.run(
        [str(tail_harness), "--scan"],
        cwd="/tmp",
        env=clean_env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert scan.returncode == 0, scan.stderr
    scanned = json.loads(scan.stdout)
    assert {"services", "binaries", "network"} <= scanned.keys()

    check = subprocess.run(
        [str(python), "-m", "control.install_check"],
        cwd="/tmp",
        env=clean_env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert check.returncode == 0, check.stdout + check.stderr
    assert check.stdout.strip().startswith("PASS:"), check.stdout

    uv_check = subprocess.run(
        [_uv_binary(), "pip", "check", "--python", str(python)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert uv_check.returncode == 0, uv_check.stdout + uv_check.stderr

    profiles = sorted((installed_wheel_venv / "share" / "tail-harness" / "profiles").glob("*.json"))
    assert profiles
    for profile in profiles:
        json.loads(profile.read_text())

    imported = subprocess.run(
        [str(python), "-c", "import control; print(control.__file__)"],
        cwd="/tmp",
        env=clean_env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert imported.returncode == 0, imported.stderr
    control_file = Path(imported.stdout.strip()).resolve()
    assert not control_file.is_relative_to(REPOSITORY_ROOT), control_file
