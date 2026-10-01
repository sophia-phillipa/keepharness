"""Filesystem boundary for local inference agents, independent of CLI approvals."""

import os
import shutil
import stat
import sys
from pathlib import Path

from agent_service.tools import ToolError
from control.product import PRODUCT

ISOLATION_VERSION = "local-bwrap-v3"


def reject_writable_hardlinks(root, private_inodes=None):
    """A bind cannot isolate pre-existing aliases to excluded private inodes."""

    def failed(error):
        raise ToolError("local_project_scope_invalid") from error

    for directory, _, files in os.walk(root, followlinks=False, onerror=failed):
        for name in files:
            info = (Path(directory) / name).lstat()
            if (
                stat.S_ISREG(info.st_mode)
                and info.st_nlink > 1
                and (private_inodes is None or (info.st_dev, info.st_ino) in private_inodes)
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
    for name in ("config", "migration-backup"):
        hidden = (private_runtime / name).resolve()
        if hidden.is_dir():

            def failed(error):
                raise ToolError("local_project_scope_invalid") from error

            for directory, _, files in os.walk(hidden, followlinks=False, onerror=failed):
                for filename in files:
                    info = (Path(directory) / filename).lstat()
                    if stat.S_ISREG(info.st_mode) and info.st_nlink > 1:
                        private_inodes.add((info.st_dev, info.st_ino))
    permissions = project.get("permissions", {})
    if permissions.get("read"):
        roots = [project.get("root"), *project.get("additional_roots", [])]
        for value in dict.fromkeys(value for value in roots if value):
            root = Path(value).resolve()
            # Mounting broad ancestors would expose private state or the host home.
            if not root.is_dir() or root == Path("/") or session.is_relative_to(root):
                raise ToolError("local_project_scope_invalid")
            reject_writable_hardlinks(root, None if permissions.get("write") else private_inodes)
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
            "/tail-web-search.py",
        ]
    if environment and environment.get(PRODUCT.env_prefix + "_LOCAL_KEY"):
        args += [
            "--setenv",
            PRODUCT.env_prefix + "_LOCAL_KEY",
            environment[PRODUCT.env_prefix + "_LOCAL_KEY"],
        ]
    return [*args, "--chdir", str(cwd), "--", "/codex-cli", *command[1:]]
