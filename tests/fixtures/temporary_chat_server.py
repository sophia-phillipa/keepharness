"""Real harness with a synthetic home and local fake Claude CLI; no inference.

Run: python tests/fixtures/temporary_chat_server.py --root DIR --port PORT
The caller owns DIR, captures stdout/stderr there and removes it after inspection.
"""

import argparse
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tests" / "operator" / "fixture"))
sys.path.insert(0, str(REPO))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--port", required=True, type=int)
    args = parser.parse_args()
    root = Path(args.root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    home = root / "home"
    home.mkdir(exist_ok=True)
    os.environ["HOME"] = str(home)
    os.environ["XDG_CONFIG_HOME"] = str(home / ".config")
    os.environ["XDG_CACHE_HOME"] = str(home / ".cache")
    os.environ["CODEX_HOME"] = str(home / ".codex")
    os.environ["CLAUDE_CONFIG_DIR"] = str(home / ".claude")
    import uvicorn
    from serve_fixture import seed

    from agent_service.app import create_app

    config, _, _ = seed(root, args.port + 1, args.port)
    config["services"].pop("gemini")
    config.pop("admin_url", None)
    uvicorn.run(create_app(config), host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
