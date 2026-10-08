"""Retirement leaves former cloud homes and linked entries entirely untouched."""

import asyncio
import os

import pytest

import adapters
from agent_service.tools import ToolError


@pytest.mark.parametrize("provider", ["codex", "claude"])
@pytest.mark.parametrize(
    "entry", ["auth.json", "config.toml", "remote-thread.json", ".credentials.json"]
)
@pytest.mark.parametrize("link", ["regular", "symlink", "hardlink"])
@pytest.mark.parametrize("auth_setting", [False, True])
def test_retired_dispatch_never_reads_or_rewrites_old_home(
    tmp_path, provider, entry, link, auth_setting, no_retired_side_effects
):
    old_home = tmp_path / "providers" / "home" / ("." + provider)
    old_home.mkdir(parents=True)
    target = tmp_path / "sentinel"
    target.write_text("sentinel credential")
    artifact = old_home / entry
    if link == "symlink":
        artifact.symlink_to(target)
    elif link == "hardlink":
        os.link(target, artifact)
    else:
        artifact.write_text("sentinel credential")
    no_retired_side_effects.extend([artifact, target])
    before = artifact.lstat()
    config = {"binary": "fixture"}
    if auth_setting:
        config["auth_file"] = str(artifact)
    with pytest.raises(ToolError, match="execution_mode_unsupported"):
        asyncio.run(
            adapters.run_scoped(
                config, "continue", lambda *_: None, session_dir=old_home, provider=provider
            )
        )
    assert artifact.lstat() == before
