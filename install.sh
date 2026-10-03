#!/bin/sh
set -eu
cd "$(dirname "$0")"
python3 -c 'import sys; assert sys.version_info >= (3,11), "Python 3.11+ is required"'
# Upgrading from Tail Harness (before 0.15.0): stop its service, then move its state once.
systemctl --user stop tail-harness.service 2>/dev/null || true
python3 control/product.py --migrate-state
TH_VENV=$(python3 control/product.py --field venv)
TH_PRODUCT_SLUG=$(python3 control/product.py --field slug)
python3 -m venv "$TH_VENV"
"$TH_VENV/bin/python" -m pip install --editable .
if [ "${1:-}" = "--check-only" ]; then
  "$TH_VENV/bin/python" -m control.install_check
else
  "$TH_VENV/bin/$TH_PRODUCT_SLUG-install" "$@"
fi
