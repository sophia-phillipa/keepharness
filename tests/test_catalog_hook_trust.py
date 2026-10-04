"""A catalog hook is trusted by digest; a changed or unreadable one is skipped until re-trusted."""

import asyncio
import json
from pathlib import Path

import pytest

from agent_service.catalog_hooks import run_hooks
from agent_service.catalog_manifest import hooks_digest, hooks_trusted, runtime_for_project
from control.catalog_admin import catalog_config, change_pin
from control.server import Manager


def make_catalog(root, hooks=("check.sh",), body="exit 0\n"):
    root.mkdir(exist_ok=True)
    (root / "harness.catalog.json").write_text(json.dumps({"version": 1, "allowed_hooks": list(hooks)}))
    for name in hooks:
        (root / name).write_text("#!/bin/sh\n" + body)
        (root / name).chmod(0o700)
    return root


def project_root(tmp_path):
    (tmp_path / "project").mkdir(exist_ok=True)
    return tmp_path / "project"


def config_for(tmp_path, root, digest):
    catalog = {"id": "demo", "root": str(root), "trusted": True}
    if digest:
        catalog["hooks_sha256"] = digest
    return {
        "state_dir": str(tmp_path / "state"),
        "catalogs": [catalog],
        "projects": {"p": {"catalogs": ["demo"]}},
    }


def test_changed_hook_is_skipped_until_retrusted(tmp_path):
    root = make_catalog(tmp_path / "catalog")
    manager = Manager(tmp_path / "state")
    manager.settings["catalogs"] = [
        {"id": "demo", "root": str(root), "trusted": True, "kind": "folder", "namespace": "demo",
         "hooks_sha256": hooks_digest(root)}
    ]
    manager.settings["projects"] = [{"id": "p", "root": str(project_root(tmp_path)), "catalogs": ["demo"]}]
    assert runtime_for_project(catalog_config(manager), "p")["allowed_hooks"]
    (root / "check.sh").write_text("#!/bin/sh\ntouch tampered\n")
    skipped = runtime_for_project(catalog_config(manager), "p")  # the turn goes on, hooks dropped
    assert skipped["allowed_hooks"] == [] and skipped["hooks_skipped"] == ["demo"]
    events = []
    asyncio.run(run_hooks(skipped, True, lambda kind, value: events.append((kind, value))))
    assert events == [("catalog_hook", {"outcome": "skipped", "reason": "hooks_not_trusted", "catalog": "demo"})]
    assert not (root / "tampered").exists()
    status = asyncio.run(
        change_pin(None, manager, {"action": "retrust", "project_id": "p", "catalog_id": "demo"})
    )
    assert manager.settings["catalogs"][0]["hooks_sha256"] == hooks_digest(root)
    assert status["preflight"][0]["hooks_trust"]["trusted"] is True
    assert runtime_for_project(catalog_config(manager), "p")["allowed_hooks"]


def test_hook_changed_between_preflight_and_exec_is_skipped(tmp_path):
    root = make_catalog(tmp_path / "catalog")
    runtime = runtime_for_project(config_for(tmp_path, root, hooks_digest(root)), "p")
    (root / "check.sh").write_text("#!/bin/sh\ntouch " + str(tmp_path / "ran") + "\n")
    events = []
    asyncio.run(run_hooks(runtime, True, lambda kind, value: events.append(value)))
    assert events[0]["outcome"] == "skipped" and events[0]["reason"] == "hooks_not_trusted"
    assert not (tmp_path / "ran").exists()


def test_unreadable_hook_is_skipped(tmp_path):
    root = make_catalog(tmp_path / "catalog")
    digest = hooks_digest(root)
    config = config_for(tmp_path, root, digest)
    (root / "check.sh").chmod(0)
    with pytest.raises(OSError):
        hooks_digest(root)
    assert runtime_for_project(config, "p")["hooks_skipped"] == ["demo"]
    (root / "check.sh").chmod(0o700)
    (root / "harness.catalog.json").write_text("{not json")
    assert hooks_trusted(config["catalogs"][0]) is False


def test_legacy_trusted_catalog_reconfirms_once(tmp_path):
    root = make_catalog(tmp_path / "catalog")
    manager = Manager(tmp_path / "state")
    manager.settings["catalogs"] = [
        {"id": "demo", "root": str(root), "trusted": True, "kind": "folder", "namespace": "demo"}
    ]
    manager.settings["projects"] = [{"id": "p", "root": str(project_root(tmp_path)), "catalogs": ["demo"]}]
    for _ in range(2):  # a plain re-save never grants trust
        manager.settings = manager.validate(manager.settings)
        assert "hooks_sha256" not in manager.settings["catalogs"][0]
        assert runtime_for_project(catalog_config(manager), "p")["hooks_skipped"] == ["demo"]
    asyncio.run(change_pin(None, manager, {"action": "retrust", "project_id": "p", "catalog_id": "demo"}))
    manager.settings = manager.validate(manager.settings)
    assert manager.settings["catalogs"][0]["hooks_sha256"] == hooks_digest(root)
    assert runtime_for_project(catalog_config(manager), "p")["allowed_hooks"]
    other = make_catalog(tmp_path / "other")  # a new or moved catalog is trusted when it is added
    manager.settings["catalogs"][0]["root"] = str(other)
    manager.settings = manager.validate(manager.settings)
    assert manager.settings["catalogs"][0]["hooks_sha256"] == hooks_digest(other)


def test_digest_covers_manifest_and_hook_paths(tmp_path):
    root = make_catalog(tmp_path / "a", hooks=("one.sh", "two.sh"))
    base = hooks_digest(root)
    assert hooks_digest(root) == base
    manifest = root / "harness.catalog.json"
    manifest.write_text(manifest.read_text() + "\n")  # manifest bytes alone change it
    assert hooks_digest(root) != base
    manifest.write_text(manifest.read_text().rstrip("\n"))
    assert hooks_digest(root) == base
    (root / "two.sh").write_text("#!/bin/sh\nexit 1\n")  # hook bytes
    assert hooks_digest(root) != base
    renamed = make_catalog(tmp_path / "b", hooks=("one.sh", "three.sh"))  # hook path, same bytes
    assert hooks_digest(renamed) != base
    assert Path(renamed / "three.sh").read_bytes() == (tmp_path / "a/one.sh").read_bytes()


@pytest.mark.parametrize("swap", ["replace", "rewrite"])
def test_hook_swapped_after_the_hash_runs_the_verified_bytes(tmp_path, monkeypatch, swap):
    from agent_service import catalog_hooks

    root = make_catalog(tmp_path / "catalog", body="touch " + str(tmp_path / "verified") + "\n")
    runtime = runtime_for_project(config_for(tmp_path, root, hooks_digest(root)), "p")
    hook = root / "check.sh"
    real = catalog_hooks.hooks_trusted

    def check_then_swap(catalog, *args):
        trusted = real(catalog, *args)
        evil = "#!/bin/sh\ntouch " + str(tmp_path / "swapped") + "\n"
        if swap == "replace":
            (root / "evil.sh").write_text(evil)
            (root / "evil.sh").chmod(0o700)
            (root / "evil.sh").replace(hook)
        else:
            hook.write_text(evil)
        return trusted

    monkeypatch.setattr(catalog_hooks, "hooks_trusted", check_then_swap)
    events = []
    asyncio.run(run_hooks(runtime, True, lambda kind, value: events.append(value)))
    assert (tmp_path / "verified").exists()
    assert not (tmp_path / "swapped").exists()
    assert events[-1]["outcome"] == "done"
