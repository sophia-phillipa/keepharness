"""Isolated KeepHarness pair (admin + harness) for the operator suite's fixture mode.

Same shape as scripts/test-ui.sh: a control admin with its own state and a harness
started from a written config. Differences: fixed ports chosen by the caller, a
private HOME (so nothing reads or writes the user's ~/.claude, ~/.codex or folders),
two fake provider CLIs (tests/operator/fixture/fake_provider.py) and one approval
enrollment nonce so the suite can enable approvals in its own browser.

Usage: python serve_fixture.py --root DIR --admin-port 18510 --harness-port 18511
Prints one JSON line when both servers answer, then waits; SIGTERM stops both.
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
FAKE = Path(__file__).resolve().parent / "fake_provider.py"
# The composer lists only Claude ids shaped like claude-<family>-<version>.
CLAUDE_MODELS = ("claude-sonnet-5-5", "claude-opus-5-5")
PROJECT_FILES = {
    "README.md": "# Alpha research\n\nFixture project for the operator suite.\n",
    "notes.md": "## Notes\n\n- first fixture note\n- second fixture note\n",
    "src/app.py": "def main():\n    return 'fixture'\n",
    "data/sample.csv": "name,value\nalpha,1\nbeta,2\n",
    # Project resources for the "/" palette: one agent, one skill, one command.
    ".claude/agents/fixture-reviewer.md": "---\nname: fixture-reviewer\ndescription: Reviews fixture notes.\n---\nReview the notes and list issues.\n",
    ".claude/skills/fixture-check/SKILL.md": "---\nname: fixture-check\ndescription: Checks fixture data.\n---\nCheck data/sample.csv.\n",
    ".claude/commands/fixture-hello.md": "---\ndescription: Says hello from the fixture.\n---\nSay hello.\n",
}
# A user-scope skill, which a run never loads (ledger L06).
HOME_FILES = {".claude/skills/user-only/SKILL.md": "---\nname: user-only\ndescription: A user-scope skill.\n---\nUser scope.\n"}


def wrapper(path, python):
    path.write_text(f'#!/bin/sh\nexec "{python}" "{FAKE}" "$@"\n')
    path.chmod(0o700)
    return str(path)


def seed(root, admin_port, harness_port):
    home = root / "home"
    alpha = home / "projects" / "alpha"
    for name, text in PROJECT_FILES.items():
        (alpha / name).parent.mkdir(parents=True, exist_ok=True)
        (alpha / name).write_text(text)
    (home / "projects" / "beta").mkdir(parents=True, exist_ok=True)
    (home / "projects" / "beta" / "plan.md").write_text("# Beta plan\n")
    for name, text in HOME_FILES.items():
        (home / name).parent.mkdir(parents=True, exist_ok=True)
        (home / name).write_text(text)
    # One configured connector so the plugins panel has something to show.
    (home / ".claude.json").write_text(
        json.dumps({"mcpServers": {"fixture-docs": {"command": "true", "args": []}}})
    )
    binaries = root / "bin"
    binaries.mkdir()
    claude = wrapper(binaries / "claude", sys.executable)
    gemini = wrapper(binaries / "gemini", sys.executable)
    projects = ["sem-projeto", "alpha"]
    permissions = {"read": True, "write": True, "shell": True, "delegate": True, "upload": True}
    service = {"enabled": True, "mode": "native", "projects": projects, "permissions": permissions}
    config = {
        "state_dir": str(root / "chat"),
        "bind": "127.0.0.1",
        "port": harness_port,
        "local_access": True,
        "shared_projects": True,
        "uploads_enabled": True,
        "admin_url": f"http://127.0.0.1:{admin_port}/",
        "clients": {"local": {"sha256": "0" * 64, "projects": projects}},
        "projects": {"sem-projeto": {}, "alpha": {"label": "Alpha research", "root": str(alpha)}},
        "services": {
            "claude": {**service, "models": list(CLAUDE_MODELS)},
            "gemini": {**service, "models": ["gemini-fixture"]},
        },
        "claude": {"binary": claude, "python": sys.executable},
        "claude_models": {model: ["configured", "low", "medium", "high"] for model in CLAUDE_MODELS},
        "gemini": {"binary": gemini},
        "gemini_models": ["gemini-fixture"],
        "origins": [f"http://127.0.0.1:{harness_port}"],
        "config_revision": uuid.uuid4().hex,
    }
    (root / "chat.json").write_text(json.dumps(config))
    admin_state = root / "admin"
    admin_state.mkdir(mode=0o700)
    # The admin lets only its harness port frame it (Settings > System); no provider is
    # enabled, so it never starts a harness of its own.
    (admin_state / "settings.json").write_text(
        json.dumps(
            {
                "services": {
                    p: {"enabled": False, "models": [], "projects": ["sem-projeto"], "mode": "native", "integrations": [], "permissions": {}}
                    for p in ("codex", "claude", "gemini", "local", "deepseek")
                },
                "projects": [],
                "catalogs": [],
                "uploads_enabled": False,
                "port": harness_port,
                "tailnet_port": harness_port,
                "logins": [],
            }
        )
    )
    return config, home, admin_state


def wait_for(url, seconds=30):
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                return response.read()
        except OSError:
            time.sleep(0.25)
    raise SystemExit("fixture server unavailable: " + url)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--admin-port", type=int, default=18510)
    parser.add_argument("--harness-port", type=int, default=18511)
    args = parser.parse_args()
    root = Path(args.root)
    root.mkdir(parents=True, exist_ok=True)
    config, home, admin_state = seed(root, args.admin_port, args.harness_port)
    sys.path.insert(0, str(REPO))
    from agent_service.approval_sessions import issue_enrollment

    nonce = issue_enrollment(config, "local")
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("KEEPHARNESS_", "TAIL_HARNESS_", "LOCAL_AGENT_"))
    }
    env.update(HOME=str(home), KEEPHARNESS_AGENT_CONFIG=str(root / "chat.json"))
    logs = [open(root / name, "ab") for name in ("admin.log", "harness.log")]
    children = [
        subprocess.Popen(
            [sys.executable, "-m", "control", "--port", str(args.admin_port), "--state", str(admin_state)],
            cwd=REPO, env=env, stdout=logs[0], stderr=subprocess.STDOUT,
        ),
        subprocess.Popen(
            [sys.executable, "-m", "agent_service.app"],
            cwd=REPO, env=env, stdout=logs[1], stderr=subprocess.STDOUT,
        ),
    ]

    def stop(*_):
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(15)
            except subprocess.TimeoutExpired:
                child.kill()
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        harness = f"http://127.0.0.1:{args.harness_port}"
        admin = f"http://127.0.0.1:{args.admin_port}"
        served = json.loads(wait_for(harness + "/v1/version"))
        assert served["config_revision"] == config["config_revision"], "another harness answers"
        wait_for(admin + "/")
        print(
            json.dumps(
                {
                    "harness_url": harness,
                    "admin_url": admin,
                    "nonce": nonce,
                    "root": str(root),
                    "home": str(home),
                    "project_root": config["projects"]["alpha"]["root"],
                    "version": served["version"],
                    "pids": [child.pid for child in children],
                }
            ),
            flush=True,
        )
        while all(child.poll() is None for child in children):
            time.sleep(0.5)
        raise SystemExit("a fixture server exited; see " + str(root))
    finally:
        stop()


if __name__ == "__main__":
    main()
