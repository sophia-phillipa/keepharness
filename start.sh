#!/bin/sh
set -eu
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  printf 'Execute ./setup.sh primeiro.\n' >&2
  exit 1
fi
exec .venv/bin/python -m control "$@"
