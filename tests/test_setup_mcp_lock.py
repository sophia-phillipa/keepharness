"""setup-mcp.sh installs the bridge dependencies from the server's hashed lock, never loose ranges.

``python3`` is a stub whose venv ``python`` records its arguments; ``curl`` and ``claude`` are
stubs too. HOME is a temporary folder, so the real home and Claude profile stay untouched.
"""

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "agent_service/setup-mcp.sh"
URL = "https://example.ts.net"

PYTHON3 = """#!/bin/sh
if [ "$1" = '-c' ]; then
  case "$2" in *platform*) echo "${TEST_PY_ARCH:-${TEST_ARCH:-x86_64}}" ;; esac
fi
if [ "$1" = '-m' ]; then
  mkdir -p "$3/bin"
  printf '#!/bin/sh\\necho "$*" >> "$CALLS"\\n' > "$3/bin/python"
  chmod +x "$3/bin/python"
fi
"""
CURL = """#!/bin/sh
echo "curl $*" >> "$CALLS"
case "$*" in *"${FAIL_URL:-__never__}"*) exit 22 ;; esac
while [ "$1" != '-o' ]; do shift; done
printf '# fixture\\n' > "$2"
"""
CLAUDE = '#!/bin/sh\necho "claude $*" >> "$CALLS"\n'


def run_script(root, **extra):
    binaries = root / "bin"
    binaries.mkdir()
    uname = '#!/bin/sh\ncase "$1" in -s) echo "${TEST_OS:-Linux}";; -m) echo "${TEST_ARCH:-x86_64}";; esac\n'
    for name, source in {
        "python3": PYTHON3,
        "curl": CURL,
        "claude": CLAUDE,
        "uname": uname,
    }.items():
        (binaries / name).write_text(source)
        (binaries / name).chmod(0o755)
    calls = root / "calls"
    env = {
        **os.environ,
        "HOME": str(root),
        "PATH": f"{binaries}:{os.environ['PATH']}",
        "CALLS": str(calls),
        **extra,
    }
    result = subprocess.run([str(SCRIPT), URL], env=env, capture_output=True, text=True)
    return result, calls.read_text().splitlines() if calls.exists() else []


def test_script_has_valid_shell_syntax():
    assert subprocess.run(["sh", "-n", str(SCRIPT)]).returncode == 0


def test_intel_macos_stops_before_creating_environment(tmp_path):
    result, calls = run_script(tmp_path, TEST_OS="Darwin", TEST_ARCH="x86_64")
    assert result.returncode != 0
    assert "Intel macOS is unsupported" in result.stderr
    assert calls == []
    assert not (tmp_path / ".local").exists()


def test_intel_python_on_an_arm64_shell_stops_before_creating_environment(tmp_path):
    """The installing interpreter decides, not the shell (no cryptography source build)."""
    result, calls = run_script(tmp_path, TEST_OS="Darwin", TEST_ARCH="arm64", TEST_PY_ARCH="x86_64")
    assert result.returncode != 0
    assert "Intel macOS" in result.stderr
    assert calls == []
    assert not (tmp_path / ".local").exists()


@pytest.mark.parametrize("system,architecture", [("Linux", "x86_64"), ("Darwin", "arm64")])
def test_supported_platforms_install_the_bridge(tmp_path, system, architecture):
    result, calls = run_script(tmp_path, TEST_OS=system, TEST_ARCH=architecture)
    assert result.returncode == 0, result.stderr
    assert any("pip install --only-binary :all: --require-hashes -r" in call for call in calls)


def test_dependencies_come_from_the_downloaded_hashed_lock(tmp_path):
    result, calls = run_script(tmp_path)
    assert result.returncode == 0, result.stderr
    folder = tmp_path / ".local/share/keepharness-mcp"
    lock = folder / "bridge-requirements.txt"
    assert lock.read_text() == "# fixture\n"
    (install,) = [call for call in calls if "pip install" in call]
    assert install.startswith("-m pip install --only-binary :all: --require-hashes -r ")
    assert "mcp>=" not in install and "httpx>=" not in install
    downloads = [call for call in calls if call.startswith("curl")]
    assert any(
        call.endswith(f"{URL}/bridge-requirements.txt -o {install.split()[-1]}")
        for call in downloads
    )
    # The lock is fetched and installed before the bridge itself is fetched.
    assert [i for i, call in enumerate(calls) if "bridge-requirements.txt" in call][0] < [
        i for i, call in enumerate(calls) if "mcp_bridge.py" in call
    ][0]
    assert not list(folder.glob("bridge-requirements.??????"))


def test_a_missing_lock_stops_before_any_install_or_registration(tmp_path):
    result, calls = run_script(tmp_path, FAIL_URL="bridge-requirements.txt")
    assert result.returncode != 0
    assert not any("pip install" in call or call.startswith("claude") for call in calls)
    folder = tmp_path / ".local/share/keepharness-mcp"
    assert not (folder / "bridge-requirements.txt").exists()
    assert not list(folder.glob("bridge-requirements.??????"))
