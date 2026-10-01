"""Model mounts protect the effective private state, including resolved aliases."""
import pytest

from control.local_models import validate_profile


@pytest.mark.parametrize("relation", ["same", "parent", "child", "symlink"])
def test_effective_state_is_never_a_model_root(tmp_path, relation):
    state = tmp_path / "custom" / "private"
    state.mkdir(parents=True)
    child = state / "runs"
    child.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(state, target_is_directory=True)
    root = {"same": state, "parent": state.parent, "child": child, "symlink": alias}[relation]
    binary = tmp_path / "llama-server"
    model = tmp_path / "test.gguf"
    binary.touch()
    model.touch()
    with pytest.raises(ValueError, match="credentials"):
        validate_profile({"binary": str(binary), "model_file": str(model), "allowed_roots": [str(root)]}, state_dir=state)


def test_unrelated_root_remains_usable(tmp_path):
    state = tmp_path / "private"
    state.mkdir()
    root = tmp_path / "workspace"
    root.mkdir()
    binary = tmp_path / "llama-server"
    model = tmp_path / "test.gguf"
    binary.touch()
    model.touch()
    assert validate_profile({"binary": str(binary), "model_file": str(model), "allowed_roots": [str(root)]}, state_dir=state)["allowed_roots"] == [str(root)]
