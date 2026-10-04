"""Read-only constants, package paths and runtime-config checks of the agent service."""

import json
from pathlib import Path
from types import MappingProxyType

from . import maestro

PACKAGE_DIR = Path(__file__).resolve().parent
REPOSITORY_ROOT = PACKAGE_DIR.parent
VERSION_FILE = PACKAGE_DIR / "VERSION"

TERMINAL = frozenset({"completed", "failed", "cancelled", "interrupted"})
# Per-project storage caps (decision D33): kept runs and uploaded bytes, each identical upload
# counted once. Settings warns from STORAGE_WARNING_RATIO of either cap.
MAX_PROJECT_RUNS = 1000
MAX_PROJECT_UPLOAD_BYTES = 2 * 1024**3
STORAGE_WARNING_RATIO = 0.8
KINDS = frozenset(
    {
        "infer",
        "repository_read",
        "repository_search",
        "web_fetch",
        "web_search",
        "test",
        "propose_patch",
    }
)
PREVIEW_MEDIA_TYPES = frozenset({"image/png", "image/jpeg", "image/webp"})
EXECUTION_MODES = MappingProxyType(
    {
        # "native" means the provider owns the host-side session.  The local adapter
        # always wraps Codex in bubblewrap, so exposing it as native would lie.
        "codex": ("native", "scoped"),
        "claude": ("native", "scoped"),
        "gemini": ("native",),
        "deepseek": ("native",),
        "local": ("scoped",),
    }
)


def validate_runtime_config(candidate):
    """Whether a control-plane runtime config has the shape the service relies on."""
    if not all(isinstance(candidate.get(key), dict) for key in ("services", "projects", "clients")):
        return False
    for spec in candidate["services"].values():
        if (
            not isinstance(spec, dict)
            or type(spec.get("enabled")) is not bool
            or not isinstance(spec.get("models"), list)
            or not all(isinstance(model, str) for model in spec["models"])
            or not isinstance(spec.get("projects"), list)
            or not all(isinstance(project, str) for project in spec["projects"])
            or not isinstance(spec.get("permissions", {}), dict)
            or not all(isinstance(value, bool) for value in spec.get("permissions", {}).values())
        ):
            return False
    if any(not isinstance(spec, dict) for spec in candidate["projects"].values()):
        return False
    for spec in candidate["clients"].values():
        if (
            not isinstance(spec, dict)
            or not isinstance(spec.get("sha256"), str)
            or not isinstance(spec.get("projects"), list)
        ):
            return False
    return all(
        key not in candidate or isinstance(candidate[key], dict) for key in candidate["services"]
    )


def runtime_job_affected(config, active_executors, row, candidate):
    """Whether a queued/running job lost its selected executor or grants."""
    data = json.loads(row["payload"])
    backend = data.get("backend")
    model = data.get("model")
    client = candidate.get("clients", {}).get(row["owner"], {})
    if row["project"] not in candidate.get("projects", {}) or row["project"] not in client.get(
        "projects", []
    ):
        return True
    project = row["project"]
    old_project = config.get("projects", {}).get(project, {})
    new_project = candidate.get("projects", {}).get(project, {})
    for key in ("root", "additional_roots", "catalogs", "catalog_pins"):
        if old_project.get(key) != new_project.get(key):
            return True

    def credential_scope(current):
        bindings = [
            item
            for item in current.get("integration_bindings", [])
            if item.get("project_id") == project
        ]
        names = {item.get("integration") for item in bindings}
        return {
            "bindings": bindings,
            "integrations": [
                item for item in current.get("integrations", []) if item.get("integration") in names
            ],
            "effects": [
                item
                for item in current.get("effect_integrations", [])
                if item.get("integration") in names
            ],
            "vault": current.get("secret_vault_path") if bindings else None,
            "revision": current.get("secret_vault_revision") if bindings else None,
            "binding_revisions": {
                item["credential_binding"]: current.get("secret_binding_revisions", {}).get(
                    item["credential_binding"]
                )
                for item in bindings
            },
        }

    if credential_scope(config) != credential_scope(candidate):
        return True
    catalog_ids = set(old_project.get("catalogs", []))
    if [item for item in config.get("catalogs", []) if item.get("id") in catalog_ids] != [
        item for item in candidate.get("catalogs", []) if item.get("id") in catalog_ids
    ]:
        return True
    orchestrated = (
        data.get("_declared_workflow")
        or len(data.get("invocations", [])) > 1
        or any(item.get("kind") == "workflow" for item in data.get("invocations", []))
    )
    if (
        row["state"] == "running"
        and orchestrated
        and config.get("local", {}).get("model_roots", {})
        != candidate.get("local", {}).get("model_roots", {})
    ):
        return True
    executor = active_executors.get(row["id"]) if row["state"] == "running" else None
    if executor:
        backend, model = executor
    old = config.get("services", {}).get(backend, {})
    new = candidate.get("services", {}).get(backend, {})
    if not new.get("enabled") or model not in new.get("models", []):
        return True
    if maestro.model_permissions(
        config, backend, model, row["project"]
    ) != maestro.model_permissions(candidate, backend, model, row["project"]):
        return True
    if old.get("integrations", []) != new.get("integrations", []) or config.get(
        "provider_revisions", {}
    ).get(backend) != candidate.get("provider_revisions", {}).get(backend):
        return True
    if backend == "local":
        old_local = config.get("local", {})
        new_local = candidate.get("local", {})
        old_runtime = {
            key: value
            for key, value in old_local.items()
            if key not in ("local_models", "model_roots")
        }
        new_runtime = {
            key: value
            for key, value in new_local.items()
            if key not in ("local_models", "model_roots")
        }
        return (
            old_runtime != new_runtime
            or old_local.get("model_roots", {}).get(model)
            != new_local.get("model_roots", {}).get(model)
            or old_local.get("local_models", {}).get(model)
            != new_local.get("local_models", {}).get(model)
        )
    return config.get(backend, {}) != candidate.get(backend, {})
