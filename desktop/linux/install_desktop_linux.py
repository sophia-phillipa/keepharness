#!/usr/bin/env python3
"""Per-user desktop lifecycle. Validate a complete deletion plan before applying it."""

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:[+-][0-9A-Za-z.]+)?")
STAGE = re.compile(r"\.keepharness-[A-Za-z0-9]{6}")
UID = os.getuid()


def refuse(message):
    raise ValueError(message)


def exists(path):
    return os.path.lexists(path)


def checked(path, root=None):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or info.st_uid != UID:
        refuse(f"Unsafe symlink or owner: {path}")
    if root is not None and path.resolve(strict=True) != root / path.name:
        refuse(f"Outside expected parent: {path}")
    return info


def tree(path):
    """Do not follow links or cross devices, including bind-mounted descendants."""
    info = checked(path, path.parent)
    # mountinfo also identifies same-device bind mounts that ismount cannot detect.
    mounts = {
        Path(re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), line.split()[4]))
        for line in Path("/proc/self/mountinfo").read_text().splitlines()
    }
    if path in mounts:
        refuse(f"Mounted deletion root: {path}")

    def walk_error(error):
        raise error

    if path.is_dir():
        for parent, dirs, files in os.walk(path, followlinks=False, onerror=walk_error):
            for name in dirs + files:
                child = Path(parent) / name
                meta = child.lstat()
                # Internal Electron Singleton links may be removed, never followed.
                if meta.st_uid != UID or meta.st_dev != info.st_dev or child in mounts:
                    refuse(f"Foreign owner or filesystem: {child}")
                if not (
                    stat.S_ISREG(meta.st_mode)
                    or stat.S_ISDIR(meta.st_mode)
                    or stat.S_ISLNK(meta.st_mode)
                ):
                    refuse(f"Special file: {child}")


def version_name(name):
    return (
        name.startswith("keepharness-")
        and not name.endswith(".installing")
        and ".failed-copy-" not in name
        and VERSION.fullmatch(name[12:])
    )


def marker(path):
    if not version_name(path.name):
        return False
    for name in ("VERSION", "build-manifest.json", "keepharness-bin"):
        p = path / name
        if p.is_symlink() or not p.is_file():
            return False
    version = path.name.removeprefix("keepharness-")
    try:
        manifest = json.loads((path / "build-manifest.json").read_text())
        return (
            (path / "VERSION").read_text().strip() == version
            and manifest.get("version") == version
            and manifest.get("product", "keepharness") == "keepharness"
        )
    except (ValueError, OSError, AttributeError):
        return False


def running_versions(opt):
    pattern = "^" + re.escape(str(opt)) + r"/keepharness-[^/]+/keepharness-bin"
    result = subprocess.run(
        ["pgrep", "-u", str(UID), "-f", pattern], capture_output=True, text=True
    )
    if result.returncode not in (0, 1):
        refuse("Cannot check running desktop processes")
    running = set()
    for pid in result.stdout.split():
        try:
            command = Path("/proc", pid, "cmdline").read_bytes().split(b"\0")[0].decode()
            running.add(Path(command).parent)
        except FileNotFoundError:
            pass
    return running


def live_singleton(config):
    lock = config / "KeepHarness/SingletonLock"
    if not lock.is_symlink():
        return False
    host, sep, pid = os.readlink(lock).rpartition("-")
    if sep and host == socket.gethostname() and pid.isdigit():
        try:
            os.kill(int(pid), 0)
            return True
        except ProcessLookupError:
            pass
        except PermissionError:
            return True
    return False


def remove(paths):
    # Call only under the lifecycle lock; validation is repeated just before deletion.
    for path in paths:
        tree(path)
    for path in paths:
        tree(path)
        subprocess.run(["rm", "-rf", "--one-file-system", "--", str(path)], check=True)


def atomic_link(container, name, target):
    link = container / name
    if exists(link) and not link.is_symlink():
        refuse(f"Not a link: {link}")
    with tempfile.TemporaryDirectory(prefix=".link-", dir=container) as stage:
        temp = Path(stage) / name
        temp.symlink_to(target)
        os.replace(temp, link)


def target_of(container, name, opt):
    link = container / name
    if not link.is_symlink():
        refuse(f"Missing link: {link}")
    target = link.resolve(strict=True)
    if target.parent != opt or not marker(target):
        refuse(f"Not a marked install: {link}")
    checked(target, opt)
    return target


def exact_entry(path, lines, browser=False):
    if not exists(path):
        return False
    checked(path, path.parent.resolve(strict=True))
    if not path.is_file():
        refuse(f"Not a regular desktop entry: {path}")
    content = path.read_text().splitlines()
    execs = [line for line in content if line.startswith("Exec=")]
    return (
        len(execs) == 1
        and execs[0] in lines
        and (not browser or "Icon=utilities-terminal" in content)
    )


def verify_package(source):
    checked(source, source.parent.resolve(strict=True))
    members = {}
    for parent, dirs, files in os.walk(source):
        for name in dirs + files:
            p = Path(parent) / name
            checked(p)
            if not (p.is_dir() or p.is_file()):
                refuse(f"Invalid package member: {p}")
        for name in files:
            p = Path(parent) / name
            if p.relative_to(source).as_posix() != "SHA256SUMS":
                members[p.relative_to(source).as_posix()] = hashlib.sha256(
                    p.read_bytes()
                ).hexdigest()
    sums = {}
    for line in (source / "SHA256SUMS").read_text().splitlines():
        digest, sep, name = line.partition("  ")
        if not sep or not re.fullmatch("[0-9a-f]{64}", digest) or name in sums:
            refuse("Malformed SHA256SUMS")
        sums[name] = digest
    if sums != members:
        refuse("Package SHA256SUMS mismatch")
    version = (source / "VERSION").read_text().strip()
    if not version_name("keepharness-" + version):
        refuse("Invalid package version")
    manifest = json.loads((source / "build-manifest.json").read_text())
    if (
        manifest.get("version") != version
        or manifest.get("product") != "keepharness"
        or manifest.get("dirty") is not False
        or not isinstance(manifest.get("commit"), str)
        or not re.fullmatch("[0-9a-fA-F]{40}", manifest["commit"])
    ):
        refuse("Invalid package provenance")
    return version


def prune(opt, container):
    keep = {target_of(container, "current", opt)} | running_versions(opt)
    if exists(container / "previous"):
        keep.add(target_of(container, "previous", opt))
    paths = []
    for p in opt.iterdir():
        if version_name(p.name):
            checked(p, opt)
            if p not in keep and marker(p):
                paths.append(p)
    for p in paths:
        print(f"Prune: {p}")
    remove(paths)


def uninstall(home, spellings, opt, config, apps, container, args):
    if running_versions(opt) or live_singleton(config):
        refuse("Desktop is running")
    desktop = config / "KeepHarness"
    python = config / "keepharness"
    if desktop.exists() and python.exists() and os.path.samefile(desktop, python):
        refuse("Config folders share an inode; desktop data left in place")
    paths = []
    for p in opt.iterdir():
        if version_name(p.name):
            checked(p, opt)
            if marker(p):
                paths.append(p)
        elif STAGE.fullmatch(p.name):
            paths.append(p)
    for p in (desktop, python / "electron"):
        if exists(p):
            if p.parent == python:
                checked(python, config)
            paths.append(p)
    entry = apps / "keepharness.desktop"
    lines = {f'Exec="{h}/.local/opt/keepharness/current/keepharness"' for h in spellings}
    for h in spellings:
        if exists(entry) and not entry.is_symlink():
            for line in entry.read_text().splitlines():
                prefix = f'Exec="{h}/.local/opt/keepharness-'
                if line.startswith(prefix) and line.endswith('/keepharness"'):
                    if VERSION.fullmatch(line[len(prefix) : -len('/keepharness"')]):
                        lines.add(line)
    if exact_entry(entry, lines):
        paths.append(entry)
    paths += [p for p in apps.iterdir() if STAGE.fullmatch(p.name)]
    links = []
    if exists(container):
        checked(container, opt)
        for name in ("current", "previous"):
            link = container / name
            if exists(link):
                if not link.is_symlink() or link.lstat().st_uid != UID:
                    refuse(f"Unsafe link: {link}")
                links.append(link)
    for p in paths:
        tree(p)
    for p in paths + links:
        print(f"Remove: {p}")
    if args.dry_run:
        return
    if not args.yes and (
        not sys.stdin.isatty() or input("Delete these desktop files? [y/N] ").lower() != "y"
    ):
        refuse("Uninstall requires --yes or interactive confirmation")
    if running_versions(opt) or live_singleton(config):
        refuse("Desktop is running")
    remove(paths)
    for link in links:
        subprocess.run(["rm", "-f", "--", str(link)], check=True)
    if container.exists() and not any(container.iterdir()):
        container.rmdir()
    print("The KeepHarness server and keepharness-open launcher are unchanged.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--uninstall", action="store_true")
    modes.add_argument("--rollback", action="store_true")
    modes.add_argument("--verify", action="store_true", help="print the package version; read-only")
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.verify:
        print(verify_package(Path(args.source).resolve(strict=True)))
        return
    raw = os.environ.get("HOME", "")
    if not raw or not Path(raw).is_absolute() or Path(raw) == Path("/"):
        refuse("HOME must be an existing absolute user directory, not /")
    home = Path(raw).resolve(strict=True)
    if home == Path("/") or not home.is_dir() or home.stat().st_uid != UID:
        refuse("Invalid HOME")
    # HOME itself may be an alias; all descendants must be ordinary owned directories.
    for suffix in (".local", ".local/opt", ".local/share", ".local/share/applications", ".config"):
        p = home / suffix
        if not exists(p):
            p.mkdir()
        checked(p, p.parent.resolve(strict=True))
        if not p.is_dir():
            refuse(f"Not a directory: {p}")
    opt, config, apps = (home / ".local/opt", home / ".config", home / ".local/share/applications")
    lock = opt / ".keepharness.lock"
    fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        meta = os.fstat(stream.fileno())
        if not stat.S_ISREG(meta.st_mode) or meta.st_uid != UID:
            refuse("Unsafe lifecycle lock")
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            refuse("Another desktop lifecycle operation holds the lock")
        container = opt / "keepharness"
        if exists(container):
            checked(container, opt)
        if args.uninstall:
            uninstall(home, {str(home), raw.rstrip("/")}, opt, config, apps, container, args)
            return
        if args.rollback:
            current = target_of(container, "current", opt)
            previous = target_of(container, "previous", opt)
            if not args.dry_run:
                atomic_link(container, "previous", current)
                atomic_link(container, "current", previous)
            return
        source = Path(args.source).resolve(strict=True)
        version = verify_package(source)
        target = opt / ("keepharness-" + version)
        if exists(target):
            checked(target, opt)
            verify_package(target)
            if (source / "SHA256SUMS").read_bytes() != (target / "SHA256SUMS").read_bytes():
                refuse("Version already installed with different contents")
        browser_lines = {
            f'Exec="{h}/.local/bin/keepharness-open"' for h in {raw.rstrip("/"), str(home)}
        }
        browser = apps / "keepharness-browser.desktop"
        if browser.is_symlink():
            print(f"Skip symlinked browser entry: {browser}")
            drop_browser = False
        else:
            drop_browser = exact_entry(browser, browser_lines, browser=True)
        entry = apps / "keepharness.desktop"
        if exists(entry):
            checked(entry, apps)
            own = {
                f'Exec="{h}/.local/opt/keepharness/current/keepharness"'
                for h in {raw.rstrip("/"), str(home)}
            }
            own |= {
                f'Exec="{h}/{p.relative_to(home)}/keepharness"'
                for h in {raw.rstrip("/"), str(home)}
                for p in opt.iterdir()
                if marker(p)
            }
            if not exact_entry(entry, own) and not exact_entry(entry, browser_lines, browser=True):
                refuse("Foreign desktop entry preserved")
        old = target_of(container, "current", opt) if exists(container / "current") else None
        if old is None:
            for candidate in opt.iterdir():
                if marker(candidate) and exact_entry(
                    entry,
                    {
                        f'Exec="{h}/{candidate.relative_to(home)}/keepharness"'
                        for h in {raw.rstrip("/"), str(home)}
                    },
                ):
                    checked(candidate, opt)
                    old = candidate
                    break
        if args.dry_run:
            print(f"Install: {target}")
            return
        # Recover only the separately allowlisted mktemp stages while holding the lock.
        stages = [p for parent in (opt, apps) for p in parent.iterdir() if STAGE.fullmatch(p.name)]
        remove(stages)
        template = (source / "share/applications/keepharness.desktop").read_text().splitlines()
        content = (
            "\n".join(
                line for line in template if not line.startswith(("Exec=", "TryExec=", "Icon="))
            )
            + "\n"
        )
        content += (
            f'Exec="{container}/current/keepharness"\nTryExec={container}/current/keepharness\n'
            f"Icon={container}/current/share/icons/hicolor/256x256/apps/keepharness.png\n"
        )
        if old == target and entry.exists() and entry.read_text() == content and not drop_browser:
            print(f"KeepHarness {version} already installed; identical reinstall is a no-op")
            return
        if not exists(target):
            stage = Path(
                subprocess.check_output(
                    ["mktemp", "-d", str(opt / ".keepharness-XXXXXX")], text=True
                ).strip()
            )
            try:
                shutil.copytree(source, stage, dirs_exist_ok=True)
                verify_package(stage)
                os.rename(stage, target)
            finally:
                if exists(stage):
                    remove([stage])
        container.mkdir(exist_ok=True)
        if old != target or not exists(container / "current"):
            if old and old != target:
                atomic_link(container, "previous", old)
            atomic_link(container, "current", target)
        temp = Path(
            subprocess.check_output(
                ["mktemp", str(apps / ".keepharness-XXXXXX")], text=True
            ).strip()
        )
        try:
            temp.write_text(content)
            temp.chmod(0o644)
            os.replace(temp, entry)
        finally:
            if exists(temp):
                remove([temp])
        if drop_browser:
            browser.unlink()
        try:
            prune(opt, container)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            print(f"Installed; prune refused: {exc}", file=sys.stderr)
        print(f"KeepHarness {version} installed in {target}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"Desktop operation refused: {error}", file=sys.stderr)
        sys.exit(1)
