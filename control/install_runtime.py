#!/usr/bin/env python3
"""Build a pinned llama.cpp runtime below a project-local ``local_ai`` folder."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

VERSION = "b11003"
COMMIT = "7d6f5d02bb40fca0ab29e65fe4eb86eab6886f19"
SOURCE_URL = "https://github.com/ggml-org/llama.cpp.git"
BACKEND_OPTIONS = {
    "cpu": [],
    "vulkan": ["-DGGML_VULKAN=ON"],
    "cuda": ["-DGGML_CUDA=ON"],
}


def command(args: list[str], cwd: Path | None = None) -> None:
    print("+", " ".join(args), flush=True)
    subprocess.run(args, cwd=cwd, check=True)


def project_root(value: str) -> Path:
    root = Path(value).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"--root is not a directory: {root}")
    return root


def runtime_dir(root: Path) -> Path:
    return root / "local_ai" / "runtime" / f"llama-{VERSION}"


def require_tools() -> None:
    missing = [name for name in ("git", "cmake") if shutil.which(name) is None]
    if missing:
        raise RuntimeError("Missing required tools: " + ", ".join(missing))


def build(root: Path, backend: str, jobs: int) -> Path:
    if platform.system() != "Linux":
        raise RuntimeError("Portable runtime builds currently support Linux only")
    target = runtime_dir(root)
    if target.exists():
        raise FileExistsError(f"Runtime already exists; refusing to overwrite: {target}")
    require_tools()
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".llama-build-", dir=parent) as temporary:
        work = Path(temporary)
        source, build_dir, package = work / "source", work / "build", work / "package"
        command(
            [
                "git",
                "clone",
                "--depth",
                "1",
                "--branch",
                VERSION,
                "--single-branch",
                SOURCE_URL,
                str(source),
            ]
        )
        actual = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=source, text=True
        ).strip()
        if actual != COMMIT:
            raise RuntimeError(f"Unexpected llama.cpp revision: {actual}; expected {COMMIT}")
        configure = [
            "cmake",
            "-S",
            str(source),
            "-B",
            str(build_dir),
            "-DCMAKE_BUILD_TYPE=Release",
            "-DGGML_NATIVE=OFF",
            "-DCMAKE_BUILD_WITH_INSTALL_RPATH=ON",
            "-DCMAKE_INSTALL_RPATH=$ORIGIN",
        ]
        configure.extend(BACKEND_OPTIONS[backend])
        command(configure)
        command(
            [
                "cmake",
                "--build",
                str(build_dir),
                "--config",
                "Release",
                "--target",
                "llama-server",
                "--parallel",
                str(jobs),
            ]
        )
        binary_dir = build_dir / "bin"
        if not (binary_dir / "llama-server").is_file():
            raise RuntimeError(f"Build did not create {binary_dir / 'llama-server'}")
        shutil.copytree(binary_dir, package)
        shutil.copy2(source / "LICENSE", package / "LICENSE")
        metadata = {
            "runtime": "llama.cpp",
            "version": VERSION,
            "commit": COMMIT,
            "source": SOURCE_URL,
            "backend": backend,
            "platform": platform.platform(),
            "machine": platform.machine(),
        }
        (package / "runtime.json").write_text(
            json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
        )
        command([str(package / "llama-server"), "--help"])
        if target.exists():
            raise FileExistsError(f"Runtime appeared during build; refusing to overwrite: {target}")
        os.replace(package, target)
        # Verify relocation too: a temporary-directory RPATH must not count as success.
        command([str(target / "llama-server"), "--help"])
    print(f"Installed {VERSION} ({backend}) in {target}")
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="project root (default: current directory)")
    parser.add_argument("--backend", choices=sorted(BACKEND_OPTIONS), default="cpu")
    parser.add_argument("--jobs", type=int, default=os.cpu_count() or 1)
    args = parser.parse_args(argv)
    if args.jobs < 1:
        parser.error("--jobs must be at least 1")
    try:
        build(project_root(args.root), args.backend, args.jobs)
    except (OSError, RuntimeError, subprocess.CalledProcessError, ValueError) as error:
        print(f"install_runtime: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
