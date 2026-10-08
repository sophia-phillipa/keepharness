"""Pinned private-directory access for provider credentials."""

import io
import os
import stat
from contextlib import contextmanager
from pathlib import Path

from agent_service.tools import ToolError


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
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise ToolError("unsafe_scoped_home")
            yield stream


def scoped_home_read(home, name):
    with scoped_home_open_read(home, name, create=True) as stream:
        if stream is None:
            return None
        with io.TextIOWrapper(stream) as text:
            return text.read()
