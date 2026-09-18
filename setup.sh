#!/bin/sh
set -eu
cd "$(dirname "$0")"
python3 -c 'import sys; assert sys.version_info >= (3,11), "Python 3.11+ é necessário"'
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
printf '\nPronto. Inicie com: .venv/bin/python -m control\nInventário sem alterações: .venv/bin/python -m control --scan\n'
