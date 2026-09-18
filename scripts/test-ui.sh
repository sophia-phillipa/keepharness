#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
TH_STATE=$(mktemp -d)
TH_PID=''
cleanup() { if [ -n "$TH_PID" ]; then kill "$TH_PID" 2>/dev/null || true; wait "$TH_PID" 2>/dev/null || true; fi; rm -rf "$TH_STATE"; }
trap cleanup EXIT INT TERM
"${PYTHON:-python3}" -m control --port 18094 --state "$TH_STATE" >"$TH_STATE/server.log" 2>&1 &
TH_PID=$!
"${PYTHON:-python3}" -c 'import time,urllib.request
for _ in range(80):
 try:
  urllib.request.urlopen("http://127.0.0.1:18094/",timeout=1);break
 except OSError:time.sleep(.25)
else:raise SystemExit("UI server unavailable")'
ADMIN_URL=http://127.0.0.1:18094 node tests/admin.spec.cjs
