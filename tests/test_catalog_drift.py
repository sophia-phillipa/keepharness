from agent_service.catalog_drift import compare_resources


def test_drift_compares_identity_revisions_dependencies_and_commits():
    before = {
        "resource_id": "catalog/demo/commands/task.md",
        "revision": "a",
        "deps_revisions": {"context.md": "x"},
        "catalog_commit": "1",
    }
    after = {**before, "deps_revisions": {"context.md": "y"}, "catalog_commit": "2"}
    rows = compare_resources({"project-a": [before], "project-b": [after]})
    assert rows[0]["resource_id"] == before["resource_id"]
    assert rows[0]["drift"] is True
    assert rows[0]["locations"] == ["project-a", "project-b"]
    assert compare_resources({"a": [before], "b": [dict(before)]})[0]["drift"] is False
    assert before["deps_revisions"] == {"context.md": "x"}
