"""Read-only constants and package paths of the agent service."""

from pathlib import Path
from types import MappingProxyType

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


def preview_metadata(file_id, pages):
    media_type = next(
        (
            page.get("media_type")
            for page in pages
            if not page.get("frame") and page.get("media_type") in PREVIEW_MEDIA_TYPES
        ),
        None,
    )
    return (
        {"preview_url": "/v1/files/" + file_id + "/preview", "media_type": media_type}
        if media_type
        else {}
    )
