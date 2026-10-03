"""One JSON file per Tail-owned agent in a private folder.

Pure file storage: atomic publication, owner-only modes and refusal of links. What an agent
may contain is decided by ``agent_service.tail_agents``, not here.
"""

import contextlib
import os
import re
import stat
import uuid
from pathlib import Path

from ..errors import APIError

AGENT_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,47}$")
MAX_FILE_BYTES = 65536
SUFFIX = ".json"


def unsafe_storage() -> APIError:
    return APIError("tail_agent_storage_unsafe", 500)


class TailAgentRepository:
    def __init__(self, folder: str | Path) -> None:
        self.folder = Path(folder)

    def _open_directory(self) -> int:
        # O_NOFOLLOW refuses a linked folder atomically, with no check-then-open gap.
        try:
            return os.open(self.folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except FileNotFoundError:
            raise
        except OSError:
            raise unsafe_storage() from None

    def _open_folder(self, *, create: bool = False) -> int | None:
        """A descriptor for the folder, or None when it does not exist and ``create`` is off."""
        try:
            directory = self._open_directory()
        except FileNotFoundError:
            if not create:
                return None
            self.folder.mkdir(mode=0o700, parents=True, exist_ok=True)
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
            if name.endswith(SUFFIX) and AGENT_ID.fullmatch(name.removesuffix(SUFFIX))
        )

    def read(self, agent_id: str) -> str | None:
        """The file text, or None when absent; a link or an unusable file is unsafe storage."""
        directory = self._open_folder()
        if directory is None:
            return None
        try:
            # O_NONBLOCK keeps a FIFO placed by hand from stalling the open.
            descriptor = os.open(
                agent_id + SUFFIX,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=directory,
            )
        except FileNotFoundError:
            return None
        except OSError:
            raise unsafe_storage() from None
        finally:
            os.close(directory)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            os.close(descriptor)
            raise unsafe_storage()
        with os.fdopen(descriptor, "rb") as stream:
            data = stream.read(MAX_FILE_BYTES + 1)
        try:
            if len(data) > MAX_FILE_BYTES:
                raise UnicodeError
            return data.decode("utf-8")
        except UnicodeError:
            raise unsafe_storage() from None

    def read_all(self, limit: int) -> tuple[list[tuple[str, str]], list[str]]:
        """Up to ``limit`` files as ``(id, text)`` and the ids that could not be read."""
        entries, skipped = [], []
        for agent_id in self.ids()[:limit]:
            try:
                text = self.read(agent_id)
            except APIError:
                skipped.append(agent_id)
                continue
            if text is not None:
                entries.append((agent_id, text))
        return entries, skipped

    def create(self, agent_id: str, text: str) -> None:
        """Publish a new file; ``FileExistsError`` when the id is taken, even by a link."""
        self._write(agent_id, text, replace=False)

    def replace(self, agent_id: str, text: str) -> None:
        """Atomically swap in new content; readers see the old file or the new one."""
        self._write(agent_id, text, replace=True)

    def delete(self, agent_id: str) -> None:
        directory = self._open_folder()
        if directory is None:
            return
        try:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(agent_id + SUFFIX, dir_fd=directory)
            os.fsync(directory)
        finally:
            os.close(directory)

    def _write(self, agent_id: str, text: str, *, replace: bool) -> None:
        directory = self._open_folder(create=True)
        temporary = ".tail-agent-" + uuid.uuid4().hex
        name = agent_id + SUFFIX
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
