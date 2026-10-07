"""The autouse fixture keeps every test away from the owner's real provider homes."""

import os
from pathlib import Path

from conftest import REAL_HOME


def test_home_and_provider_dirs_point_at_a_fake_home(isolated_provider_homes):
    home = isolated_provider_homes
    assert Path.home() == home
    assert Path.home().resolve() != REAL_HOME
    assert Path(os.environ["CODEX_HOME"]) == home / ".codex"
    assert Path(os.environ["CLAUDE_CONFIG_DIR"]) == home / ".claude"
    assert (home / ".codex").is_dir() and (home / ".claude").is_dir()
