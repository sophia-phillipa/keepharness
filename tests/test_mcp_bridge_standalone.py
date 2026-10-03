"""setup-mcp.sh downloads mcp_bridge.py alone, so it must not import harness packages."""

import subprocess
import sys
from pathlib import Path

BRIDGE = Path(__file__).resolve().parent.parent / "agent_service" / "mcp_bridge.py"


def test_bridge_imports_without_harness_packages(tmp_path):
    copy = tmp_path / "mcp_bridge.py"
    copy.write_text(BRIDGE.read_text())
    code = (
        "import sys\n"
        "for name in ('control', 'agent_service', 'adapters', 'harness_ui'):\n"
        "    sys.modules[name] = None\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "import mcp_bridge\n"
        "assert mcp_bridge.read_env('AGENT_URL', 'LOCAL_AGENT_URL', 'x') == 'x'\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path)], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
