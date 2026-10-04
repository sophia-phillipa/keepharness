#!/usr/bin/env bash
# Starts the KeepHarness desktop client. KEEPHARNESS_PYTHON points at a Python
# environment with KeepHarness installed (default: the one install.sh created,
# else the repository's .venv).
# On an immutable host (Bazzite), run it inside the development container:
#   distrobox-enter -n <container> -- <repo>/desktop/launch-linux.sh
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [ -z "${KEEPHARNESS_PYTHON:-}" ]; then
  VENV="$(python3 "$ROOT/control/product.py" --field venv 2>/dev/null || true)"
  if [ -n "$VENV" ] && [ -x "$VENV/bin/python" ]; then KEEPHARNESS_PYTHON="$VENV/bin/python"; else KEEPHARNESS_PYTHON="$ROOT/.venv/bin/python"; fi
fi
export KEEPHARNESS_PYTHON
exec "$ROOT/desktop/node_modules/electron/dist/electron" "$ROOT/desktop" "$@"
