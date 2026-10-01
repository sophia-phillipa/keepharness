"""Build/install two identities in isolated homes using the documented generator."""
import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, replace
from pathlib import Path

from control.product import PRODUCT


def run(arguments, *, cwd, env):
    result = subprocess.run(arguments, cwd=cwd, env=env, capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-4000:]
    return result.stdout


def test_second_identity_build_and_install_side_by_side(tmp_path):
    repository = Path(__file__).resolve().parents[1]
    source = tmp_path / "source"
    shutil.copytree(repository, source, ignore=shutil.ignore_patterns(".git", ".venv", "__pycache__", "*.egg-info", ".pytest_cache", ".ruff_cache", "dist", "build", "graphify-out", "node_modules"))
    home = tmp_path / "home"
    home.mkdir()
    environment = {**os.environ, "HOME": str(home), "PYTHONPATH": "", "PIP_DISABLE_PIP_VERSION_CHECK": "1"}
    synthetic = replace(PRODUCT, name="Synthetic Harness", slug="synthetic-harness", env_prefix="SYNTHETIC_HARNESS", state_dir=".local/share/synthetic-harness", config_dir=".config/synthetic-harness", mcp_name="synthetic-harness", lineage="synthetic-harness")
    config = tmp_path / "identity.json"
    config.write_text(json.dumps(asdict(synthetic)))
    for identity in (PRODUCT, synthetic):
        if identity == synthetic:
            run([sys.executable, "-m", "control.product", "--identity", str(config)], cwd=source, env=environment)
        wheels = tmp_path / (identity.slug + "-wheels")
        run([sys.executable, "-m", "build", "--wheel", "--no-isolation", "--outdir", str(wheels)], cwd=source, env=environment)
        wheel = next(wheels.glob("*.whl"))
        assert wheel.name.startswith(identity.slug.replace("-", "_") + "-")
        target = tmp_path / identity.slug
        run([sys.executable, "-m", "pip", "install", "--no-deps", "--target", str(target), str(wheel)], cwd=tmp_path, env=environment)
        code = """
import json
from pathlib import Path
from control.product import PRODUCT, ensure_lineage
from control.install import files
from control import env
from agent_service.mcp_bridge import mcp, config
assert PRODUCT.slug == EXPECTED
assert env.read('AGENT_URL') == 'https://identity.invalid'
assert mcp.name == PRODUCT.mcp_name
ensure_lineage(PRODUCT.state_path())
assert PRODUCT.name in (Path(__import__('control').__file__).parent / 'index.html').read_text()
assert all(PRODUCT.slug in str(p) for p in files(Path.home(), '/fixture/python'))
print(json.dumps({'slug': PRODUCT.slug, 'state': str(PRODUCT.state_path())}))
""".replace("EXPECTED", repr(identity.slug))
        effective = {**environment, "PYTHONPATH": str(target), identity.env_prefix + "_AGENT_URL": "https://identity.invalid"}
        result = json.loads(run([sys.executable, "-c", code], cwd=tmp_path, env=effective))
        assert result["state"] == str(identity.state_path(home))
        assert (target / "bin" / identity.slug).is_file()
        assert (target / "bin" / (identity.slug + "-mcp")).is_file()
        run([str(target / "bin" / identity.slug), "--help"], cwd=tmp_path, env=effective)
    assert PRODUCT.state_path(home).is_dir() and synthetic.state_path(home).is_dir()
