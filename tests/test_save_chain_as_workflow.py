import json

import pytest
from test_workflow_schema import workflow

from agent_service import workflows


def test_saves_successful_chain_only_in_project_collection(tmp_path):
    plan = workflows.validate_workflow(workflow())
    path = workflows.save_chain_as_workflow({"root": str(tmp_path)}, plan, "saved", successful=True)
    assert path == tmp_path / "workflows" / "saved.json"
    assert json.loads(path.read_text())["id"] == "saved"
    assert workflows.load_workflow(path)["steps"][0]["invocation"]["args"] == "  Keep\nbytes "
    with pytest.raises(workflows.WorkflowError):
        workflows.save_chain_as_workflow({"root": str(tmp_path)}, plan, "saved", successful=True)


@pytest.mark.parametrize("identifier,successful", [("../escape", True), ("okay", False)])
def test_refuses_unsuccessful_or_escaping_save(tmp_path, identifier, successful):
    with pytest.raises(workflows.WorkflowError):
        workflows.save_chain_as_workflow(
            {"root": str(tmp_path)}, workflow(), identifier, successful=successful
        )


def test_refuses_symlink_and_catalog_destinations(tmp_path):
    catalog = tmp_path / "catalog"
    catalog.mkdir()
    root = tmp_path / "project"
    root.mkdir()
    (root / "workflows").symlink_to(catalog)
    with pytest.raises(workflows.WorkflowError):
        workflows.save_chain_as_workflow({"root": str(root)}, workflow(), "saved", successful=True)
    with pytest.raises(workflows.WorkflowError):
        workflows.save_chain_as_workflow(
            {"root": str(catalog)},
            workflow(),
            "saved",
            successful=True,
            catalogs=[{"root": str(catalog), "trusted": True}],
        )
