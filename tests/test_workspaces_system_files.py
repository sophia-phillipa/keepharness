"""Directly selected system files pass the same sensitive-file filter as folder walks."""

import os
from pathlib import Path

import pytest

from agent_service import workspaces


def home(tmp_path):
    root = tmp_path / "home"
    (root / ".ssh").mkdir(parents=True)
    (root / ".ssh" / "id_rsa").write_text("private key")
    (root / ".cache").mkdir()
    (root / ".cache" / "token.txt").write_text("token")
    (root / ".bashrc").write_text("export TOKEN=secret")
    (root / ".env").write_text("TOKEN=secret")
    (root / "key.pem").write_text("pem")
    (root / "notes.txt").write_text("notes")
    return root


@pytest.mark.parametrize("name", [".ssh/id_rsa", ".env", "key.pem", ".bashrc", ".cache/token.txt"])
def test_directly_selected_sensitive_file_is_skipped(tmp_path, name):
    selected, skipped = workspaces.selected_system_files(home(tmp_path), [name], 10)
    assert selected == []
    assert skipped == [{"path": name, "reason": "sensitive_file"}]


def test_directly_selected_proc_file_is_skipped():
    name = f"proc/{os.getpid()}/environ"
    if not (Path("/") / name).is_file():
        pytest.skip("procfs is not available")
    selected, skipped = workspaces.selected_system_files(Path("/"), [name], 10)
    assert selected == []
    assert skipped == [{"path": name, "reason": "sensitive_file"}]


def test_directly_selected_regular_file_still_attaches(tmp_path):
    root = home(tmp_path)
    selected, skipped = workspaces.selected_system_files(root, ["notes.txt"], 10)
    assert selected == [("notes.txt", root / "notes.txt")]
    assert skipped == []


def test_directory_selection_is_unchanged(tmp_path):
    root = tmp_path / "home"
    folder = root / "folder"
    (folder / ".ssh").mkdir(parents=True)
    (folder / ".ssh" / "id_rsa").write_text("private key")
    (folder / ".env").write_text("TOKEN=secret")
    (folder / "key.pem").write_text("pem")
    (folder / "notes.txt").write_text("notes")
    selected, skipped = workspaces.selected_system_files(root, ["folder"], 10)
    assert selected == [("folder/notes.txt", folder / "notes.txt")]
    assert sorted(item["path"] for item in skipped) == [
        "folder/.env",
        "folder/.ssh/id_rsa",
        "folder/key.pem",
    ]
    assert {item["reason"] for item in skipped} == {"sensitive_file"}
