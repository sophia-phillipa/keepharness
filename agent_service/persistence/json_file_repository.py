"""One JSON file per entry in a private folder.

Pure file storage: atomic publication, owner-only modes and refusal of links. What an entry
may contain is decided by the module that owns the folder (Tail agents, pages, schedules),
which also supplies the error raised for storage that is not safe to use.
"""

import contextlib
import hashlib
import os
import re
import stat
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from ..errors import APIError

SUFFIX = ".json"


def owner_folder_name(owner: str) -> str:
    """A folder name for one owner that never contains the owner's own name."""
    return hashlib.sha256(owner.encode()).hexdigest()[:16]


def revision_of(text: str) -> str:
    """The revision of a stored file: the SHA-256 of its exact text, so any change alters it."""
    return hashlib.sha256(text.encode()).hexdigest()


def timestamp() -> str:
    """The current UTC time as ISO 8601 with milliseconds, for ``created_at``/``updated_at``."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class JsonFileRepository:
    def __init__(
        self,
        folder: str | Path,
        *,
        id_pattern: re.Pattern,
        max_bytes: int,
        unsafe: Callable[[], APIError],
    ) -> None:
        self.folder = Path(folder)
        self.id_pattern = id_pattern
        self.max_bytes = max_bytes
        self.unsafe = unsafe

    def _open_directory(self) -> int:
        # O_NOFOLLOW refuses a linked folder atomically, with no check-then-open gap.
        try:
            return os.open(self.folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except FileNotFoundError:
            raise
        except OSError:
            raise self.unsafe() from None

    def _create_folder(self) -> None:
        """Create the folder and every missing parent; ``mkdir`` ignores ``mode`` for parents."""
        missing = []
        for path in (self.folder, *self.folder.parents):
            if path.exists():
                break
            missing.append(path)
        for path in reversed(missing):
            path.mkdir(mode=0o700, exist_ok=True)

    def _open_folder(self, *, create: bool = False) -> int | None:
        """A descriptor for the folder, or None when it does not exist and ``create`` is off."""
        try:
            directory = self._open_directory()
        except FileNotFoundError:
            if not create:
                return None
            self._create_folder()
            directory = self._open_directory()
        if create:
            os.fchmod(directory, 0o700)
        return directory

    def ids(self) -> list[str]:
        """Sorted ids that have a ``<id>.json`` entry; links and junk count, so limits hold."""
        directory = self._open_folder()
        if directory is None:
            return []
        try:
            names = os.listdir(directory)
        finally:
            os.close(directory)
        return sorted(
            name.removesuffix(SUFFIX)
            for name in names
            if name.endswith(SUFFIX) and self.id_pattern.fullmatch(name.removesuffix(SUFFIX))
        )

    def read(self, entry_id: str) -> str | None:
        """The file text, or None when absent; a link or an unusable file is unsafe storage."""
        directory = self._open_folder()
        if directory is None:
            return None
        try:
            # O_NONBLOCK keeps a FIFO placed by hand from stalling the open.
            descriptor = os.open(
                entry_id + SUFFIX,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=directory,
            )
        except FileNotFoundError:
            return None
        except OSError:
            raise self.unsafe() from None
        finally:
            os.close(directory)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            os.close(descriptor)
            raise self.unsafe()
        with os.fdopen(descriptor, "rb") as stream:
            data = stream.read(self.max_bytes + 1)
        try:
            if len(data) > self.max_bytes:
                raise UnicodeError
            return data.decode("utf-8")
        except UnicodeError:
            raise self.unsafe() from None

    def read_all(self, limit: int) -> tuple[list[tuple[str, str]], list[str]]:
        """Up to ``limit`` files as ``(id, text)`` and the ids that could not be read."""
        entries, skipped = [], []
        for entry_id in self.ids()[:limit]:
            try:
                text = self.read(entry_id)
            except APIError:
                skipped.append(entry_id)
                continue
            if text is not None:
                entries.append((entry_id, text))
        return entries, skipped

    def create(self, entry_id: str, text: str) -> None:
        """Publish a new file; ``FileExistsError`` when the id is taken, even by a link."""
        self._write(entry_id, text, replace=False)

    def replace(self, entry_id: str, text: str) -> None:
        """Atomically swap in new content; readers see the old file or the new one."""
        self._write(entry_id, text, replace=True)

    def delete(self, entry_id: str) -> None:
        directory = self._open_folder()
        if directory is None:
            return
        try:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(entry_id + SUFFIX, dir_fd=directory)
            os.fsync(directory)
        finally:
            os.close(directory)

    def _write(self, entry_id: str, text: str, *, replace: bool) -> None:
        directory = self._open_folder(create=True)
        temporary = ".tmp-" + uuid.uuid4().hex
        name = entry_id + SUFFIX
        try:
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=directory,
            )
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            if replace:
                os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
            else:
                os.link(
                    temporary,
                    name,
                    src_dir_fd=directory,
                    dst_dir_fd=directory,
                    follow_symlinks=False,
                )
            # The rename is only durable once the directory entry itself is flushed.
            os.fsync(directory)
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temporary, dir_fd=directory)
            os.close(directory)
