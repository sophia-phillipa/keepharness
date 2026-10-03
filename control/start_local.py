"""Start one validated llama.cpp local profile without carrying model-specific code."""

import argparse
import json
import os
import secrets
import shlex
from pathlib import Path

from .local_models import MANAGED_ALIAS, launch_command, validate_profile


def resolve_profile(profile, root):
    if not isinstance(profile, dict):
        raise ValueError("Invalid local profile.")
    result = dict(profile)
    for name in ("binary", "model_file", "mmproj_file"):
        if name in result and result[name]:
            path = Path(result[name]).expanduser()
            result[name] = str((path if path.is_absolute() else root / path).resolve())
    return validate_profile(result)


def ensure_key(path):
    path = Path(path)
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return path
    with os.fdopen(fd, "w") as out:
        out.write(secrets.token_urlsafe(32))
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description="Starts a validated local llama.cpp profile.")
    parser.add_argument("--profile", required=True)
    parser.add_argument("--root", required=True)
    parser.add_argument("--port", type=int, default=8096)
    parser.add_argument("--alias", default=MANAGED_ALIAS)
    parser.add_argument("--key-file", required=True)
    parser.add_argument(
        "--check", action="store_true", help="Show the command without starting the runtime."
    )
    args = parser.parse_args(argv)
    root = Path(args.root).expanduser().resolve()
    if not root.is_dir():
        raise ValueError("Project root not found.")
    profile_path = Path(args.profile).expanduser()
    profile_path = profile_path if profile_path.is_absolute() else root / profile_path
    profile = resolve_profile(json.loads(profile_path.read_text()), root)
    key = Path(args.key_file).expanduser()
    key = key if key.is_absolute() else root / key
    command = launch_command(profile, key_file=key.resolve(), port=args.port, alias=args.alias)
    if args.check:
        print(shlex.join(command))
        return
    ensure_key(key)
    os.environ.update(MANGOHUD="0", DISABLE_MANGOHUD="1")
    os.execv(command[0], command)


if __name__ == "__main__":
    main()
