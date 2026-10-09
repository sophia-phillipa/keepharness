"""Project hook grants never silently enable unrelated global hooks.

The owner's personal-setup opt-in (decision D01) replaced the Claude-only ``global_hooks``.
"""

import json

import pytest

from adapters.claude.native import build_command


@pytest.mark.parametrize(
    "hooks,personal_setup,source",
    [
        (False, False, "project"),
        (True, False, "project"),
        (True, True, "user,project"),
        (False, True, "project"),
    ],
)
def test_hook_setting_sources(tmp_path, monkeypatch, hooks, personal_setup, source):
    monkeypatch.setattr("control.integrations.configurations", lambda: {"claude": {}})
    monkeypatch.setattr("control.integrations.inventory", lambda: {"claude": []})
    command = build_command(
        {"binary": "claude", "personal_setup": personal_setup},
        "haiku",
        tmp_path,
        {"hooks": hooks},
        [],
        "ask",
        [],
    )
    assert "--setting-sources" not in command
    settings = json.loads(command[command.index("--settings") + 1])
    assert "disableAllHooks" not in settings
