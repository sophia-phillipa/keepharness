"""Temporary authorized workspace and staged changes, independent of CLI syntax."""

import difflib
import json
import shutil
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
def prepare_scoped(config, project, staged, session_dir, provider, auth_name):
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
        for p in (work, home, bridge):
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
        auth_target = home / auth_name
        shutil.copyfile(auth, auth_target)
        auth_target.chmod(0o600)
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
        (home / "config.toml").write_text(
            '[mcp_servers.selected_project]\ndefault_tools_approval_mode = "approve"\ncommand = "/venv/bin/python"\nargs = ["/bridge/project_mcp.py"]\n'
        )
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
            (bridge / "effect.sock").touch(mode=0o600)
            command += ["--ro-bind", capability["socket"], "/bridge/effect.sock"]
            spec = server_spec(capability, scoped=True)
            with (home / "config.toml").open("a") as stream:
                stream.write(
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
