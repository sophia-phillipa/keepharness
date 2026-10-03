#!/bin/sh
# Install or upgrade KeepHarness for this Linux user, on the host (never inside a container).
#   ./install.sh [--port N] [--boot]        build a wheel, install it, register the user service
#   ./install.sh --dev [--port N] [--boot]  editable install: the service runs this checkout
#   ./install.sh --check-only               the same checks and a trial install in a temporary
#                                           folder; nothing outside it changes
#   ./install.sh --merge-legacy [--apply]   heal a Tail Harness state split in two (dry run first)
#   ./install.sh --rollback-to-0.14         remove the service and give the state back to 0.14
set -eu
cd "$(dirname "$0")"
python3 -c 'import sys; assert sys.version_info >= (3,11), "Python 3.11+ is required"'
case "${1:-}" in
  --merge-legacy) shift; exec python3 -m control.state_merge "$@" ;;
  --rollback-to-0.14) exec python3 -m control.install --rollback-to-0.14 ;;
esac
TH_CHECK=
TH_DEV=
for arg in "$@"; do
  case "$arg" in
    --check-only) TH_CHECK=1 ;;
    --dev) TH_DEV=1 ;;
  esac
done
# Refuses inside a container, on a port another program holds and on state that cannot move.
python3 -m control.install --check-only "$@"

export PIP_DISABLE_PIP_VERSION_CHECK=1
TH_TMP=$(mktemp -d "${TMPDIR:-/tmp}/keepharness-install.XXXXXX")
TH_STOPPED=
cleanup() {
  status=$?
  rm -rf "$TH_TMP"
  if [ "$status" -ne 0 ] && [ -n "$TH_STOPPED" ]; then
    echo "KeepHarness is not installed yet: fix the error above and run ./install.sh again," \
      "or go back to Tail Harness 0.14 with ./install.sh --rollback-to-0.14." >&2
  fi
}
trap cleanup EXIT
trap 'exit 1' INT TERM
preflight_failed() {
  echo "$1 Nothing was stopped or moved." >&2
  exit 1
}
# Preflight: a trial install in a temporary venv, before anything is stopped or moved.
python3 -m venv "$TH_TMP/venv" ||
  preflight_failed "$(command -v python3) cannot create a virtual environment (on Debian/Ubuntu: install python3-venv)."
# Build from a copy of the tracked files: the checkout may be read-only and stays untouched.
TH_SOURCE=.
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  TH_SOURCE=$TH_TMP/source
  mkdir "$TH_SOURCE"
  git ls-files -z | tar --null --ignore-failed-read -T - -cf - | tar -xf - -C "$TH_SOURCE"
fi
"$TH_TMP/venv/bin/python" -m pip wheel --quiet --no-deps --wheel-dir "$TH_TMP/wheel" "$TH_SOURCE" ||
  preflight_failed "The package did not build."
TH_WHEEL=$(ls "$TH_TMP"/wheel/*.whl)
"$TH_TMP/venv/bin/python" -m pip install --quiet "$TH_WHEEL" ||
  preflight_failed "The package or its dependencies did not install (offline?)."
"$TH_TMP/venv/bin/python" -m control.install_check ||
  preflight_failed "The installed package failed its smoke test."
if [ -n "$TH_CHECK" ]; then
  exit 0
fi

# Upgrading from Tail Harness (before 0.15.0): stop its service, then move its state once.
TH_STOPPED=1
systemctl --user stop tail-harness.service 2>/dev/null || true
python3 control/product.py --migrate-state
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
if [ -n "$TH_DEV" ]; then
  "$TH_VENV/bin/python" -m pip install --editable .
else
  # The second step replaces an install of the same version (an editable one included).
  "$TH_VENV/bin/python" -m pip install --quiet "$TH_WHEEL"
  "$TH_VENV/bin/python" -m pip install --quiet --force-reinstall --no-deps "$TH_WHEEL"
fi
"$TH_VENV/bin/$TH_PRODUCT_SLUG-install" "$@"
