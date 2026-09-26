"""Environment variable reader with a deprecated-legacy-alias fallback.

Public variables are named ``TAIL_HARNESS_<NAME>``. A handful of older, unprefixed names are
still accepted so existing deployments keep working; using one logs a one-time deprecation
warning per name. See dossier/naming-model.md for the full rationale.
"""

from __future__ import annotations

import os
import warnings
from pathlib import Path

# Maps the suffix passed to read() to the legacy, unprefixed environment variable it replaces.
_LEGACY_ALIASES = {
    "AGENT_URL": "LOCAL_AGENT_URL",
    "AGENT_CLIENT": "LOCAL_AGENT_CLIENT",
    "AGENT_CONFIG": "LOCAL_AGENT_CONFIG",
    "VENV": "TH_VENV",
}

_WARNED: set[str] = set()


def read(name: str, default: str | None = None) -> str | None:
    """Read ``TAIL_HARNESS_<name>``, falling back to its deprecated legacy alias, if any."""
    preferred = f"TAIL_HARNESS_{name}"
    if preferred in os.environ:
        return os.environ[preferred]
    legacy = _LEGACY_ALIASES.get(name)
    if legacy and legacy in os.environ:
        if name not in _WARNED:
            _WARNED.add(name)
            warnings.warn(
                f"{legacy} is deprecated; set {preferred} instead.",
                DeprecationWarning,
                stacklevel=2,
            )
        return os.environ[legacy]
    return default


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
# Data root for local model runtimes, downloads and keys (``<root>/local_ai``).
LOCAL_AI_ROOT = Path(read("ROOT", str(REPOSITORY_ROOT)))
LOCAL_AI = LOCAL_AI_ROOT / "local_ai"
