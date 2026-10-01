"""Resource identity, availability and native reachability at admission."""

import json

import pytest
from test_resources import catalog_cfg, put
from test_workspaces import config

from agent_service import resources
from agent_service.app import Service
from agent_service.catalog_pin import snapshot_catalogs


def test_missing_git_catalog_keeps_project_resources(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    root = tmp_path / "project"
    put(root, ".codex/agents/review.toml", 'name="review"\ndeveloper_instructions="Review"')
    cfg = catalog_cfg(root, tmp_path / "missing")
    cfg["catalogs"][0]["kind"] = "git"
    found = resources.discover(cfg, "p", "codex")
    assert any(item["name"] == "review" for item in found["items"])
    assert any("catalog" in warning.lower() for warning in found["warnings"])
    snapshot = snapshot_catalogs(cfg, cfg["projects"]["p"])
    assert snapshot[0]["error"] == "catalog_git_failed"
    assert snapshot[0]["dirty"] is None


def test_canonical_cross_kind_chain_keeps_exact_ids_and_arguments(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    root = tmp_path / "project"
    put(root, ".codex/agents/review.toml", 'name="review"\ndeveloper_instructions="Review"')
    put(root, ".agents/skills/review/SKILL.md", "---\nname: review\n---\nReview")
    cfg = config(tmp_path / "state")
    cfg["projects"]["p"]["root"] = str(root)
    service = Service(cfg)
    identity = ("a", cfg["clients"]["a"])
    try:
        items = [
            item
            for item in service.resource_catalog(identity, "p", "codex", "gpt-6-astra")["items"]
            if item["name"] == "review"
        ]
        assert len(items) == 2
        chain = [
            dict(
                kind=item["kind"],
                resource_id=item["resource_id"],
                args="task-" + item["kind"],
                order=index,
                mode=item["mode"],
            )
            for index, item in enumerate(items)
        ]
        job = service.submit(
            identity,
            dict(
                project_id="p",
                backend="codex",
                model="gpt-6-astra",
                effort="low",
                prompt="",
                invocations=chain,
            ),
        )["job_id"]
        payload = json.loads(service.job(identity, job)["payload"])
        assert [
            (v["resource_id"], v["args"].strip(), v["order"]) for v in payload["invocations"]
        ] == [(v["resource_id"], v["args"], v["order"]) for v in chain]
    finally:
        service.db.close()


@pytest.mark.parametrize("linked", [False, True, "file"])
def test_catalog_skill_requires_native_discovery_path(tmp_path, monkeypatch, linked):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    root = tmp_path / "project"
    root.mkdir()
    catalog = tmp_path / "catalog"
    skill = put(catalog, "skills/catalog-only/SKILL.md", "---\nname: catalog-only\n---\nReview")
    if linked:
        native = home / ".agents/skills"
        native.mkdir(parents=True)
        if linked == "file":
            (native / "catalog-only").mkdir()
            (native / "catalog-only/SKILL.md").symlink_to(skill)
        else:
            (native / "catalog-only").symlink_to(skill.parent, target_is_directory=True)
    cfg = catalog_cfg(root, catalog)
    item = next(
        i
        for i in resources.discover(cfg, "p", "codex", private=True)["items"]
        if i["name"] == "catalog-only"
    )
    assert item["selectable"] is (linked is True)
    if linked is not True:
        assert "native skill" in item["unavailable_reason"].lower()
