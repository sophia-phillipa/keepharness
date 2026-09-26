#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
TH_STATE=$(mktemp -d)
TH_PID=''
TH_CHAT_PID=''
cleanup() { if [ -n "$TH_CHAT_PID" ]; then kill "$TH_CHAT_PID" 2>/dev/null || true; wait "$TH_CHAT_PID" 2>/dev/null || true; fi; if [ -n "$TH_PID" ]; then kill "$TH_PID" 2>/dev/null || true; wait "$TH_PID" 2>/dev/null || true; fi; rm -rf "$TH_STATE"; }
trap cleanup EXIT INT TERM
"${PYTHON:-python3}" -m control --port 18094 --state "$TH_STATE" >"$TH_STATE/server.log" 2>&1 &
TH_PID=$!
"${PYTHON:-python3}" -c 'import time,urllib.request
for _ in range(80):
 try:
  urllib.request.urlopen("http://127.0.0.1:18094/",timeout=1);break
 except OSError:time.sleep(.25)
else:raise SystemExit("UI server unavailable")'
"${PYTHON:-python3}" -c 'import json,sys;from pathlib import Path
root=Path(sys.argv[1]);(root/"chat.json").write_text(json.dumps({"state_dir":str(root/"chat"),"bind":"127.0.0.1","port":18095,"local_access":True,"clients":{"local":{"sha256":"0"*64,"projects":["sem-projeto"]}},"projects":{"sem-projeto":{}},"services":{},"origins":["http://127.0.0.1:18095"]}))' "$TH_STATE"
LOCAL_AGENT_CONFIG="$TH_STATE/chat.json" "${PYTHON:-python3}" -m agent_service.app >"$TH_STATE/chat.log" 2>&1 &
TH_CHAT_PID=$!
"${PYTHON:-python3}" -c 'import time,urllib.request
for _ in range(80):
 try:
  urllib.request.urlopen("http://127.0.0.1:18095/",timeout=1);break
 except OSError:time.sleep(.25)
else:raise SystemExit("Harness test server unavailable")'
# Enumerate every browser regression so new feature tests cannot be omitted.
ui_failures=0
for test_file in tests/*.spec.cjs tests/personas/*.spec.cjs; do
  [ -e "$test_file" ] || continue
  echo "RUN $test_file"
  if ADMIN_URL=http://127.0.0.1:18094 HARNESS_URL=http://127.0.0.1:18095 node "$test_file"; then
    echo "PASS FILE $test_file"
  else
    echo "FAIL FILE $test_file"
    ui_failures=1
  fi
done
exit "$ui_failures"
