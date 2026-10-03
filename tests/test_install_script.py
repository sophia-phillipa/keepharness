"""install.sh: a preflight in a temporary venv comes before anything is stopped or moved.

Every command install.sh runs is a stub that records its arguments, so nothing touches the
real home, services or virtual environments. ``FAIL`` makes the stub whose arguments match
that shell pattern fail, after recording the call.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LOGGER = '#!/bin/sh\necho "{name} $*" >> "$CALLS"\n'
VENV_LOGGER = (
    '#!/bin/sh\nname=venv-python; case "$0" in "$VENV"/*) ;; *) name=preflight-python ;; esac\n'
    'echo "$name $*" >> "$CALLS"\n'
)
FAIL_ON = 'case "$*" in ${FAIL:-__never__}) echo "stub failed: $*" >&2; exit 1 ;; esac\n'
# `pip wheel --wheel-dir DIR` leaves one wheel in DIR, as the real build does.
WHEEL = (
    'case "$*" in *"pip wheel"*)\n'
    '  while [ $# -gt 0 ]; do\n'
    '    if [ "$1" = --wheel-dir ]; then mkdir -p "$2" && : > "$2/keepharness-0.15.0-py3-none-any.whl"; fi\n'
    "    shift\n"
    "  done ;;\n"
    "esac\n"
)


def significant(calls):
    """The calls after install.sh's Python version check."""
    return [call for call in calls if not call.startswith("python3 -c ")]


def stub(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(0o700)


@pytest.fixture
def install(tmp_path):
    bin_dir, venv, calls, temporary = (
        tmp_path / "bin", tmp_path / "venv", tmp_path / "calls", tmp_path / "tmp"
    )
    temporary.mkdir()
    stub(bin_dir / "systemctl", LOGGER.format(name="systemctl"))
    stub(
        bin_dir / "python3",
        LOGGER.format(name="python3")
        + FAIL_ON
        + 'case "$*" in\n'
        + '  *"-m control.install"*) [ -z "${REAL_PYTHON:-}" ] || exec "$REAL_PYTHON" "$@" ;;\n'
        + '  *"--field venv"*) echo "$VENV" ;;\n'
        + '  *"--field slug"*) echo keepharness ;;\n'
        + '  *"-m venv"*) for target; do :; done; mkdir -p "$target/bin" && cp "$VENV_PYTHON" "$target/bin/python" ;;\n'
        + "esac\n",
    )
    # The installed venv's python logs as venv-python, the preflight's as preflight-python.
    stub(tmp_path / "venv-python", VENV_LOGGER + FAIL_ON + WHEEL)
    stub(venv / "bin/keepharness-install", LOGGER.format(name="keepharness-install") + FAIL_ON)

    def run(*args, check=True, **extra):
        environment = {
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "HOME": str(tmp_path),
            "TMPDIR": str(temporary),
            "CALLS": str(calls),
            "VENV": str(venv),
            "VENV_PYTHON": str(tmp_path / "venv-python"),
            **extra,
        }
        run.result = subprocess.run(
            ["sh", str(ROOT / "install.sh"), *args],
            env=environment,
            check=check,
            capture_output=True,
            text=True,
        )
        if not calls.exists():
            return []
        # The preflight's temporary folder has a random name: call it TMP.
        pattern = re.escape(str(temporary)) + r"/keepharness-install\.\w+"
        return [re.sub(pattern, "TMP", line) for line in calls.read_text().splitlines()]

    run.venv = venv
    run.temporary = temporary
    return run


def test_check_only_neither_stops_tail_harness_nor_moves_its_state(install):
    calls = install("--check-only")
    assert not any(call.startswith("systemctl") for call in calls)
    assert "python3 control/product.py --migrate-state" not in calls
    assert any(call.startswith("python3 -m control.install --check-only") for call in calls)
    assert calls[-1] == "preflight-python -m control.install_check"


def test_install_stops_and_moves_before_the_venv_and_drops_the_old_package(install):
    calls = install("--port", "8094")
    wheel = "TMP/wheel/keepharness-0.15.0-py3-none-any.whl"
    order = [
        "python3 -m control.install --check-only --port 8094",
        "python3 -m venv TMP/venv",
        "preflight-python -m control.install_check",
        "systemctl --user stop tail-harness.service",
        "python3 control/product.py --migrate-state",
        f"python3 -m venv {install.venv}",
        "venv-python -m pip uninstall --yes tail-harness",
        f"venv-python -m pip install --quiet {wheel}",
        f"venv-python -m pip install --quiet --force-reinstall --no-deps {wheel}",
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


# --------------------------------------------------------------------------- preflight


@pytest.mark.parametrize(
    "failing",
    [
        "*-m?venv?*/keepharness-install.*",  # no ensurepip (python3-venv missing)
        "*pip?wheel*",
        "*pip?install?--quiet?*/keepharness-install.*",  # offline, or a dependency is missing
        "*-m?control.install_check*",
    ],
    ids=["venv", "build", "install", "smoke-test"],
)
def test_a_failed_preflight_stops_and_moves_nothing(install, failing):
    calls = install("--port", "8094", check=False, FAIL=failing)
    assert install.result.returncode != 0
    assert not any(call.startswith("systemctl") for call in calls)
    assert "python3 control/product.py --migrate-state" not in calls
    assert not any(call.startswith("keepharness-install") for call in calls)
    assert "Nothing was stopped or moved" in install.result.stderr
    assert list(install.temporary.iterdir()) == []  # the preflight venv is removed


def test_the_preflight_refusal_stops_install_sh_before_the_venv(install):
    calls = install("--port", "8094", check=False, FAIL="*-m?control.install?--check-only*")
    assert install.result.returncode != 0
    assert significant(calls) == ["python3 -m control.install --check-only --port 8094"]


def test_install_sh_refuses_inside_a_container_before_any_move(install, tmp_path):
    legacy = tmp_path / ".local/share/tail-harness"
    legacy.mkdir(parents=True)
    (legacy / "settings.json").write_text("{}")
    calls = install(
        "--port", "18977", check=False, REAL_PYTHON=sys.executable, CONTAINER_ID="claude-ubuntu"
    )
    assert install.result.returncode != 0
    assert "claude-ubuntu" in install.result.stderr and "host" in install.result.stderr
    assert "distrobox-host-exec" in install.result.stderr
    assert significant(calls) == ["python3 -m control.install --check-only --port 18977"]
    assert (legacy / "settings.json").exists()
    assert not (tmp_path / ".local/share/keepharness").exists()


def test_dev_installs_the_checkout_editable_and_records_it(install):
    calls = install("--dev", "--port", "8094")
    assert "venv-python -m pip install --editable ." in calls
    assert not any("force-reinstall" in call for call in calls)
    assert calls[-1] == "keepharness-install --dev --port 8094"


def test_the_wheel_is_built_from_a_copy_of_the_tracked_files(install):
    calls = install("--port", "8094")
    (build,) = [call for call in calls if " -m pip wheel " in call]
    assert build.endswith(" TMP/source")  # never the checkout itself


def test_a_failure_after_the_move_says_how_to_go_back(install):
    install("--port", "8094", check=False, FAIL="--port 8094")
    assert install.result.returncode != 0
    assert "./install.sh --rollback-to-0.14" in install.result.stderr


@pytest.mark.parametrize(
    "args, expected",
    [
        (["--merge-legacy"], "python3 -m control.state_merge"),
        (["--merge-legacy", "--apply"], "python3 -m control.state_merge --apply"),
        (["--rollback-to-0.14"], "python3 -m control.install --rollback-to-0.14"),
    ],
)
def test_merge_and_rollback_run_alone(install, args, expected):
    calls = install(*args)
    assert significant(calls) == [expected]
