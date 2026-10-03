"""install.sh: only a real install stops Tail Harness and moves its state; the venv is repaired.

Every command install.sh runs is a stub that records its arguments, so nothing touches the
real home, services or virtual environments.
"""

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LOGGER = '#!/bin/sh\necho "{name} $*" >> "$CALLS"\n'


def stub(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(0o700)


@pytest.fixture
def install(tmp_path):
    bin_dir, venv, calls = tmp_path / "bin", tmp_path / "venv", tmp_path / "calls"
    venv_python = LOGGER.format(name="venv-python")
    stub(bin_dir / "systemctl", LOGGER.format(name="systemctl"))
    stub(
        bin_dir / "python3",
        LOGGER.format(name="python3")
        + 'case "$*" in\n'
        + '  *"--field venv"*) echo "$VENV" ;;\n'
        + '  *"--field slug"*) echo keepharness ;;\n'
        + '  *"-m venv"*) mkdir -p "$VENV/bin" && cp "$VENV_PYTHON" "$VENV/bin/python" ;;\n'
        + "esac\n",
    )
    stub(tmp_path / "venv-python", venv_python)
    stub(venv / "bin/keepharness-install", LOGGER.format(name="keepharness-install"))

    def run(*args):
        environment = {
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "HOME": str(tmp_path),
            "CALLS": str(calls),
            "VENV": str(venv),
            "VENV_PYTHON": str(tmp_path / "venv-python"),
        }
        subprocess.run(["sh", str(ROOT / "install.sh"), *args], env=environment, check=True)
        return calls.read_text().splitlines()

    run.venv = venv
    return run


def test_check_only_neither_stops_tail_harness_nor_moves_its_state(install):
    calls = install("--check-only")
    assert not any(call.startswith("systemctl") for call in calls)
    assert "python3 control/product.py --migrate-state" not in calls
    assert "python3 control/product.py --check-migrated" in calls
    assert calls[-1] == "venv-python -m control.install_check"


def test_install_stops_and_moves_before_the_venv_and_drops_the_old_package(install):
    calls = install("--port", "8094")
    order = [
        "systemctl --user stop tail-harness.service",
        "python3 control/product.py --migrate-state",
        f"python3 -m venv {install.venv}",
        "venv-python -m pip uninstall --yes tail-harness",
        "venv-python -m pip install --editable .",
        "keepharness-install --port 8094",
    ]
    assert [call for call in calls if call in order] == order


@pytest.mark.parametrize("moved", [True, False])
def test_a_venv_moved_with_the_state_folder_is_rebuilt(install, moved):
    origin = "/old/tail-harness/venv" if moved else str(install.venv)
    stub(install.venv / "bin/pip", f"#!{origin}/bin/python3\n")
    calls = install()
    clear = f"python3 -m venv --clear {install.venv}"
    assert (clear in calls) is moved
    assert os.access(install.venv / "bin/python", os.X_OK)
