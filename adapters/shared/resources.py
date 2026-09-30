"""Execution-local regular copies of already selected and revision-checked resources."""

import hashlib
from pathlib import Path

from agent_service.tools import ToolError


def copy_resource(item, directory, suffix):
    text = item.get("_text")
    if not isinstance(text, str) or hashlib.sha256(text.encode()).hexdigest() != item["revision"]:
        raise ToolError("resource_changed")
    # The digest keeps names inside the private temporary directory regardless of metadata.
    name = hashlib.sha256(item["resource_id"].encode()).hexdigest() + suffix
    path = Path(directory) / name
    path.write_text(text)
    path.chmod(0o600)
    return path
