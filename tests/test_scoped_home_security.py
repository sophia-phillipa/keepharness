"""Persistent worker homes cannot redirect privileged harness writes."""

import os

import pytest

from adapters.shared.scoped import prepare_scoped
from agent_service.tools import ToolError


@pytest.fixture
def scoped_config(tmp_path):
    binary = tmp_path / "fixture"
    binary.write_text("fixture")
    auth = tmp_path / "provider.json"
    auth.write_text("{}")
    return {"binary": str(binary), "auth_file": str(auth), "python": "/usr/bin/python3"}


@pytest.mark.parametrize("name", ["config.toml", "auth.json", ".credentials.json"])
def test_planted_home_symlinks_refused_without_modifying_host(tmp_path, scoped_config, name):
    host = tmp_path / "host-secret"
    host.write_text("unchanged")
    host.chmod(0o640)
    home = tmp_path / "session"
    home.mkdir()
    (home / name).symlink_to(host)
    auth_name = ".credentials.json" if name == ".credentials.json" else "auth.json"
    with pytest.raises(ToolError, match="unsafe_scoped_home"):
        with prepare_scoped(scoped_config, {}, {}, home, "codex", auth_name):
            pass
    assert host.read_text() == "unchanged"
    assert host.stat().st_mode & 0o777 == 0o640


@pytest.mark.parametrize("ancestor", [False, True])
def test_home_symlink_or_ancestor_refused(tmp_path, scoped_config, ancestor):
    host = tmp_path / "host"
    host.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(host, target_is_directory=True)
    home = alias / "session" if ancestor else alias
    with pytest.raises(ToolError, match="unsafe_scoped_home"):
        with prepare_scoped(scoped_config, {}, {}, home, "codex", "auth.json"):
            pass
    assert list(host.iterdir()) == []


def test_home_hardlink_refused_without_modifying_host(tmp_path, scoped_config):
    host = tmp_path / "host-secret"
    host.write_text("unchanged")
    home = tmp_path / "session"
    home.mkdir()
    os.link(host, home / "auth.json")
    with pytest.raises(ToolError, match="unsafe_scoped_home"):
        with prepare_scoped(scoped_config, {}, {}, home, "codex", "auth.json"):
            pass
    assert host.read_text() == "unchanged"


def test_replacement_race_cannot_truncate_host(tmp_path, monkeypatch):
    from adapters.shared.scoped import scoped_home_write

    home = tmp_path / "session"
    home.mkdir()
    target = home / "config.toml"
    target.write_text("old")
    host = tmp_path / "host"
    host.write_text("unchanged")
    replace = os.replace

    def planted_replace(source, destination, **kwargs):
        target.unlink()
        target.symlink_to(host)
        return replace(source, destination, **kwargs)

    monkeypatch.setattr(os, "replace", planted_replace)
    scoped_home_write(home, "config.toml", "new")
    assert host.read_text() == "unchanged"
    assert target.read_text() == "new"
    assert not target.is_symlink()
    assert target.stat().st_mode & 0o777 == 0o600


def test_directory_swap_does_not_redirect_write(tmp_path, monkeypatch):
    from adapters.shared.scoped import scoped_home_write

    home = tmp_path / "session"
    home.mkdir()
    original = tmp_path / "original-session"
    host = tmp_path / "host"
    host.mkdir()
    (host / "config.toml").write_text("unchanged")
    original_open = os.open

    def swapped_open(path, flags, *args, **kwargs):
        if str(path).startswith(".harness-"):
            home.rename(original)
            home.symlink_to(host, target_is_directory=True)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swapped_open)
    scoped_home_write(home, "config.toml", "new")
    assert (host / "config.toml").read_text() == "unchanged"
    assert (original / "config.toml").read_text() == "new"


def test_thread_marker_rejects_planted_links(tmp_path):
    from adapters.shared.scoped import scoped_home_read, scoped_home_write

    host = tmp_path / "host"
    host.write_text("unchanged")
    home = tmp_path / "session"
    home.mkdir()
    (home / "remote-thread.json").symlink_to(host)
    for operation in (
        lambda: scoped_home_read(home, "remote-thread.json"),
        lambda: scoped_home_write(home, "remote-thread.json", "new"),
    ):
        with pytest.raises(ToolError, match="unsafe_scoped_home"):
            operation()
    assert host.read_text() == "unchanged"
