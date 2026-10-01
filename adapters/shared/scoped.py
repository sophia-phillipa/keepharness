"""Temporary authorized workspace and staged changes, independent of CLI syntax."""

import difflib
import json
import os
import secrets
import shutil
import stat
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import agent_service
from agent_service.tools import ToolError, safe_file


@dataclass
class ScopedWorkspace:
    command: list[str]
    home: Path
    work: Path
    bridge: Path
    roots: dict


@contextmanager
def scoped_home_directory(home):
    """Pin every directory component; never follow worker-planted symlinks."""
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in Path(home).absolute().parts[1:]:
            if part == "..":
                raise ToolError("unsafe_scoped_home")
            try:
                os.mkdir(part, mode=0o700, dir_fd=fd)
            except FileExistsError:
                pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    except OSError as exc:
        raise ToolError("unsafe_scoped_home") from exc
    finally:
        os.close(fd)


def scoped_home_read(home, name):
    with scoped_home_directory(home) as directory:
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        except FileNotFoundError:
            return None
        with os.fdopen(fd, "r") as stream:
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise ToolError("unsafe_scoped_home")
            return stream.read()


def scoped_home_write(home, name, content):
    """Replace a regular entry without truncating links or reopening a raced path."""
    with scoped_home_directory(home) as directory:
        try:
            existing = os.stat(name, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            if not stat.S_ISREG(existing.st_mode) or existing.st_nlink != 1:
                raise ToolError("unsafe_scoped_home")
        temporary = ".harness-" + secrets.token_hex(16)
        fd = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory,
        )
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(content if isinstance(content, bytes) else content.encode())
            os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
        finally:
            try:
                os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError:
                pass


@contextmanager
def prepare_scoped(config, project, staged, session_dir, provider, auth_name):
    if config.get("_validate_private_files"):
        config["_validate_private_files"]()
    binary = Path(config["binary"]).resolve()
    auth = Path(config["auth_file"])
    if not binary.is_file() or not auth.is_file():
        raise ToolError(provider + "_login_or_binary_unavailable")
    with tempfile.TemporaryDirectory(prefix="local-agent-codex-") as tmp:
        base = Path(tmp)
        work = base / "work"
        home = base / "auth"
        bridge = base / "bridge"
        if session_dir:
            home = Path(session_dir)
        for p in (work, bridge):
            p.mkdir(parents=True, exist_ok=True, mode=0o700)
        roots = {}
        if project and project.get("root"):
            roots = {
                "project": Path(project["root"]).resolve(),
                **{
                    f"extra-{i + 1}": Path(p).resolve()
                    for i, p in enumerate(project.get("additional_roots", []))
                },
            }
        # Restore only text proposals in authorized project roots, never runtime state.
        restored = {}
        for name, content in (staged or {}).items():
            parts = Path(name).parts
            if (
                Path(name).is_absolute()
                or len(parts) < 2
                or parts[0] not in roots
                or any(p.startswith(".") for p in parts)
                or not isinstance(content, str)
                or len(content.encode()) > 100000
            ):
                raise ToolError("invalid_staged_file")
            restored[name] = content
        if sum(len(v.encode()) for v in restored.values()) > 2 * 1024 * 1024:
            raise ToolError("staged_context_limit")
        for name, content in restored.items():
            dest = work / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content)
        scoped_home_write(home, auth_name, auth.read_bytes())
        for name in ("project_mcp.py", "tools.py", "errors.py"):
            shutil.copyfile(Path(agent_service.__file__).with_name(name), bridge / name)
        (bridge / "project.json").write_text(
            json.dumps(
                {
                    "roots": list(roots),
                    "test_commands": (project or {}).get("test_commands", {}),
                    "test_paths": (project or {}).get("test_paths", []),
                    "apply_changes": bool((project or {}).get("apply_changes")),
                    "permissions": (project or {}).get("permissions", {}),
                }
            )
        )
        runtime = Path(config["python"]).parent.parent
        home_config = '[mcp_servers.selected_project]\ndefault_tools_approval_mode = "approve"\ncommand = "/venv/bin/python"\nargs = ["/bridge/project_mcp.py"]\n'
        command = [
            "bwrap",
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
            "/tmp",
            "--setenv",
            "CODEX_HOME",
            "/codex",
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
            str(home),
            "/codex",
            "--bind",
            str(work),
            "/work",
            "--ro-bind",
            str(binary),
            "/codex-cli",
            "--ro-bind",
            str(runtime),
            "/venv",
            "--ro-bind",
            str(bridge),
            "/bridge",
            "--chdir",
            "/work",
        ]
        if config.get("_effect_capability"):
            from agent_service.effect_transport import server_spec

            capability = config["_effect_capability"]
            shutil.copyfile(
                Path(agent_service.__file__).with_name("effect_mcp.py"), bridge / "effect_mcp.py"
            )
            with open(
                bridge / "effect.json", "x", opener=lambda path, flags: os.open(path, flags, 0o600)
            ) as stream:
                json.dump({"socket": "/bridge/effect.sock", "token": capability["token"]}, stream)
            (bridge / "effect.sock").touch(mode=0o600)
            command += ["--ro-bind", capability["socket"], "/bridge/effect.sock"]
            spec = server_spec(capability, scoped=True)
            home_config += (
                "\n[mcp_servers.harness_effects]\ncommand = "
                + json.dumps(spec["command"])
                + "\nargs = "
                + json.dumps(spec["args"])
                + "\n"
            )
        code_host = binary.with_name("codex-code-mode-host")
        if code_host.exists():
            command += ["--ro-bind", str(code_host), "/codex-code-mode-host"]
        for label, root in roots.items():
            command += ["--ro-bind", str(root), "/sources/" + label]
        for source in (
            "/etc/ssl",
            "/etc/pki",
            "/etc/resolv.conf",
            "/etc/hosts",
            "/etc/nsswitch.conf",
        ):
            if Path(source).exists():
                command += ["--ro-bind", source, source]
        scoped_home_write(home, "config.toml", home_config)
        capability = config.get("_effect_capability")
        if capability and capability.get("_validate_scoped"):
            capability["enforcement"] = capability["_validate_scoped"](command, auth)
            if capability.get("_publication_policy"):
                capability["_publication_policy"]()
        yield ScopedWorkspace(command, home, work, bridge, roots)


def collect_changes(workspace):
    work, roots = workspace.work, workspace.roots
    patches = []
    staged_files = {}
    for path in work.rglob("*"):
        if not path.is_file() or path.is_symlink() or path.stat().st_size > 2 * 1024 * 1024:
            continue
        name = str(path.relative_to(work))
        parts = Path(name).parts
        before = b""
        if len(parts) > 1 and parts[0] in roots:
            try:
                before = safe_file(roots[parts[0]], str(Path(*parts[1:]))).read_bytes()
            except ToolError:
                pass
        after = path.read_bytes()
        if before == after:
            continue
        if len(parts) > 1 and parts[0] in roots and not any(p.startswith(".") for p in parts):
            try:
                staged_files[name] = after.decode()
            except UnicodeError:
                pass
        try:
            patches.extend(
                difflib.unified_diff(
                    before.decode().splitlines(True),
                    after.decode().splitlines(True),
                    "a/" + name,
                    "b/" + name,
                )
            )
        except UnicodeError:
            patches.append("Binary changed: " + name + "\n")
    if sum(len(v.encode()) for v in staged_files.values()) > 2 * 1024 * 1024:
        raise ToolError("staged_context_limit")
    return {
        "staged_files": staged_files,
        "patch": "".join(patches),
        "host_changed": False,
        "project_mode": "scoped_read_and_staged_changes" if roots else "projectless",
    }
