"""Filesystem boundary for local inference agents, independent of CLI approvals."""

import os
import shutil
import stat
import sys
from pathlib import Path

from agent_service.tools import ToolError
from control.product import PRODUCT

ISOLATION_VERSION = "local-bwrap-v3"


class WrappedCommand(list):
    """A bwrap command line and the descriptors its process must inherit (``pass_fds``).

    Values that must stay out of the command line (visible in ``ps``) travel on a descriptor
    bwrap reads with ``--args``. The caller closes them with ``close`` once the child has started.
    """

    def __init__(self, parts, descriptors=()):
        super().__init__(parts)
        self.descriptors = tuple(descriptors)

    def close(self):
        for descriptor in self.descriptors:
            try:
                os.close(descriptor)
            except OSError:
                pass  # already closed
        self.descriptors = ()


def argument_descriptor(*arguments):
    """An in-memory file holding NUL-separated bwrap arguments, ready to be read from the start."""
    if any("\0" in argument for argument in arguments):
        raise ValueError("A sandbox argument cannot contain a NUL byte.")
    descriptor = os.memfd_create("bwrap-arguments")
    try:
        os.write(descriptor, "".join(argument + "\0" for argument in arguments).encode())
        os.lseek(descriptor, 0, os.SEEK_SET)
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def reject_writable_hardlinks(root, private_inodes=None, *, hidden=()):
    """A bind cannot isolate pre-existing aliases to excluded private inodes."""

    def failed(error):
        raise ToolError("local_project_scope_invalid") from error

    for directory, directories, files in os.walk(root, followlinks=False, onerror=failed):
        if any(Path(directory).is_relative_to(path) for path in hidden):
            directories.clear()
            continue
        for name in files:
            info = (Path(directory) / name).lstat()
            if stat.S_ISREG(info.st_mode) and (
                (private_inodes is None and info.st_nlink > 1)
                or (private_inodes is not None and (info.st_dev, info.st_ino) in private_inodes)
            ):
                raise ToolError("local_project_hardlink_denied")


def wrap(command, session, cwd, project, environment=None):
    binary = Path(command[0]).resolve()
    bwrap = shutil.which("bwrap")
    if sys.platform != "linux" or not bwrap:
        raise ToolError("local_filesystem_isolation_unavailable")
    if not binary.is_file():
        raise ToolError("local_cli_binary_unavailable")
    session = Path(session).resolve()
    cwd = Path(cwd).resolve()
    reject_writable_hardlinks(session)
    private = session / "agent-home"
    private.mkdir(exist_ok=True, mode=0o700)
    codex_home = private / ".codex"
    codex_home.mkdir(exist_ok=True, mode=0o700)
    args = [
        bwrap,
        "--unshare-all",
        "--share-net",
        "--die-with-parent",
        "--new-session",
        "--clearenv",
        "--setenv",
        "PATH",
        "/usr/bin",
        "--setenv",
        "HOME",
        str(private),
        "--setenv",
        "CODEX_HOME",
        str(codex_home),
        "--ro-bind",
        "/usr",
        "/usr",
        "--symlink",
        "usr/lib",
        "/lib",
        "--symlink",
        "usr/lib64",
        "/lib64",
        "--symlink",
        "usr/bin",
        "/bin",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--tmpfs",
        "/tmp",
        "--bind",
        str(session),
        str(session),
        "--ro-bind",
        str(binary),
        "/codex-cli",
    ]
    host = binary.with_name("codex-code-mode-host")
    if host.is_file():
        args += ["--ro-bind", str(host), "/codex-code-mode-host"]
    # Runtime credentials/backups now live inside the checkout, not in model context.
    private_runtime = (
        Path(
            os.environ.get(PRODUCT.env_prefix + "_ROOT", str(Path(__file__).resolve().parents[2]))
        ).resolve()
        / "local_ai"
    )
    private_inodes = set()
    visited = set()
    pending = [private_runtime / name for name in ("config", "migration-backup")]
    while pending:
        path = pending.pop()
        try:
            if not path.exists() and not path.is_symlink():
                continue
            info = path.stat()
            inode = (info.st_dev, info.st_ino)
            if inode in visited:
                continue
            visited.add(inode)
            if len(visited) > 100000:
                raise ToolError("local_project_scope_invalid")
            if stat.S_ISDIR(info.st_mode):
                for child in path.iterdir():
                    if len(visited) + len(pending) >= 100000:
                        raise ToolError("local_project_scope_invalid")
                    pending.append(child)
            elif stat.S_ISREG(info.st_mode):
                private_inodes.add(inode)
        except (OSError, RuntimeError) as error:
            raise ToolError("local_project_scope_invalid") from error
    reject_writable_hardlinks(session, private_inodes)
    permissions = project.get("permissions", {})
    hidden_roots = [
        (private_runtime / name).resolve()
        for name in ("config", "migration-backup")
        if (private_runtime / name).is_dir()
    ]
    if permissions.get("read"):
        roots = [project.get("root"), *project.get("additional_roots", [])]
        for value in dict.fromkeys(value for value in roots if value):
            root = Path(value).resolve()
            # Mounting broad ancestors would expose private state or the host home.
            if not root.is_dir() or root == Path("/") or session.is_relative_to(root):
                raise ToolError("local_project_scope_invalid")
            if any(root.is_relative_to(hidden) for hidden in hidden_roots):
                raise ToolError("local_project_scope_invalid")
            reject_writable_hardlinks(root, private_inodes, hidden=hidden_roots)
            if permissions.get("write"):
                reject_writable_hardlinks(root)
            args += [
                "--bind" if permissions.get("write") else "--ro-bind",
                str(root),
                str(root),
            ]
    for name in ("config", "migration-backup"):
        hidden = (private_runtime / name).resolve()
        if not hidden.is_dir():
            continue
        for value in [project.get("root"), *project.get("additional_roots", [])]:
            if not value:
                continue
            root = Path(value).resolve()
            if root.is_relative_to(hidden):
                raise ToolError("local_project_scope_invalid")
            if hidden.is_relative_to(root) and permissions.get("read"):
                args += ["--tmpfs", str(hidden)]
                break
    for value in (
        "/etc/ssl",
        "/etc/pki",
        "/etc/resolv.conf",
        "/etc/hosts",
        "/etc/nsswitch.conf",
    ):
        if Path(value).exists():
            args += ["--ro-bind", value, value]
    if permissions.get("internet") and permissions.get("shell"):
        args += [
            "--ro-bind",
            str(Path(__file__).with_name("web_search.py").resolve()),
            "/keepharness-web-search.py",
        ]
    descriptors = []
    key_name = PRODUCT.env_prefix + "_LOCAL_KEY"
    if environment and environment.get(key_name):
        descriptors = [argument_descriptor("--setenv", key_name, environment[key_name])]
        args += ["--args", str(descriptors[0])]
    parts = [*args, "--chdir", str(cwd), "--", "/codex-cli", *command[1:]]
    return WrappedCommand(parts, descriptors)
