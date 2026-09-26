"""project_root/project_path symlink boundaries (spec P5-15); cases not already covered
by tests/test_workspaces.py::test_archive_rejects_symlinks_and_limits or
tests/test_project_browser.py."""

import pytest

from agent_service import workspaces
from agent_service.tools import ToolError


def test_project_root_through_a_symlinked_parent_directory_is_unavailable(tmp_path):
    real = tmp_path / "real" / "proj"
    real.mkdir(parents=True)
    link = tmp_path / "link"
    link.symlink_to(tmp_path / "real", target_is_directory=True)
    spec = {"root": str(link / "proj")}
    with pytest.raises(ToolError) as excinfo:
        workspaces.project_root(spec, "root")
    assert str(excinfo.value) == "project_root_unavailable"


def test_project_path_denies_a_symlink_inside_a_real_root_pointing_outside(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret-ish")
    (root / "inner-link").symlink_to(outside)
    with pytest.raises(ToolError) as excinfo:
        workspaces.project_path(root, "inner-link")
    assert str(excinfo.value) == "symlink_denied"


def test_project_path_denies_traversal_above_the_root(tmp_path):
    root = tmp_path / "root"
    (root / "sub").mkdir(parents=True)
    with pytest.raises(ToolError) as excinfo:
        workspaces.project_path(root, "sub/../../x")
    assert str(excinfo.value) == "path_not_authorized"
