#!/usr/bin/env bash
# Starts the Tail Harness desktop client. TAIL_HARNESS_PYTHON points at a Python
# environment with Tail Harness installed (default: the repository's .venv).
# On an immutable host (Bazzite), run it inside the development container:
#   distrobox-enter -n <container> -- <repo>/desktop/launch-linux.sh
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export TAIL_HARNESS_PYTHON="${TAIL_HARNESS_PYTHON:-$ROOT/.venv/bin/python}"
exec "$ROOT/desktop/node_modules/electron/dist/electron" "$ROOT/desktop" "$@"
