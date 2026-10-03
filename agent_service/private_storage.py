"""Canonical runtime storage boundaries for registration, import and scoped workers."""

import os
from pathlib import Path

from .errors import APIError

MAX_PRIVATE_ENTRIES = 100_000


def private_roots(config, root):
    return list(
        dict.fromkeys(
            [
                Path(root),
                *(
                    Path(config[key])
                    for key in (
                        "control_state_dir",
                        "sessions_dir",
                        "effect_credentials_path",
                        "secret_vault_path",
                    )
                    if config.get(key)
                ),
            ]
        )
    )


def private_paths(config, root, *, follow_links=False, skip_catalogs=False):
    """Visit relocated storage once per directory inode, including link entries."""
    pending = private_roots(config, root)
    seen = set()
    control = Path(config["control_state_dir"]) if config.get("control_state_dir") else None
    catalogs = (
        {control / name for name in ("catalog_pins", "catalog_runtime")} if control else set()
    )
    count = 0
    while pending:
        path = pending.pop()
        count += 1
        if count > MAX_PRIVATE_ENTRIES:
            raise OSError("Private storage inventory limit exceeded")
        yield path
        if skip_catalogs and path in catalogs:
            continue
        try:
            stat = path.stat()
            identity = (stat.st_dev, stat.st_ino)
            if not path.is_dir() or identity in seen or (path.is_symlink() and not follow_links):
                continue
            seen.add(identity)
            pending.extend(path.iterdir())
        except FileNotFoundError:
            # Concurrent SQLite sidecar cleanup and not-yet-created stores are normal.
            continue


def validate_attachment_source(config, root, source, stream):
    """Check the opened inode as well as paths before copying any private bytes."""
    opened = os.fstat(stream.fileno())
    target = source.resolve()
    try:
        for private in private_paths(config, root, follow_links=True):
            resolved = private.resolve()
            if target == resolved or target.is_relative_to(resolved):
                raise APIError("project_file_forbidden", 403)
            try:
                stat = private.stat()
            except FileNotFoundError:
                continue
            if (stat.st_dev, stat.st_ino) == (opened.st_dev, opened.st_ino):
                raise APIError("project_file_forbidden", 403)
    except (OSError, RuntimeError):
        raise APIError("project_file_forbidden", 403) from None
