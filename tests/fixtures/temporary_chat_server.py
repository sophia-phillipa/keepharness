"""Real harness with a synthetic home and local fake Claude CLI; no inference.

Run: python tests/fixtures/temporary_chat_server.py --root DIR --port PORT [--fake-edits]
The caller owns DIR, captures stdout/stderr there and removes it after inspection.
--fake-edits adds a Codex service and runs Claude through the edit-playing fake CLIs
(tests/fixtures/fake-codex, tests/fixtures/fake-claude) instead of the operator fake.
"""

import argparse
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tests" / "operator" / "fixture"))
sys.path.insert(0, str(REPO))
FIXTURES = REPO / "tests" / "fixtures"
CODEX_MODEL = "gpt-6-astra"


def wire_fake_edits(config):
    config["services"]["codex"] = {**config["services"]["claude"], "models": [CODEX_MODEL]}
    config["codex"] = {"binary": str(FIXTURES / "fake-codex" / "codex")}
    config["codex_models"] = {CODEX_MODEL: ["low", "medium", "high"]}
    config["claude"]["binary"] = str(FIXTURES / "fake-claude" / "claude")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--fake-edits", action="store_true")
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
    if args.fake_edits:
        # The fake Codex writes its log under CODEX_HOME, which the seed does not create.
        Path(os.environ["CODEX_HOME"]).mkdir(parents=True, exist_ok=True)
        wire_fake_edits(config)
    uvicorn.run(create_app(config), host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
