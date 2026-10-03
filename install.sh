#!/bin/sh
set -eu
cd "$(dirname "$0")"
python3 -c 'import sys; assert sys.version_info >= (3,11), "Python 3.11+ is required"'
if [ "${1:-}" = "--check-only" ]; then
  # Creating the venv in the new folder would make the pending Tail Harness move refuse to merge.
  python3 control/product.py --check-migrated
else
  # Upgrading from Tail Harness (before 0.15.0): stop its service, then move its state once.
  systemctl --user stop tail-harness.service 2>/dev/null || true
  python3 control/product.py --migrate-state
fi
TH_VENV=$(python3 control/product.py --field venv)
TH_PRODUCT_SLUG=$(python3 control/product.py --field slug)
# A venv moved with the Tail Harness folder keeps the old path in its scripts: rebuild it.
if [ -f "$TH_VENV/bin/pip" ] && ! grep -qF -- "$TH_VENV/bin/" "$TH_VENV/bin/pip"; then
  python3 -m venv --clear "$TH_VENV"
else
  python3 -m venv "$TH_VENV"
fi
# Its console scripts (tail-harness, tail-harness-install, ...) would outlive the rename.
"$TH_VENV/bin/python" -m pip uninstall --yes tail-harness 2>/dev/null || true
"$TH_VENV/bin/python" -m pip install --editable .
if [ "${1:-}" = "--check-only" ]; then
  "$TH_VENV/bin/python" -m control.install_check
else
  "$TH_VENV/bin/$TH_PRODUCT_SLUG-install" "$@"
fi
