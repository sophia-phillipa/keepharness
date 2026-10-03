"""Exercise the portable installer without network or a real Claude profile."""

import os
import subprocess
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "agent_service/setup-mcp.sh"


def test_installer_with_spaces_and_download_failure():
    with tempfile.TemporaryDirectory(prefix="mcp setup ") as directory:
        root = Path(directory)
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
        env = {**os.environ, "HOME": directory, "PATH": str(binaries) + ":" + os.environ["PATH"]}
        url = "https://example.ts.net"
        result = subprocess.run([str(SCRIPT), url], env=env, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        args = (root / "claude-args").read_text().splitlines()
        assert "KEEPHARNESS_AGENT_URL=" + url in args
        assert str(root / ".local/share/keepharness/venv/bin/python") in args
        bridge = root / ".local/share/keepharness/mcp_bridge.py"
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
