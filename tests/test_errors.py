import pickle
import subprocess
import sys
from pathlib import Path

import adapters
from agent_service import app, errors, tools
from agent_service.errors import APIError, HarnessError, ToolError
from agent_service.resources import ResourceError

ROOT = Path(__file__).resolve().parents[1]


def test_error_classes_share_the_harness_base():
    assert issubclass(APIError, HarnessError)
    assert issubclass(ToolError, HarnessError)
    assert HarnessError.__bases__ == (Exception,)
    assert not issubclass(ResourceError, HarnessError)


def test_every_importer_sees_the_same_class_objects():
    assert app.APIError is errors.APIError
    assert tools.ToolError is errors.ToolError
    assert adapters.ToolError is errors.ToolError


def test_str_and_args_match_a_plain_exception():
    assert str(ToolError("x")) == "x"
    assert ToolError("x").args == ("x",)
    assert APIError("x", 403).args == ("x", 403)
    assert str(APIError("x", 403)) == str(Exception("x", 403))
    error = APIError("busy", 429, 5)
    assert (error.code, error.status, error.retry_after) == ("busy", 429, 5)
    assert (APIError("x").status, APIError("x").retry_after) == (422, None)


def test_errors_survive_pickling():
    for error in (ToolError("tool_failed"), APIError("denied", 403), APIError("busy", 429, 1)):
        clone = pickle.loads(pickle.dumps(error))
        assert type(clone) is type(error)
        assert clone.args == error.args
        assert str(clone) == str(error)
        assert (clone.code, clone.status, clone.retry_after) == (
            error.code,
            error.status,
            error.retry_after,
        )


def test_tools_imports_standalone_in_the_sandbox_bridge(tmp_path):
    # adapters.shared.scoped copies these files beside project_mcp.py, outside the package.
    for name in ("tools.py", "errors.py"):
        (tmp_path / name).write_bytes((ROOT / "agent_service" / name).read_bytes())
    result = subprocess.run(
        [sys.executable, "-c", "import tools; print(tools.ToolError.__mro__[1].__name__)"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "HarnessError"
