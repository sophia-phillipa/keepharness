"""Control-plane state files under the admin state directory."""

import json
import os
import sqlite3
import time
from pathlib import Path

# Files a rejected or interrupted configuration update must restore.
CONFIGURATION_FILES = (
    "settings.json",
    "runtime.json",
    "local-profile.json",
    "local-profiles.json",
    "autostart",
)


def private_file(path, flags):
    """``open`` opener: state files are owner-only whatever the process umask."""
    return os.open(path, flags, 0o600)


class ControlStateRepository:
    """Atomic settings/runtime writes, the audit log, rollback snapshots and harness busy."""

    def __init__(self, state):
        self.state = Path(state)
        self.settings_path = self.state / "settings.json"
        self.runtime_path = self.state / "runtime.json"

    def save_settings(self, settings):
        self._replace(self.settings_path, json.dumps(settings, indent=2))

    def read_runtime(self):
        try:
            return json.loads(self.runtime_path.read_text())
        except (OSError, ValueError):
            return {}

    def write_runtime(self, config):
        self._replace(self.runtime_path, json.dumps(config))

    @staticmethod
    def _replace(path, text):
        """Atomic owner-only replace; a failed write (e.g. disk full) leaves no temporary file."""
        tmp = path.with_suffix(".tmp")
        try:
            tmp.write_text(text)
            tmp.chmod(0o600)
            tmp.replace(path)
        finally:
            tmp.unlink(missing_ok=True)

    def audit(self, action):
        with open(self.state / "audit.jsonl", "a", opener=private_file) as out:
            out.write(json.dumps({"time": time.time(), "action": action}) + "\n")

    def snapshot(self):
        paths = [self.state / name for name in CONFIGURATION_FILES]
        return {path: path.read_bytes() if path.exists() else None for path in paths}

    def restore(self, previous):
        for path, content in previous.items():
            current = path.read_bytes() if path.exists() else None
            if current == content:
                continue
            if content is None:
                path.unlink(missing_ok=True)
            else:
                temporary = path.with_suffix(".rollback")
                temporary.write_bytes(content)
                temporary.chmod(0o600)
                temporary.replace(path)

    def harness_busy(self):
        db = self.state / "runs/jobs.sqlite3"
        if not db.exists():
            return False
        with sqlite3.connect(db) as c:
            return bool(
                c.execute(
                    "SELECT 1 FROM jobs WHERE state IN ('queued','running') LIMIT 1"
                ).fetchone()
            )
