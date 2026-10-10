"""Pinned private-directory access for provider credentials."""

import io
import os
import stat
from contextlib import contextmanager
from pathlib import Path

from agent_service.tools import ToolError


def validate_private_file(metadata):
    """Require a regular file whose inode belongs only to its private path."""
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise ToolError("unsafe_scoped_home")


def trusted_state_root(state):
    """Resolve the owner-configured state root (e.g. /home -> /var/home) before pinning it.

    Only this root is resolved; components below it stay under O_NOFOLLOW.
    """
    return Path(state).resolve()


@contextmanager
def scoped_home_directory(home, *, create=True):
    """Pin every directory component; never follow worker-planted symlinks."""
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in Path(home).absolute().parts[1:]:
            if part == "..":
                raise ToolError("unsafe_scoped_home")
            if create:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    except OSError as exc:
        if not create:
            raise
        raise ToolError("unsafe_scoped_home") from exc
    finally:
        os.close(fd)


@contextmanager
def scoped_home_open_read(home, name, *, create=False):
    """Open a regular, unlinked binary source without creating preview directories."""
    if Path(name).name != name or name in ("", ".", ".."):
        raise ToolError("unsafe_scoped_home")
    with scoped_home_directory(home, create=create) as directory:
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        except FileNotFoundError:
            yield None
            return
        with os.fdopen(fd, "rb") as stream:
            validate_private_file(os.fstat(stream.fileno()))
            yield stream


def scoped_home_read(home, name, *, create=True):
    with scoped_home_open_read(home, name, create=create) as stream:
        if stream is None:
            return None
        with io.TextIOWrapper(stream, encoding="utf-8") as text:
            return text.read()
