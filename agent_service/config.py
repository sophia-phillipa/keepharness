"""Read-only constants, package paths and runtime-config checks of the agent service."""

import json
from pathlib import Path
from types import MappingProxyType

from . import maestro

PACKAGE_DIR = Path(__file__).resolve().parent
REPOSITORY_ROOT = PACKAGE_DIR.parent
VERSION_FILE = PACKAGE_DIR / "VERSION"

TERMINAL = frozenset({"completed", "failed", "cancelled", "interrupted"})
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
        # Maestro may use an isolated local step internally, but its own session is
        # not a separate provider session the user can choose a transport for.
        "maestro": ("native",),
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
    executor = active_executors.get(row["id"]) if row["state"] == "running" else None
    if backend == "maestro" and executor:
        backend, model = executor
    elif backend in ("auto", "maestro"):
        return not any(
            item["backend"] == "codex" for item in maestro.candidates(candidate, row["project"])
        )
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
