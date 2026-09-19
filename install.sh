#!/bin/sh
set -eu
cd "$(dirname "$0")"
python3 -c 'import sys; assert sys.version_info >= (3,11), "Python 3.11+ é necessário"'
TH_VENV="${TAIL_HARNESS_VENV:-$HOME/.local/share/tail-harness/venv}"
python3 -m venv "$TH_VENV"
"$TH_VENV/bin/python" -m pip install --editable '.[test]'
"$TH_VENV/bin/python" -m pytest -q
if [ "${1:-}" = "--check-only" ]; then
  "$TH_VENV/bin/python" -m control.install_check
else
  "$TH_VENV/bin/tail-harness-install" "$@"
fi
