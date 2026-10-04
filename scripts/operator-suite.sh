#!/bin/sh
# KeepHarness operator suite: scripted walkthroughs of the real app (docs/operator-suite.md).
#   scripts/operator-suite.sh                      headless, fixture mode, every area
#   scripts/operator-suite.sh --visible --target desktop   watch the desktop app on this screen
#   scripts/operator-suite.sh --mode real --url URL --admin-url URL --budget 3 --areas shell,new-chat
# Headless runs (the default) get a private X display from xvfb-run, so the desktop
# window never appears on the user's screen; --visible uses the current display.
set -eu
cd "$(dirname "$0")/.."
export PYTHON="${PYTHON:-$PWD/.venv/bin/python}"
visible=0
for arg in "$@"; do [ "$arg" = "--visible" ] && visible=1; done
if [ "$visible" = 1 ]; then
  exec node tests/operator/run-operator.cjs "$@"
fi
command -v xvfb-run >/dev/null 2>&1 || { echo "xvfb-run is required for headless runs (or pass --visible)" >&2; exit 2; }
exec env -u DISPLAY -u WAYLAND_DISPLAY xvfb-run -a node tests/operator/run-operator.cjs "$@"
