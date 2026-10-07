"""Provider state seam: types, errors, the adapter protocol and the atomic JSON writer.

KeepHarness reads and writes the provider CLIs' real state (decisions D-038, D-039). This module is
the shared contract only; the Codex, Claude and DeepSeek adapters implement it elsewhere.

``write_json_atomic`` is the one direct-edit path for Claude's ``settings.json`` and
``~/.claude.json``. The latter holds the sign-in session, so nothing here logs, copies (beyond the
owner-only backup folder) or puts file content in an error message.
"""

import errno
import hashlib
import json
import logging
import os
import re
import stat
import tempfile
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, NamedTuple, Protocol, TypedDict, runtime_checkable

from agent_service.errors import HarnessError

logger = logging.getLogger(__name__)

Kind = Literal["plugin", "app", "mcp", "skill", "hook", "instructions"]
Scope = Literal["user", "project", "local", "managed", "profile"]

# Where the pre-write copies of ~/.claude.json live, relative to the state folder. The copies hold
# the sign-in session, so exports (control/backup.py) and the rollback archive of the state merge
# (control/state_merge.py) leave this folder out. No diagnostics bundle exists yet; when one does,
# it must leave it out too.
CLAUDE_JSON_BACKUP_PARTS = ("backups", "claude-json")
KEPT_BACKUPS = 3
BACKUP_FOLDER_MODE = 0o700
BACKUP_FILE_MODE = 0o600
_BACKUP_NAME = re.compile(r"\d{20}\.json")


def claude_json_backup_dir(state: Path) -> Path:
    """The folder that keeps the newest pre-write copies of ``~/.claude.json``."""
    return Path(state).joinpath(*CLAUDE_JSON_BACKUP_PARTS)


# --- types -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class StateItem:
    id: str  # "<kind>:<provider-native id>", e.g. "plugin:github@openai-curated", "mcp:linear"
    kind: Kind
    name: str
    scope: Scope  # the layer that decides the effective value
    enabled: bool  # effective value after layering
    source: str  # file or command that decides it, for display ("~/.codex/config.toml")
    writable: bool
    reason: str = ""  # why not writable, shown under the switch
    affects: tuple[str, ...] = ()  # other provider ids that read the same source


@dataclass(frozen=True)
class StateSnapshot:
    provider: str
    engine: str  # engine id, separate from the provider id
    project_root: str | None
    items: tuple[StateItem, ...]
    fingerprint: str  # sha256 over the bytes of the files read; changes when any of them changes
    cli_version: str
    warnings: tuple[str, ...] = ()


class RunSetup(NamedTuple):
    environment: dict[str, str]
    extra_args: list[str]
    settings_overrides: dict


class CredentialRule(TypedDict):
    home: str
    forbidden_files: tuple[str, ...]
    keyring: str
    env_only: tuple[str, ...]


class LoginStatus(TypedDict):
    signed_in: bool
    account_label: str | None


class SecretStr:
    """A secret that does not show up in ``repr``, ``str`` or logs; same shape as pydantic's."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def get_secret_value(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "SecretStr('**********')"

    __str__ = __repr__


# --- errors ----------------------------------------------------------------------------------


class _ProviderStateError(HarnessError):
    """``str()`` is the message; ``code`` and ``status`` are the class's contract values."""

    error_code = ""
    http_status = 422

    def __init__(self, message: str = "") -> None:
        super().__init__(self.error_code, self.http_status)


class ProviderStateConflictError(_ProviderStateError):
    error_code, http_status = "provider_state_conflict", 409


class ProviderStateUnsupportedError(_ProviderStateError):
    error_code = "provider_state_write_unsupported"


class ProviderStateSchemaError(_ProviderStateError):
    error_code = "provider_state_unreadable"


class ProviderStateVersionError(_ProviderStateError):
    error_code = "provider_state_version_untested"


class ProviderVersionUnsupportedError(_ProviderStateError):
    error_code = "provider_version_unsupported"  # raised at run start


class ProviderStateValidationError(_ProviderStateError):
    error_code = "provider_state_validation_failed"

    def __init__(self, message: str = "", *, errors: Sequence[str] = ()) -> None:
        super().__init__(message)
        self.errors = tuple(errors)


class ProviderCommandError(_ProviderStateError):
    """A CLI command failed; the message is already redacted and never holds its environment."""

    error_code, http_status = "provider_command_failed", 502

    def __init__(self, message: str = "", *, exit_code: int | None = None) -> None:
        super().__init__(message)
        self.exit_code = exit_code


# --- adapter protocol ------------------------------------------------------------------------


@runtime_checkable
class ProviderStateAdapter(Protocol):
    """One provider's view of its CLI state; the guarantees live in the design's Contracts."""

    def read_state(self, project_root: Path | None) -> StateSnapshot: ...

    def set_enabled(
        self,
        item_id: str,
        scope: Scope,
        enabled: bool,
        expected_fingerprint: str,
        *,
        project_root: Path | None = None,
    ) -> StateSnapshot: ...

    def watch_paths(self, project_root: Path | None) -> tuple[Path, ...]: ...

    def is_project_trusted(self, project_root: Path) -> bool: ...

    def trust_project(self, project_root: Path) -> None: ...

    def approved_project_servers(self, project_root: Path) -> frozenset[str]: ...

    def run_environment(
        self, project_root: Path, trusted: bool, permission_flags: Sequence[str]
    ) -> RunSetup: ...

    def credential_isolation(self) -> CredentialRule | None: ...

    def login_command(self, headless: bool) -> list[str]: ...

    def login_status(self) -> LoginStatus: ...

    def set_api_key(self, secret: SecretStr) -> None: ...


# --- fingerprint -----------------------------------------------------------------------------


def _stat_marker(path: Path) -> str:
    try:
        info = os.stat(path)
        if not stat.S_ISDIR(info.st_mode):
            return f"{info.st_mtime_ns}:{info.st_size}:{info.st_ino}"
        newest, count = 0, 0
        with os.scandir(path) as entries:
            for entry in entries:
                try:
                    newest = max(newest, entry.stat().st_mtime_ns)
                except OSError:
                    continue  # removed while we looked, a symlink loop or no permission
                count += 1
        return f"dir:{info.st_mtime_ns}:{newest}:{count}"
    except OSError:  # missing, not a directory, a symlink loop or no permission
        return "missing"


def fingerprint(paths: Iterable[Path]) -> str:
    """The cheap "did anything move" check: one sha256 over a stat tuple per path.

    A file contributes ``(st_mtime_ns, st_size, st_ino)``, a directory its own mtime, the newest
    mtime among its direct children and their number (so removing an older child shows too), a
    missing path a marker. Symlinks are followed, as the CLIs do; a child that cannot be stat'ed
    (dangling, a loop, no permission) is not counted, and such a path counts as missing. This is
    not the write-side conflict hash.
    """
    digest = hashlib.sha256()
    for path in paths:
        digest.update(f"{path}\0{_stat_marker(Path(path))}\n".encode())
    return digest.hexdigest()


# --- write_json_atomic -----------------------------------------------------------------------

# ``expected_sha256`` for create mode: "the file must not exist". Not a hex digest, so no file can match it.
MISSING_FILE = "<create-new-file>"
NEW_FILE_MODE = 0o600
# errno values of a filesystem that cannot hard-link
_NO_HARD_LINKS = frozenset({errno.EPERM, errno.EOPNOTSUPP, errno.ENOSYS})
_NEW_FILE_TEXT = (
    "{\n  }\n"  # only its layout counts: a new file gets a 2-space indent and a final newline
)

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(real: Path) -> threading.Lock:
    """One lock per real path: a symlink and its target share it. Cooperates with ourselves only."""
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(str(real), threading.Lock())


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _fsync_directory(folder: Path) -> None:
    descriptor = os.open(folder, os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _current_sha256(real: Path) -> str | None:
    try:
        return _sha256(real.read_bytes())
    except FileNotFoundError:
        return None


def _swap(real: Path, data: bytes, expected_sha256: str, info: os.stat_result | None) -> None:
    """Replace ``real`` by ``data`` if it still holds the bytes with ``expected_sha256``.

    The temp file sits beside the real target (same filesystem) with its mode and, where
    permitted, its owner. The hash is checked again right before ``os.replace``. With no ``info``
    (create mode) the file is new: the temp file is hard-linked to the target, which fails when
    something appeared there meanwhile, so a file that is not ours is never overwritten.
    """
    prefix = f".{real.name}."
    descriptor, temporary = tempfile.mkstemp(dir=real.parent, prefix=prefix, suffix=".tmp")
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(
                stream.fileno(), NEW_FILE_MODE if info is None else stat.S_IMODE(info.st_mode)
            )
            if info is not None:
                try:
                    os.fchown(stream.fileno(), info.st_uid, info.st_gid)
                except PermissionError:
                    pass  # not our file to hand back to its owner; the mode still holds
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if info is None:
            try:
                os.link(temporary, real)
            except FileExistsError:
                raise ProviderStateConflictError(
                    f"{real.name} appeared while it was being written"
                ) from None
            except OSError as error:
                if error.errno not in _NO_HARD_LINKS:
                    raise
                raise ProviderStateUnsupportedError(
                    "this filesystem cannot create a file exclusively"
                ) from None
        else:
            if _current_sha256(real) != expected_sha256:
                raise ProviderStateConflictError(f"{real.name} changed while it was being written")
            os.replace(temporary, real)
    finally:
        Path(temporary).unlink(missing_ok=True)
    _fsync_directory(real.parent)


def _backup(folder: Path, data: bytes) -> None:
    """Copy ``data`` into ``folder`` (0700, files 0600), durably, and keep the newest three."""
    folder.mkdir(mode=BACKUP_FOLDER_MODE, parents=True, exist_ok=True)
    folder.chmod(BACKUP_FOLDER_MODE)
    ours = sorted(p for p in folder.iterdir() if _BACKUP_NAME.fullmatch(p.name))
    # Names sort in creation order even when the clock went backwards: the new copy is always last.
    newest = int(ours[-1].stem) + 1 if ours else 0
    target = folder / f"{max(time.time_ns(), newest):020d}.json"
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, BACKUP_FILE_MODE)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        _fsync_directory(folder)
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    for old in [*ours, target][:-KEPT_BACKUPS]:
        old.unlink()


def _parse(real: Path, data: bytes) -> tuple[dict, str]:
    try:
        text = data.decode("utf-8")
        document = json.loads(text)
    except ValueError:  # JSON and UTF-8 errors; their text could quote the file, so it is dropped
        raise ProviderStateSchemaError(f"{real.name} is not valid JSON") from None
    if not isinstance(document, dict):
        raise ProviderStateSchemaError(f"{real.name} is not a JSON object")
    return document, text


def _render(document: dict, original: str) -> bytes:
    """Serialize like ``original``: its indent, its separators and its final newline."""
    indented = re.search(r"\n([ \t]+)\S", original)
    options = {"ensure_ascii": False}
    if indented:
        options["indent"] = indented.group(1)
    elif '": ' not in original:
        options["separators"] = (",", ":")
    text = json.dumps(document, **options) + ("\n" if original.endswith("\n") else "")
    return text.encode("utf-8", errors="backslashreplace")  # a lone surrogate becomes \uXXXX


def _refuse_new_errors(real: Path, before: bytes, written: bytes, validate: Callable) -> None:
    """Raise when ``written`` has a validation error that ``before`` did not have."""
    known = set(validate(before))
    introduced = [error for error in validate(written) if error not in known]
    if introduced:
        raise ProviderStateValidationError(
            f"{real.name} would fail validation, so nothing was written", errors=introduced
        )


def write_json_atomic(
    path: Path,
    change: Callable[[dict], dict],
    expected_sha256: str,
    *,
    backup_dir: Path | None = None,
    validate: Callable[[bytes], Sequence[str]] | None = None,
) -> str:
    """Apply ``change`` to the JSON object in ``path`` atomically; the sha256 of the new bytes.

    The per-path lock is not reentrant: ``change`` and ``validate`` must not write files.

    ``path`` is resolved first, so a symlink stays a symlink and its target is replaced. Raises
    ``ProviderStateConflictError`` when the file is gone or its bytes are not the ones with
    ``expected_sha256`` (now, or right before the replace), ``ProviderStateSchemaError`` when it
    is not a JSON object and ``ProviderStateValidationError`` when ``validate`` reports an error
    on the new bytes that the previous bytes did not have; nothing is written then.

    Create mode: with ``expected_sha256=MISSING_FILE`` the file must not exist. ``change`` starts
    from ``{}``, ``validate`` still runs (the previous bytes being ``{}``), nothing is backed up, and
    the file is placed with mode 0600 without ever overwriting one that appeared meanwhile (that is
    a ``ProviderStateConflictError``). A missing parent folder is ``ProviderStateUnsupportedError``;
    folders are never created. Without ``MISSING_FILE`` the function never creates the file.

    This is a plain blocking function, serialized by one thread lock per real path. A real
    ``~/.claude.json`` is often hundreds of KB, so async callers run it with ``asyncio.to_thread``.
    """
    real = Path(os.path.realpath(path))
    creating = expected_sha256 == MISSING_FILE
    with _lock_for(real):
        if creating:
            if not real.parent.is_dir():
                raise ProviderStateUnsupportedError(f"the folder of {real.name} does not exist")
            if os.path.lexists(real):
                raise ProviderStateConflictError(f"{real.name} appeared since it was read")
            before, info, document, original = b"{}", None, {}, _NEW_FILE_TEXT
        else:
            try:
                before, info = real.read_bytes(), real.stat()
            except FileNotFoundError:
                raise ProviderStateConflictError(f"{real.name} is gone since it was read") from None
            if _sha256(before) != expected_sha256:
                raise ProviderStateConflictError(f"{real.name} changed since it was read")
            document, original = _parse(real, before)
        updated = change(document)
        if not isinstance(updated, dict):
            raise ProviderStateSchemaError("the change did not return a JSON object")
        written = _render(updated, original)
        if validate:
            _refuse_new_errors(real, before, written, validate)
        if backup_dir is not None and not creating:
            _backup(Path(backup_dir), before)  # no copy, no write
        _swap(real, written, expected_sha256, info)
        logger.debug("provider state written: %s", real.name)
        return _sha256(written)
