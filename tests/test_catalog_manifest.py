import json
from pathlib import Path

import pytest

from agent_service import catalog_manifest as manifests


def write_manifest(root, **values):
    root.mkdir(exist_ok=True)
    (root / "harness.catalog.json").write_text(json.dumps({"version": 1, **values}))
    return manifests.load_manifest(root)


def test_optional_manifest_and_strict_paths(tmp_path):
    assert manifests.load_manifest(tmp_path) is None
    for values in (
        {"context": ["../secret"]},
        {"cwd": "/tmp"},
        {"writable_state": ["../escape"]},
        {"runtime": {"requirements": "../outside"}},
    ):
        with pytest.raises(ValueError):
            write_manifest(tmp_path, **values)


def test_runtime_state_and_venv_outside_catalog(tmp_path):
    root = tmp_path / "catalog"
    manifest = write_manifest(root, writable_state=["memory"], runtime={"venv": True}, cwd=".")
    assert manifests.preflight(root, manifest, tmp_path / "state", "demo")
    result = manifests.materialize_runtime(root, manifest, tmp_path / "state", "demo")
    assert Path(result["environment"]["VIRTUAL_ENV"]).is_dir()
    assert not Path(result["writable_roots"][0]).is_relative_to(root)
    assert manifests.preflight(root, manifest, tmp_path / "state", "demo") == []


def test_preflight_never_executes_checks(tmp_path):
    manifest = write_manifest(
        tmp_path / "catalog", preflight=[{"file": "missing", "hint": "Create fixture input."}]
    )
    assert manifests.preflight(tmp_path / "catalog", manifest, tmp_path / "state", "demo") == [
        "Create fixture input."
    ]
    with pytest.raises(ValueError):
        write_manifest(tmp_path, preflight=[{"command": "touch marker"}])


def test_discovery_preflight_and_maintenance(tmp_path, monkeypatch):
    from agent_service.resources import discover

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    root = tmp_path / "catalog"
    write_manifest(
        root,
        preflight=[{"file": "ready", "hint": "Create ready file."}],
        provisions_maintenance=True,
    )
    (root / "commands").mkdir()
    (root / "commands/task.md").write_text("Run task.")
    (root / "commands/install.md").write_text("Install.")
    config = {
        "state_dir": str(tmp_path / "state"),
        "catalogs": [{"id": "demo", "root": str(root), "trusted": True}],
        "projects": {"p": {"root": str(tmp_path / "project"), "catalogs": ["demo"]}},
        "services": {"claude": {"mode": "native"}},
    }
    items = discover(config, "p", "claude")["items"]
    assert len(items) == 2
    assert all(not item["selectable"] for item in items)
    assert items[1]["unavailable_reason"] == "Create ready file."
    (root / "ready").touch()
    assert [item["name"] for item in discover(config, "p", "claude")["items"]] == ["task"]


def test_manifest_rejects_symlink_escape_and_runtime_state_alias(tmp_path):
    root = tmp_path / "catalog"
    root.mkdir()
    (root / "context").symlink_to(tmp_path / "outside")
    with pytest.raises(ValueError, match="escape"):
        write_manifest(root, context=["context"])
    manifest = write_manifest(root, writable_state=["memory"])
    state = tmp_path / "state"
    (state / "catalog_runtime/demo").mkdir(parents=True)
    (state / "catalog_runtime/demo/memory").symlink_to(tmp_path / "outside")
    with pytest.raises(ValueError):
        manifests.materialize_runtime(root, manifest, state, "demo")


def test_runtime_context_rules_and_two_catalogs(tmp_path):
    config = {
        "state_dir": str(tmp_path / "state"),
        "catalogs": [],
        "projects": {"p": {"catalogs": ["one", "two"]}},
    }
    for catalog_id in ("one", "two"):
        root = tmp_path / catalog_id
        write_manifest(root, context=["context.md"], rules=["rules.md"], writable_state=["memory"])
        (root / "context.md").write_text("Context")
        (root / "rules.md").write_text("Rules")
        manifests.materialize_runtime(
            root, manifests.load_manifest(root), tmp_path / "state", catalog_id
        )
        config["catalogs"].append({"id": catalog_id, "root": str(root), "trusted": True})
    result = manifests.runtime_for_project(config, "p")
    assert len(result["contexts"]) == len(result["rules"]) == len(result["writable_roots"]) == 2
    assert len(result["environment"]) == 2


def test_native_declared_hooks_are_available_in_palette(tmp_path, monkeypatch):
    from agent_service.resources import discover

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    root = tmp_path / "catalog"
    write_manifest(root, allowed_hooks=["check.sh"])
    (root / "check.sh").write_text("#!/bin/sh\nexit 0")
    (root / "check.sh").chmod(0o700)
    (root / "commands").mkdir()
    (root / "commands/task.md").write_text("Run task.")
    config = {
        "state_dir": str(tmp_path / "state"),
        "catalogs": [{"id": "demo", "root": str(root), "trusted": True}],
        "projects": {"p": {"catalogs": ["demo"]}},
        "services": {"claude": {"mode": "native"}},
    }
    item = discover(config, "p", "claude")["items"][0]
    assert item["selectable"] is True
    assert item["unavailable_reason"] == ""


def test_missing_writable_state_requires_provisioning(tmp_path):
    root = tmp_path / "catalog"
    manifest = write_manifest(root, writable_state=["memory"])
    state = tmp_path / "state"
    assert manifests.preflight(root, manifest, state, "demo") == [
        "Provision the catalog writable state in Admin: memory"
    ]
    manifests.materialize_runtime(root, manifest, state, "demo")
    assert manifests.preflight(root, manifest, state, "demo") == []


def test_requirements_can_be_added_to_existing_venv(tmp_path, monkeypatch):
    monkeypatch.setenv("PIP_NO_INDEX", "1")
    monkeypatch.setenv("PIP_DISABLE_PIP_VERSION_CHECK", "1")
    root = tmp_path / "catalog"
    state = tmp_path / "state"
    manifest = write_manifest(root, runtime={"venv": True})
    manifests.materialize_runtime(root, manifest, state, "demo")
    (root / "requirements.txt").write_text("")
    manifest = write_manifest(root, runtime={"venv": True, "requirements": "requirements.txt"})
    manifests.materialize_runtime(root, manifest, state, "demo")
    assert manifests.preflight(root, manifest, state, "demo") == []


def test_provisioning_rejects_dependency_stamp_symlink(tmp_path, monkeypatch):
    monkeypatch.setenv("PIP_NO_INDEX", "1")
    monkeypatch.setenv("PIP_DISABLE_PIP_VERSION_CHECK", "1")
    root, state = tmp_path / "catalog", tmp_path / "state"
    manifest = write_manifest(root, runtime={"venv": True})
    manifests.materialize_runtime(root, manifest, state, "demo")
    external = tmp_path / "external"
    external.write_text("preserve")
    (state / "catalog_runtime/demo/requirements.sha256").symlink_to(external)
    (root / "requirements.txt").write_text("")
    manifest = write_manifest(root, runtime={"venv": True, "requirements": "requirements.txt"})
    with pytest.raises(ValueError, match="stamp"):
        manifests.materialize_runtime(root, manifest, state, "demo")
    assert external.read_text() == "preserve"


def test_palette_explains_missing_catalog_credential_binding(tmp_path, monkeypatch):
    from agent_service.resources import discover
    from agent_service.secret_vault import SecretVault

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    root, state = tmp_path / "catalog", tmp_path / "state"
    contract = {
        "integration": "demo",
        "consumers": ["claude"],
        "environment": {"DEMO_TOKEN": "token"},
        "precedence": "vault",
        "mediated": False,
    }
    write_manifest(root, integrations=[contract])
    (root / "commands").mkdir()
    (root / "commands/task.md").write_text("Run task.")
    config = {
        "state_dir": str(state),
        "catalogs": [{"id": "demo", "root": str(root), "trusted": True}],
        "projects": {"p": {"catalogs": ["demo"]}},
        "services": {"claude": {"mode": "native"}},
    }
    item = discover(config, "p", "claude")["items"][0]
    assert not item["selectable"]
    assert "credential binding" in item["unavailable_reason"]
    config["integration_bindings"] = [
        {
            "integration": "demo",
            "catalog_id": "demo",
            "project_id": "p",
            "credential_binding": "demo",
        }
    ]
    SecretVault(state / "harness.secrets.json").set("demo", {"token": "fake-palette-secret"})
    item = discover(config, "p", "claude")["items"][0]
    assert item["selectable"]
    assert "fake-palette-secret" not in str(item)


def test_preflight_explains_nonexecutable_hook(tmp_path):
    root = tmp_path / "catalog"
    manifest = write_manifest(root, allowed_hooks=["check.sh"])
    (root / "check.sh").write_text("#!/bin/sh\nexit 0")
    (root / "check.sh").chmod(0o600)
    assert manifests.preflight(root, manifest, tmp_path / "state", "demo") == [
        "Make the declared catalog hook executable: check.sh"
    ]
