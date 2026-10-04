"""P5 §4: upgrading from a 0.5.0-era state directory to the current, split code.

``slow`` (skipped unless ``--run-slow``): checks out commit 9fd68e5 (0.5.0, before the
module split) into a throwaway ``git worktree``, runs its monolithic
``agent_service.app.Service``/``control.server.Manager`` in a subprocess (via
``PYTHONPATH``, since the current process already has today's ``agent_service`` and
``control`` imported under those same names) to build a realistic state directory, then
opens that same directory with today's code and checks the data and the legacy-execution-
mode freeze survive.
"""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.slow

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
OLD_COMMIT = "9fd68e5"

OLD_SETUP_SCRIPT = """
import asyncio, hashlib, json, sys
from pathlib import Path

state = Path(sys.argv[1])
text_file = Path(sys.argv[2])

from agent_service.app import Service

cfg = {
    "state_dir": str(state),
    "projects": {"p": {}},
    "clients": {"a": {"sha256": hashlib.sha256(b"a").hexdigest(), "projects": ["p"]}},
    "services": {
        "local": {
            "enabled": True,
            "models": ["installed-model"],
            "projects": ["p"],
            "permissions": {"read": True, "upload": True},
        }
    },
    "uploads_enabled": True,
    "origins": [],
}
identity = ("a", cfg["clients"]["a"])
service = Service(cfg)

# A conversation created through the real 0.5.0 submit() path: it already carries an
# execution_mode (that field predates 9fd68e5), then renamed.
root_a = service.submit(identity, {
    "project_id": "p", "backend": "local", "model": "installed-model",
    "prompt": "First conversation",
})["job_id"]
with service.db:
    service.db.execute(
        "UPDATE jobs SET state='completed', result=? WHERE id=?",
        (json.dumps({"answer": "ok"}), root_a),
    )
    service.db.execute(
        "INSERT INTO conversation_titles(id,title) VALUES(?,?)",
        (root_a, "Renamed conversation"),
    )

# A genuinely legacy (pre-0.5.0) row shape: no execution_mode key at all, inserted
# directly since 0.5.0's own submit() always sets one. Deleted, to check the deletion
# marker survives the upgrade.
legacy_b_payload = {
    "project_id": "p", "backend": "local", "model": "installed-model",
    "prompt": "Legacy conversation, deleted before the upgrade",
}
legacy_b = "legacyb" + hashlib.sha256(b"b").hexdigest()[:25]
with service.db:
    service.db.execute(
        "INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?)",
        (legacy_b, "p", "a", "completed", 1.0, json.dumps(legacy_b_payload),
         json.dumps({"answer": "legacy answer"}), None, None),
    )
    service.db.execute("INSERT INTO deleted_conversations VALUES(?)", (legacy_b,))

# A second legacy row, kept alive, to continue after the upgrade and check the
# legacy-conversation execution_mode freeze (app.py's ``legacy_root`` handling).
legacy_c_payload = {
    "project_id": "p", "backend": "local", "model": "installed-model",
    "prompt": "Legacy conversation kept alive, no execution_mode",
}
legacy_c = "legacyc" + hashlib.sha256(b"c").hexdigest()[:25]
with service.db:
    service.db.execute(
        "INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?)",
        (legacy_c, "p", "a", "completed", 1.5, json.dumps(legacy_c_payload),
         json.dumps({"answer": "legacy c answer"}), None, None),
    )

attach_result = asyncio.run(
    service.attach_project_files(
        identity, "p", [("notes.txt", text_file)], [], "local", "installed-model"
    )
)
file_id = attach_result["attachments"][0]["file_id"]

from control.server import Manager

manager = Manager(state)
manager.settings["services"]["local"]["enabled"] = True
manager.settings["services"]["local"]["models"] = ["installed-model"]
manager.save(manager.settings)

print(json.dumps(
    {"root_a": root_a, "legacy_b": legacy_b, "legacy_c": legacy_c, "file_id": file_id}
))
"""


@pytest.fixture(scope="module")
def old_worktree(tmp_path_factory):
    worktree = tmp_path_factory.mktemp("upgrade") / "old-0.5.0"
    subprocess.run(
        ["git", "worktree", "add", str(worktree), OLD_COMMIT],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    try:
        yield worktree
    finally:
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(worktree)],
            cwd=REPOSITORY_ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )


def _new_config(state_dir):
    return {
        "state_dir": str(state_dir),
        "projects": {"p": {}},
        "clients": {"a": {"sha256": hashlib.sha256(b"a").hexdigest(), "projects": ["p"]}},
        "services": {
            "local": {
                "enabled": True,
                "models": ["installed-model"],
                "projects": ["p"],
                "permissions": {"read": True, "upload": True},
            }
        },
        "uploads_enabled": True,
        "origins": [],
    }


def test_conversations_titles_deletions_and_files_survive_the_upgrade(old_worktree, tmp_path):
    state = tmp_path / "state"
    notes = tmp_path / "notes.txt"
    notes.write_text("evidence line one\nevidence line two\n")

    script = tmp_path / "old_setup.py"
    script.write_text(OLD_SETUP_SCRIPT)

    old_run = subprocess.run(
        [sys.executable, str(script), str(state), str(notes)],
        cwd=old_worktree,
        env={"PYTHONPATH": str(old_worktree)},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert old_run.returncode == 0, old_run.stderr
    ids = json.loads(old_run.stdout.strip().splitlines()[-1])

    from agent_service.app import create_app
    from control.server import Manager

    identity = ("a", _new_config(state)["clients"]["a"])
    app = create_app(_new_config(state))
    service = app.state.service

    title = service.conversation_title(service.job(identity, ids["root_a"]))
    assert title == "Renamed conversation"

    assert ids["legacy_b"] in service.conversation_repository.archived()

    owner = service.db.execute("SELECT owner FROM files WHERE id=?", (ids["file_id"],)).fetchone()[
        0
    ]
    assert owner == "a"

    assert service.db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    manager = Manager(state)
    manager.validate(manager.settings)  # must not raise: 0.5.0-written settings.json

    legacy_row = json.loads(service.conversation_repository.get(ids["legacy_c"])["payload"])
    assert "execution_mode" not in legacy_row

    continuation = service.submit(
        identity,
        {
            "project_id": "p",
            "backend": "local",
            "model": "installed-model",
            "parent_job_id": ids["legacy_c"],
            "prompt": "continue the legacy conversation",
        },
    )

    frozen_row = json.loads(service.conversation_repository.get(ids["legacy_c"])["payload"])
    assert frozen_row["execution_mode"] == continuation["execution_mode"]
