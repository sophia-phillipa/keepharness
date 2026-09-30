"""Project hook grants never silently enable unrelated global hooks."""

import json

import pytest

from adapters.claude.native import build_command


@pytest.mark.parametrize(
    "hooks,global_hooks,source",
    [
        (False, False, "project"),
        (True, False, "project"),
        (True, True, "user,project"),
        (False, True, "project"),
    ],
)
def test_hook_setting_sources(tmp_path, monkeypatch, hooks, global_hooks, source):
    monkeypatch.setattr("adapters.claude.native.configurations", lambda: {"claude": {}})
    monkeypatch.setattr("adapters.claude.native.inventory", lambda: {"claude": []})
    command = build_command(
        {"binary": "claude", "global_hooks": global_hooks},
        "haiku",
        tmp_path,
        {"hooks": hooks},
        [],
        "ask",
        [],
    )
    assert command[command.index("--setting-sources") + 1] == source
    settings = json.loads(command[command.index("--settings") + 1])
    assert settings["disableAllHooks"] is not hooks
