import json

import pytest
from test_workflow_schema import workflow

from agent_service import resources, workflows

AVAILABLE = [
    {
        "backend": "codex",
        "model": "m",
        "efforts": ["low"],
        "mode": "native",
        "permissions": {"read": True},
        "integrations": [],
    }
]


@pytest.mark.parametrize(
    "field,value",
    [
        ("backend", "claude"),
        ("model", "missing"),
        ("effort", "high"),
        ("requires", {"permissions": ["write"]}),
        ("requires", {"integrations": ["jira"]}),
        ("requires", {"operations": ["jira.create_issue"]}),
        ("requires", {"mode": "sdk"}),
    ],
)
def test_candidates_and_requirements_fail_closed(field, value):
    value_workflow = workflow()
    value_workflow["steps"][0][field] = value
    with pytest.raises(workflows.WorkflowError):
        workflows.validate_workflow(value_workflow, AVAILABLE)


def test_resource_resolution_revision_and_unavailable():
    value = workflow()
    value["steps"][0].update(kind="skill", resource_id="project/p/.agents/skills/review/SKILL.md")
    item = {
        "resource_id": value["steps"][0]["resource_id"],
        "kind": "skill",
        "revision": "abc",
        "selectable": True,
        "name": "review",
    }
    resolved = workflows.validate_workflow(value, AVAILABLE, [item])
    assert resolved["steps"][0]["resource_revision"] == "abc"
    item["selectable"] = False
    with pytest.raises(workflows.WorkflowError):
        workflows.validate_workflow(value, AVAILABLE, [item])


def test_discovery_includes_workflow_even_without_native_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    folder = tmp_path / "project" / "workflows"
    folder.mkdir(parents=True)
    (folder / "review.json").write_text(json.dumps(workflow()))
    config = {
        "projects": {"p": {"root": str(folder.parent)}},
        "services": {"codex": {"mode": "sdk"}},
    }
    found = resources.discover(config, "p", "codex")["items"]
    assert found[0]["kind"] == "workflow"
    assert found[0]["resource_id"] == "project/p/workflows/review.json"


def test_publication_is_explicitly_unenforced_until_executor(tmp_path):
    value = workflow()
    value["steps"][0]["publish"] = True
    assert workflows.validate_workflow(value)["steps"][0]["enforcement"] == "unenforced"


def test_declared_resource_dependencies_are_current_bounded_and_private(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    root = tmp_path / "project"
    folder = root / ".agents/skills/review"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        '---\nname: review\ndependencies: ["knowledge.md"]\n---\nReview'
    )
    dependency = folder / "knowledge.md"
    dependency.write_text("first")
    config = {"projects": {"p": {"root": str(root)}}, "services": {"codex": {"mode": "native"}}}
    first = resources.discover(config, "p", "codex")["items"][0]
    assert first["deps_revisions"]
    assert "_dependency_texts" not in first
    dependency.write_text("second")
    second = resources.discover(config, "p", "codex", private=True)["items"][0]
    assert first["revision"] == second["revision"]
    assert first["deps_revisions"] != second["deps_revisions"]
    assert list(second["_dependency_texts"].values()) == ["second"]
    dependency.unlink()
    dependency.symlink_to(tmp_path / "outside")
    (tmp_path / "outside").write_text("outside")
    assert resources.discover(config, "p", "codex")["items"] == []


@pytest.mark.parametrize(
    "value,valid", [({"ok": True}, True), ({"ok": 1}, False), ({}, False), (None, False)]
)
def test_small_result_schema(value, valid):
    schema = {"type": "object", "required": ["ok"], "properties": {"ok": {"type": "boolean"}}}
    assert workflows.validate_result(value, schema) is valid
