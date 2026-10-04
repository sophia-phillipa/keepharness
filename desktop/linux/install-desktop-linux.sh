#!/usr/bin/env bash
# The helper uses only Python's standard library; the backend also requires Python.
set -euo pipefail
[[ ! -L "$0" ]] || { echo 'Installer must not be a symlink.' >&2; exit 1; }
source_dir="$(dirname -- "$0")"
exec python3 "$source_dir/install_desktop_linux.py" --source "$source_dir" "$@"
