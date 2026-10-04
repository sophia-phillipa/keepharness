#!/bin/sh
set -eu
cd "$(dirname "$0")"
python3 -c 'import sys; assert sys.version_info >= (3,11), "Python 3.11+ is required"'
python3 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements.txt
.venv/bin/python -m pip install --no-deps --no-build-isolation .
printf '\nDone. Start with: .venv/bin/python -m control\nInventory without changes: .venv/bin/python -m control --scan\n'
