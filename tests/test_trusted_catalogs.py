"""Trusted catalog settings and the public catalog discovery delegate."""

import copy

import pytest

from agent_service.catalog import catalog
from control import runtime_config
from control.server import Manager


def test_control_validates_and_plumbs_project_catalogs(tmp_path):
    state = tmp_path / "state"
    project = tmp_path / "project"
    catalog_root = tmp_path / "catalog"
    project.mkdir()
    catalog_root.mkdir()
    manager = Manager(state)
    settings = copy.deepcopy(manager.settings)
    settings["catalogs"] = [
        {
            "id": "demo",
            "root": str(catalog_root),
            "kind": "folder",
            "trusted": True,
            "namespace": "demo",
        }
    ]
    settings["projects"] = [
        {"id": "p", "root": str(project), "catalogs": ["demo"]}
    ]
    validated = manager.validate(settings)
    config = runtime_config.base_config(validated, state, 8094, "http://local/", {})
    assert config["catalogs"] == validated["catalogs"]
    assert config["projects"]["p"]["catalogs"] == ["demo"]

    invalid = copy.deepcopy(settings)
    invalid["projects"][0]["catalogs"] = ["missing"]
    with pytest.raises(ValueError, match="Catalog not registered"):
        manager.validate(invalid)


def test_catalog_delegates_to_resource_discovery(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    skill = project / ".agents/skills/check/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: check\ndescription: Check things.\n---\nDo it")
    config = {
        "projects": {"p": {"root": str(project), "catalogs": []}},
        "services": {
            "codex": {
                "enabled": True,
                "mode": "native",
                "models": ["gpt-6-astra"],
                "projects": ["p"],
            }
        },
    }
    result = catalog(config, config["projects"]["p"], "p")
    assert result["items"][0]["resource_id"] == "project/p/.agents/skills/check/SKILL.md"
    assert result["skills"][0]["name"] == "check"
