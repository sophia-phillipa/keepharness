import subprocess
from pathlib import Path

import pytest

from agent_service import catalog_pin as pins


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


@pytest.fixture
def repository(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.email", "fixture@example.invalid")
    git(root, "config", "user.name", "Fixture")
    (root / "commands").mkdir()
    (root / "commands/task.md").write_text("First revision")
    git(root, "add", ".")
    git(root, "commit", "-qm", "first")
    first = git(root, "rev-parse", "HEAD")
    (root / "commands/task.md").write_text("Second revision")
    git(root, "commit", "-qam", "second")
    return {"id": "demo", "root": str(root), "kind": "git", "trusted": True}, first


def test_pin_update_preview_and_project_isolation(repository, tmp_path):
    catalog, first = repository
    state = tmp_path / "state"
    pin = pins.pin_catalog(catalog, state, first, owner=True)
    assert pin["commit"] == first
    assert (Path(pin["root"]) / "commands/task.md").read_text() == "First revision"
    assert not (Path(pin["root"]) / "commands/task.md").stat().st_mode & 0o222
    update = pins.preview_update(catalog, pin, state, "HEAD", owner=True, fetch=False)
    assert update["pin"]["commit"] != first
    assert update["diff"][0]["resource_id"] == "catalog/demo/commands/task.md"
    assert update["diff"][0]["before"] != update["diff"][0]["after"]
    config = {"state_dir": str(state), "catalogs": [catalog]}
    project = {"catalogs": ["demo"], "catalog_pins": {"demo": pin}}
    assert pins.effective_catalogs(config, project)[0]["root"] == pin["root"]
    assert pins.snapshot_catalogs(config, project)[0]["dirty"] is False
    assert pins.effective_catalogs(config, {"catalogs": ["demo"]})[0]["root"] == catalog["root"]


def test_pin_requires_owner_and_rejects_escape(repository, tmp_path):
    catalog, first = repository
    with pytest.raises(ValueError, match="owner"):
        pins.pin_catalog(catalog, tmp_path / "state", first, owner=False)
    with pytest.raises(ValueError):
        pins.pin_catalog({**catalog, "id": "../escape"}, tmp_path / "state", first, owner=True)


def test_pin_refuses_dirty_checkout_and_symlink_storage(repository, tmp_path):
    catalog, first = repository
    state = tmp_path / "state"
    state.mkdir()
    (state / "catalog_pins").symlink_to(tmp_path / "outside")
    with pytest.raises(ValueError, match="symlink"):
        pins.pin_catalog(catalog, state, first, owner=True)
    (state / "catalog_pins").unlink()
    pin = pins.pin_catalog(catalog, state, first, owner=True)
    resource = Path(pin["root"]) / "commands/task.md"
    resource.chmod(0o644)
    resource.write_text("Changed")
    config = {"state_dir": str(state), "catalogs": [catalog]}
    with pytest.raises(ValueError, match="modified"):
        pins.effective_catalogs(config, {"catalogs": ["demo"], "catalog_pins": {"demo": pin}})


def test_update_diff_includes_manifest_custom_resources_and_runtime_dependencies(
    repository, tmp_path
):
    import json

    catalog, first = repository
    root = Path(catalog["root"])
    pin = pins.pin_catalog(catalog, tmp_path / "state", first, owner=True)
    (root / "ports").mkdir()
    (root / "ports/task.md").write_text("Custom command")
    (root / "requirements.txt").write_text("fixture-package==1")
    (root / "harness.catalog.json").write_text(
        json.dumps(
            {
                "version": 1,
                "resources": {"command": ["ports"]},
                "runtime": {"venv": True, "requirements": "requirements.txt"},
            }
        )
    )
    git(root, "add", ".")
    git(root, "commit", "-qm", "manifest")
    preview = pins.preview_update(catalog, pin, tmp_path / "state", "HEAD", owner=True, fetch=False)
    identities = {item["resource_id"] for item in preview["diff"]}
    assert {
        "catalog/demo/ports/task.md",
        "catalog/demo/requirements.txt",
        "catalog/demo/harness.catalog.json",
    } <= identities
