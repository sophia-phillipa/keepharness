"""Keep adapter code, offline contracts and packaged model records in sync."""

import importlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("provider", ["codex", "claude", "gemini", "deepseek", "local"])
def test_adapter_spec_matches_implementation_and_has_model_records(provider):
    backend = importlib.import_module(f"adapters.{provider}.backend")
    folder = ROOT / "adapters" / provider / "specs"
    spec = json.loads((folder / "compatibility.json").read_text())
    assert spec["adapter_spec_revision"] == backend.SPEC_REVISION
    assert spec["provider"] == provider
    assert spec["reviewed_at"]
    assert spec["sources"] and all(url.startswith("https://") for url in spec["sources"])
    assert spec["models"]
    for model in spec["models"]:
        assert (folder / model["spec"]).is_file(), model
