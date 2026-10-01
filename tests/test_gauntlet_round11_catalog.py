import json
import subprocess
from pathlib import Path

import pytest

from agent_service.catalog_pin import pin_catalog, preview_update


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


@pytest.mark.parametrize("filter_kind", ["smudge", "process"])
@pytest.mark.parametrize("first_pin", [False, True])
def test_update_preview_does_not_execute_mutable_smudge(tmp_path, filter_kind, first_pin):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.email", "synthetic@example.invalid")
    git(root, "config", "user.name", "Synthetic fixture")
    (root / "task.md").write_text("First revision")
    (root / ".gitattributes").write_text("*.md filter=synthetic\n")
    git(root, "add", ".")
    git(root, "commit", "-qm", "first")
    catalog = {"id": "synthetic", "root": str(root), "kind": "git", "trusted": True}
    state = tmp_path / "state"
    pin = pin_catalog(catalog, state, "HEAD", owner=True)
    (root / "task.md").write_text("Second revision")
    git(root, "add", ".")
    git(root, "commit", "-qm", "second")
    marker = tmp_path / "HOST_EXECUTED"
    callback = tmp_path / "synthetic-smudge.sh"
    callback.write_text("#!/bin/sh\nprintf synthetic > " + str(marker) + "\ncat\n")
    callback.chmod(0o700)
    git(root, "config", "filter.synthetic." + filter_kind, str(callback))
    if first_pin:
        state = tmp_path / "fresh-state"
    preview = preview_update(catalog, pin, state, "HEAD", owner=True, fetch=False)
    print(
        json.dumps(
            {
                "preview_completed": bool(preview),
                "host_marker_written": marker.exists(),
                "candidate_content": (Path(preview["pin"]["root"]) / "task.md").read_text(),
            }
        )
    )
    assert not marker.exists(), (
        "Read-only preview executed mutable Git smudge configuration on host"
    )


@pytest.mark.parametrize("name", ["..", ".git", ".", "/absolute"])
def test_invalid_git_tree_never_writes_outside_pin(tmp_path, name):
    from agent_service.catalog_pin import CatalogPinError

    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.email", "synthetic@example.invalid")
    git(root, "config", "user.name", "Synthetic")

    def object_command(*args, content):
        return (
            subprocess.check_output(["git", "-C", str(root), *args], input=content).decode().strip()
        )

    blob = object_command("hash-object", "-w", "--stdin", content=b"SYNTHETIC_ESCAPE")
    inner = object_command("mktree", content=f"100644 blob {blob}\tescaped.txt\n".encode())
    tree = object_command(
        "hash-object",
        "--literally",
        "-t",
        "tree",
        "-w",
        "--stdin",
        content=b"40000 " + name.encode() + b"\0" + bytes.fromhex(inner),
    )
    commit = git(root, "commit-tree", tree, "-m", "Synthetic invalid tree")
    state = tmp_path / "state"
    with pytest.raises(CatalogPinError):
        pin_catalog(
            {"id": "demo", "root": str(root), "kind": "git", "trusted": True},
            state,
            commit,
            owner=True,
        )
    assert not list(state.rglob("escaped.txt"))
