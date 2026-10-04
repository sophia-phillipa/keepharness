"""Optional declarative catalog provisioning, checked without executing discovery hooks."""

import hashlib
import hmac
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import venv
from pathlib import Path

from control.product import PRODUCT

from .catalog_pin import effective_catalogs, snapshot_catalogs
from .errors import APIError

MANIFEST_NAME = "harness.catalog.json"
KINDS = frozenset({"command", "agent", "skill", "rule", "context", "workflow"})


def _relative(value):
    if (
        not isinstance(value, str)
        or not value
        or Path(value).is_absolute()
        or ".." in Path(value).parts
    ):
        raise ValueError("invalid_catalog_path")
    return value


def _inside(root, value):
    root = Path(root).resolve()
    path = root / _relative(value)
    if not path.resolve().is_relative_to(root):
        raise ValueError("catalog_path_escape")
    return path


def load_manifest(root):
    path = Path(root) / MANIFEST_NAME
    if not path.exists():
        return None
    if not path.resolve().is_relative_to(Path(root).resolve()) or path.stat().st_size > 65536:
        raise ValueError("invalid_catalog_manifest")
    manifest = json.loads(path.read_text())
    allowed = {
        "version",
        "resources",
        "context",
        "rules",
        "runtime",
        "integrations",
        "writable_state",
        "allowed_hooks",
        "preflight",
        "cwd",
        "provisions_maintenance",
    }
    if not isinstance(manifest, dict) or manifest.get("version") != 1 or set(manifest) - allowed:
        raise ValueError("invalid_catalog_manifest")
    for key in ("context", "rules", "writable_state", "allowed_hooks"):
        values = manifest.get(key, [])
        if not isinstance(values, list) or len(values) > 64:
            raise ValueError("invalid_catalog_manifest")
        for value in values:
            _inside(root, value)
    if "cwd" in manifest:
        _inside(root, manifest["cwd"])
    if not isinstance(manifest.get("provisions_maintenance", False), bool):
        raise ValueError("invalid_catalog_manifest")
    resources = manifest.get("resources", {})
    if not isinstance(resources, dict) or set(resources) - KINDS:
        raise ValueError("invalid_catalog_resources")
    for values in resources.values():
        if not isinstance(values, list) or len(values) > 64:
            raise ValueError("invalid_catalog_resources")
        for value in values:
            _inside(root, value)
    runtime = manifest.get("runtime", {})
    if (
        not isinstance(runtime, dict)
        or set(runtime) - {"venv", "requirements"}
        or not isinstance(runtime.get("venv", False), bool)
    ):
        raise ValueError("invalid_catalog_runtime")
    if "requirements" in runtime:
        _inside(root, runtime["requirements"])
        if not runtime.get("venv"):
            raise ValueError("catalog_requirements_need_venv")
    integrations = manifest.get("integrations", [])
    if not isinstance(integrations, list) or any(
        not isinstance(item, dict) for item in integrations
    ):
        raise ValueError("invalid_catalog_integrations")
    checks = manifest.get("preflight", [])
    if not isinstance(checks, list) or len(checks) > 64:
        raise ValueError("invalid_catalog_preflight")
    for check in checks:
        if not isinstance(check, dict) or set(check) - {
            "file",
            "executable",
            "environment",
            "hint",
        }:
            raise ValueError("invalid_catalog_preflight")
        if len(set(check) & {"file", "executable", "environment"}) != 1 or any(
            not isinstance(value, str) or not value for value in check.values()
        ):
            raise ValueError("invalid_catalog_preflight")
        if "file" in check:
            _inside(root, check["file"])
    return manifest


def hooks_digest(root):
    """Digest of the manifest bytes and each declared hook (relpath, sha256); any error raises."""
    raw = (Path(root) / MANIFEST_NAME).read_bytes()
    manifest = load_manifest(root)
    hooks = sorted(
        (value, hashlib.sha256(_inside(root, value).read_bytes()).hexdigest())
        for value in manifest.get("allowed_hooks", [])
    )
    return hashlib.sha256(
        json.dumps([hashlib.sha256(raw).hexdigest(), hooks]).encode()
    ).hexdigest()


def require_trusted_hooks(catalog):
    """Block unless the catalog's hooks still match the digest the owner trusted in Admin."""
    try:
        current = hooks_digest(catalog["root"])
    except (OSError, ValueError):
        raise APIError("catalog_hooks_changed") from None
    if not hmac.compare_digest(current, str(catalog.get("hooks_sha256") or "")):
        raise APIError("catalog_hooks_changed")


def _runtime_root(root, state_dir, catalog_id):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", catalog_id):
        raise ValueError("invalid_catalog_id")
    state = Path(state_dir).resolve()
    target = state / "catalog_runtime" / catalog_id
    if target.resolve().is_relative_to(Path(root).resolve()) or not target.resolve().is_relative_to(
        state
    ):
        raise ValueError("catalog_state_inside_source")
    # Even links remaining inside state can alias another catalog's writable state.
    for path in (state / "catalog_runtime", target):
        if path.is_symlink():
            raise ValueError("catalog_state_symlink")
    return target


def _runtime_options(root, manifest, state_dir, catalog_id):
    target = _runtime_root(root, state_dir, catalog_id)
    roots = [_inside(target, value) for value in manifest.get("writable_state", [])]
    for path in roots:
        if any(part.is_symlink() for part in (path, *path.parents) if part.is_relative_to(target)):
            raise ValueError("catalog_state_symlink")
    environment = {
        PRODUCT.env_prefix + "_CATALOG_STATE_" + catalog_id.upper().replace("-", "_"): str(target)
    }
    if manifest.get("runtime", {}).get("venv"):
        python_home = target / "venv"
        if python_home.is_symlink() or (python_home / "bin").is_symlink():
            raise ValueError("catalog_state_symlink")
        environment.update(
            VIRTUAL_ENV=str(python_home),
            PATH=str(python_home / "bin") + os.pathsep + os.environ.get("PATH", ""),
        )
    return {
        "environment": environment,
        "cwd": str(_inside(root, manifest["cwd"])) if "cwd" in manifest else None,
        "writable_roots": [str(path) for path in roots],
        "allowed_hooks": [str(_inside(root, value)) for value in manifest.get("allowed_hooks", [])],
        "contexts": [str(_inside(root, value)) for value in manifest.get("context", [])],
        "rules": [str(_inside(root, value)) for value in manifest.get("rules", [])],
    }


def _dependency_stamp(runtime_root):
    stamp = runtime_root / "requirements.sha256"
    if stamp.is_symlink() or (stamp.exists() and not stamp.is_file()):
        raise ValueError("invalid_catalog_dependency_stamp")
    return stamp


def preflight(root, manifest, state_dir, catalog_id):
    options = _runtime_options(root, manifest, state_dir, catalog_id)
    problems = []
    for relative, path in zip(manifest.get("writable_state", []), options["writable_roots"]):
        if not Path(path).is_dir():
            problems.append("Provision the catalog writable state in Admin: " + relative)
    for key in ("context", "rules", "allowed_hooks"):
        for value in manifest.get(key, []):
            if not _inside(root, value).is_file():
                problems.append("Missing catalog " + key + ": " + value)
            elif key == "allowed_hooks" and not os.access(_inside(root, value), os.X_OK):
                problems.append("Make the declared catalog hook executable: " + value)
    if options["cwd"] and not Path(options["cwd"]).is_dir():
        problems.append("Catalog working directory is missing.")
    if "VIRTUAL_ENV" in options["environment"]:
        python_home = Path(options["environment"]["VIRTUAL_ENV"])
        if not (python_home / "bin/python").is_file():
            problems.append("Provision the catalog Python environment in Admin.")
        requirements = manifest.get("runtime", {}).get("requirements")
        if requirements:
            import hashlib

            path = _inside(root, requirements)
            stamp = _dependency_stamp(python_home.parent)
            digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""
            if not digest or not stamp.is_file() or stamp.read_text() != digest:
                problems.append("Provision the catalog Python dependencies in Admin.")
    for check in manifest.get("preflight", []):
        ready = (
            _inside(root, check["file"]).exists()
            if "file" in check
            else shutil.which(check["executable"], path=options["environment"].get("PATH"))
            if "executable" in check
            else os.environ.get(check["environment"])
        )
        if not ready:
            problems.append(
                check.get(
                    "hint",
                    "Missing catalog prerequisite: "
                    + next(value for key, value in check.items() if key != "hint"),
                )
            )
    return problems


def materialize_runtime(root, manifest, state_dir, catalog_id):
    options = _runtime_options(root, manifest, state_dir, catalog_id)
    for path in options["writable_roots"]:
        Path(path).mkdir(parents=True, exist_ok=True)
    if "VIRTUAL_ENV" in options["environment"]:
        python_home = Path(options["environment"]["VIRTUAL_ENV"])
        requirements = manifest.get("runtime", {}).get("requirements")
        if not (python_home / "bin/python").is_file():
            venv.EnvBuilder(with_pip=False).create(python_home)
        if requirements:
            import hashlib

            path = _inside(root, requirements)
            stamp = _dependency_stamp(python_home.parent)
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "--python",
                    str(python_home / "bin/python"),
                    "install",
                    "-r",
                    str(path),
                ],
                cwd=root,
                check=True,
                capture_output=True,
                timeout=300,
            )
            descriptor, temporary = tempfile.mkstemp(
                prefix=".requirements-", dir=python_home.parent
            )
            try:
                with os.fdopen(descriptor, "w") as stream:
                    stream.write(hashlib.sha256(path.read_bytes()).hexdigest())
                os.replace(temporary, stamp)
            finally:
                Path(temporary).unlink(missing_ok=True)
    return options


def runtime_for_project(config, project_id):
    project = config["projects"][project_id]
    state = config.get("control_state_dir", config.get("state_dir", "state"))
    result = {
        "environment": {},
        "cwd": None,
        "writable_roots": [],
        "allowed_hooks": [],
        "contexts": [],
        "rules": [],
        "read_only_roots": [],
        "hook_catalogs": [],
        "catalogs": snapshot_catalogs(config, project),
    }
    for catalog in effective_catalogs(config, project):
        if catalog.get("pin"):
            result["read_only_roots"].append(catalog["root"])
        manifest = load_manifest(catalog["root"])
        if not manifest:
            continue
        hooked = bool(manifest.get("allowed_hooks")) and not catalog.get("pin")
        if hooked:
            require_trusted_hooks(catalog)
        problems = preflight(catalog["root"], manifest, state, catalog["id"])
        if problems:
            raise APIError("catalog_preflight_failed: " + "; ".join(problems))
        options = _runtime_options(catalog["root"], manifest, state, catalog["id"])
        if options["cwd"]:
            if result["cwd"] and result["cwd"] != options["cwd"]:
                raise APIError("catalog_cwd_conflict")
            result["cwd"] = options["cwd"]
        for key, value in options["environment"].items():
            if key in result["environment"] and result["environment"][key] != value:
                raise APIError("catalog_environment_conflict")
            result["environment"][key] = value
        for key in ("writable_roots", "contexts", "rules"):
            result[key].extend(options[key])
        if hooked:
            result["allowed_hooks"].extend(options["allowed_hooks"])
            result["hook_catalogs"].append(
                {"root": catalog["root"], "hooks_sha256": catalog["hooks_sha256"]}
            )
    return result
