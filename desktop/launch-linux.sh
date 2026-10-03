#!/usr/bin/env bash
# Starts the KeepHarness desktop client. KEEPHARNESS_PYTHON points at a Python
# environment with KeepHarness installed (default: the repository's .venv).
# On an immutable host (Bazzite), run it inside the development container:
#   distrobox-enter -n <container> -- <repo>/desktop/launch-linux.sh
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export KEEPHARNESS_PYTHON="${KEEPHARNESS_PYTHON:-$ROOT/.venv/bin/python}"
exec "$ROOT/desktop/node_modules/electron/dist/electron" "$ROOT/desktop" "$@"
