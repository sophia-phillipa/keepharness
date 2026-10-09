#!/bin/sh
# Install or upgrade KeepHarness for this Linux user, on the host (never inside a container).
#   ./install.sh [--port N] [--boot]        build a wheel, install it, register the user service
#   ./install.sh --dev [--port N] [--boot]  editable install: the service runs this checkout
#   ./install.sh --check-only               the same checks and a trial install in a temporary
#                                           folder; nothing outside it changes
#   ./install.sh --require-desktop          fail when no desktop package can be installed
#                                           (see KEEPHARNESS_DESKTOP_PACKAGE below)
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
TH_REQUIRE=
# --require-desktop is for this script only: it is dropped from the arguments forwarded below.
for arg in "$@"; do
  shift
  case "$arg" in
    --require-desktop) TH_REQUIRE=1; continue ;;
    --check-only) TH_CHECK=1 ;;
    --dev) TH_DEV=1 ;;
  esac
  set -- "$@" "$arg"
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
    echo "KeepHarness setup is incomplete: fix the error above and run ./install.sh again," \
      "or go back to Tail Harness 0.14 with ./install.sh --rollback-to-0.14." >&2
  fi
}
trap cleanup EXIT
trap 'exit 1' INT TERM
preflight_failed() {
  echo "$1 Nothing was stopped or moved." >&2
  exit 1
}
# The desktop app comes from an unpacked package, never built or downloaded here: the folder named
# by KEEPHARNESS_DESKTOP_PACKAGE (an explicit input, so a bad one is an error), else
# dist/keepharness-<version>-linux-x64. Its version must be the one in pyproject.toml.
TH_VERSION=$(sed -n 's/^version = "\(.*\)"$/\1/p' pyproject.toml | head -n 1)
TH_PKG=
TH_SKIP=
desktop_package() { # $1 = package folder; sets TH_SKIP to the reason when it cannot be used
  [ -n "$TH_VERSION" ] || { TH_SKIP="pyproject.toml has no project version"; return 1; }
  [ -d "$1" ] || { TH_SKIP="there is no package folder at $1"; return 1; }
  # The checkout's desktop installer checks SHA256SUMS and the build manifest and changes nothing.
  found=$(python3 desktop/linux/install_desktop_linux.py --source "$1" --verify 2>"$TH_TMP/verify.err") ||
    { TH_SKIP="the package at $1 failed its check ($(cat "$TH_TMP/verify.err"))"; return 1; }
  [ "$found" = "$TH_VERSION" ] ||
    { TH_SKIP="the package at $1 is version $found, not $TH_VERSION"; return 1; }
  TH_PKG=$1
}
if [ -n "${KEEPHARNESS_DESKTOP_PACKAGE:-}" ]; then
  case "$KEEPHARNESS_DESKTOP_PACKAGE" in
    /*) desktop_package "$KEEPHARNESS_DESKTOP_PACKAGE" || true ;;
    *) TH_SKIP="KEEPHARNESS_DESKTOP_PACKAGE must be an absolute path" ;;
  esac
  [ -n "$TH_PKG" ] || preflight_failed "The desktop package was rejected: $TH_SKIP."
elif [ -n "$TH_DEV" ]; then
  TH_SKIP="--dev runs this checkout and installs no desktop app unless KEEPHARNESS_DESKTOP_PACKAGE is set"
else
  desktop_package "dist/keepharness-$TH_VERSION-linux-x64" || true
fi
[ -n "$TH_PKG" ] || [ -z "$TH_REQUIRE" ] ||
  preflight_failed "No desktop app can be installed (--require-desktop): $TH_SKIP."
if [ -n "$TH_PKG" ]; then echo "Desktop package: $TH_PKG"; else echo "No desktop package: $TH_SKIP."; fi
# Preflight: a trial install in a temporary venv, before anything is stopped or moved.
python3 -m venv "$TH_TMP/venv" ||
  preflight_failed "$(command -v python3) cannot create a virtual environment (on Debian/Ubuntu: install python3-venv)."
# Build from a copy of the source: the checkout may be read-only and stays untouched. This folder is
# the git repository itself: copy its tracked and untracked files minus the ignored ones. A tarball
# extracted inside another repository, or no git at all: copy the tree minus build, state and secrets.
TH_SOURCE=$TH_TMP/source
mkdir "$TH_SOURCE"
if [ "$(git rev-parse --show-toplevel 2>/dev/null)" = "$(pwd -P)" ]; then
  git ls-files -z --cached --others --exclude-standard |
    tar --null --ignore-failed-read -T - -cf - | tar -xf - -C "$TH_SOURCE"
else
  tar --exclude=.git --exclude=.venv --exclude=__pycache__ --exclude=.pytest_cache \
    --exclude=node_modules --exclude=graphify-out --exclude=local_ai --exclude=local-ai \
    --exclude='*.egg-info' --exclude='.env*' --exclude='*.key' --exclude='*.gguf' \
    --exclude='*.sqlite3*' --exclude=./build --exclude=./dist --exclude=./state \
    --exclude=./reports -cf - . | tar -xf - -C "$TH_SOURCE"
fi
# requirements.txt is a hashed lock (it also pins the build backend), so the build needs no index.
"$TH_TMP/venv/bin/python" -m pip install --quiet --require-hashes -r "$TH_SOURCE/requirements.txt" ||
  preflight_failed "The locked dependencies did not install (offline, or a hash did not match)."
"$TH_TMP/venv/bin/python" -m pip wheel --quiet --no-deps --no-build-isolation \
  --wheel-dir "$TH_TMP/wheel" "$TH_SOURCE" ||
  preflight_failed "The package did not build."
TH_WHEEL=$(ls "$TH_TMP"/wheel/*.whl)
"$TH_TMP/venv/bin/python" -m pip install --quiet --no-deps "$TH_WHEEL" ||
  preflight_failed "The package did not install."
"$TH_TMP/venv/bin/python" -m control.install_check ||
  preflight_failed "The installed package failed its smoke test."
if [ -n "$TH_CHECK" ]; then
  exit 0
fi

# Work may have arrived while the trial environment was built. Check before any live changes.
python3 -m control.install --check-only "$@"
# Upgrading from Tail Harness (before 0.15.0): stop its service, then move its state once.
TH_STOPPED=1
systemctl --user stop tail-harness.service 2>/dev/null || true
python3 -m control.product --migrate-state
TH_VENV=$(python3 -m control.product --field venv)
TH_PRODUCT_SLUG=$(python3 -m control.product --field slug)
# A venv moved with the Tail Harness folder keeps the old path in its scripts: rebuild it.
if [ -f "$TH_VENV/bin/pip" ] && ! grep -qF -- "$TH_VENV/bin/" "$TH_VENV/bin/pip"; then
  python3 -m venv --clear "$TH_VENV"
else
  python3 -m venv "$TH_VENV"
fi
# Its console scripts (tail-harness, tail-harness-install, ...) would outlive the rename.
"$TH_VENV/bin/python" -m pip uninstall --yes tail-harness 2>/dev/null || true
"$TH_VENV/bin/python" -m pip install --quiet --require-hashes -r "$TH_SOURCE/requirements.txt"
if [ -n "$TH_DEV" ]; then
  "$TH_VENV/bin/python" -m pip install --editable .
else
  # Replaces an install of the same version (an editable one included).
  "$TH_VENV/bin/python" -m pip install --quiet --force-reinstall --no-deps "$TH_WHEEL"
fi
"$TH_VENV/bin/$TH_PRODUCT_SLUG-install" "$@"
# The service is done: a desktop failure from here on is not a reason to go back to Tail Harness.
TH_STOPPED=
if [ -n "$TH_PKG" ]; then
  if "$TH_PKG/install-desktop-linux.sh"; then
    echo "Service and desktop app installed (KeepHarness $TH_VERSION)."
    exit 0
  fi
  echo "The service is installed and running; the desktop app was not installed:" \
    "the desktop installer failed (see above). Run ./install.sh again after fixing it." >&2
  [ -z "$TH_REQUIRE" ] || exit 1
  exit 0
fi
echo "Service installed; desktop app skipped: $TH_SKIP. Build it with ./scripts/package-desktop-linux.sh"
