"""Exercise the portable installer without network or a real Claude profile."""

import os
import subprocess
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "agent_service/setup-mcp.sh"


def fake_tools(root):
    """Stub python3, curl and claude on a PATH, with HOME set to ``root`` (never the real one)."""
    binaries = root / "bin"
    binaries.mkdir()
    scripts = {
        "python3": """#!/bin/sh
if [ "$1" = '-m' ]; then
  mkdir -p "$3/bin"
  printf '#!/bin/sh\\nexit 0\\n' > "$3/bin/python"
  chmod +x "$3/bin/python"
fi
""",
        "curl": """#!/bin/sh
[ "${FAIL_DOWNLOAD:-0}" = 0 ] || exit 22
while [ "$1" != '-o' ]; do shift; done
printf '# bridge fixture\\n' > "$2"
""",
        "claude": '#!/bin/sh\nprintf "%s\\n" "$@" > "$HOME/claude-args"\n',
    }
    for name, source in scripts.items():
        executable = binaries / name
        executable.write_text(source)
        executable.chmod(0o755)
    return {**os.environ, "HOME": str(root), "PATH": str(binaries) + ":" + os.environ["PATH"]}


def test_installer_with_spaces_and_download_failure():
    with tempfile.TemporaryDirectory(prefix="mcp setup ") as directory:
        root = Path(directory)
        env = fake_tools(root)
        url = "https://example.ts.net"
        result = subprocess.run([str(SCRIPT), url], env=env, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        args = (root / "claude-args").read_text().splitlines()
        assert "KEEPHARNESS_AGENT_URL=" + url in args
        assert str(root / ".local/share/keepharness-mcp/venv/bin/python") in args
        bridge = root / ".local/share/keepharness-mcp/mcp_bridge.py"
        assert bridge.read_text() == "# bridge fixture\n"
        (root / "claude-args").unlink()
        result = subprocess.run(
            [str(SCRIPT), url], env={**env, "FAIL_DOWNLOAD": "1"}, capture_output=True
        )
        assert result.returncode != 0
        assert not (root / "claude-args").exists()
        assert bridge.read_text() == "# bridge fixture\n"
        assert not list(bridge.parent.glob("mcp_bridge.??????"))
        assert subprocess.run([str(SCRIPT)], env=env, capture_output=True).returncode != 0


def test_installer_writes_nothing_under_the_server_state_folder():
    """HAR-R4-2: the bridge has its own folder; the state folder and its venv are the server's."""
    with tempfile.TemporaryDirectory(prefix="mcp setup ") as directory:
        root = Path(directory)
        state = root / ".local/share/keepharness"
        (state / "runs").mkdir(parents=True)
        (state / "venv").mkdir()
        (state / "settings.json").write_text("{}")
        (state / "venv/pyvenv.cfg").write_text("home = /usr/bin\n")
        before = {p: p.read_bytes() for p in state.rglob("*") if p.is_file()}
        result = subprocess.run(
            [str(SCRIPT), "https://example.ts.net"],
            env=fake_tools(root),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert {p: p.read_bytes() for p in state.rglob("*") if p.is_file()} == before
        assert sorted(entry.name for entry in state.iterdir()) == ["runs", "settings.json", "venv"]
        assert (root / ".local/share/keepharness-mcp/mcp_bridge.py").is_file()
        args = (root / "claude-args").read_text().splitlines()
        assert str(root / ".local/share/keepharness-mcp/venv/bin/python") in args
