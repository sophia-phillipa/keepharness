#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
TH_STATE=$(mktemp -d)
TH_PID=''
TH_CHAT_PID=''
TH_ADMIN_PORT=${TH_ADMIN_PORT:-18094}
TH_CHAT_PORT=${TH_CHAT_PORT:-18095}
cleanup() { if [ -n "$TH_CHAT_PID" ]; then kill "$TH_CHAT_PID" 2>/dev/null || true; wait "$TH_CHAT_PID" 2>/dev/null || true; fi; if [ -n "$TH_PID" ]; then kill "$TH_PID" 2>/dev/null || true; wait "$TH_PID" 2>/dev/null || true; fi; rm -rf "$TH_STATE"; }
trap cleanup EXIT INT TERM
# Refuse occupied ports instead of accidentally testing another worktree's server.
"${PYTHON:-python3}" -c 'import socket,sys
listeners=[]
for value in sys.argv[1:]:
 listener=socket.socket()
 listener.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
 try:
  listener.bind(("127.0.0.1",int(value)))
  listener.listen(1)
 except OSError as exc:raise SystemExit(f"UI test port {value} unavailable: {exc}")
 listeners.append(listener)' "$TH_ADMIN_PORT" "$TH_CHAT_PORT"
"${PYTHON:-python3}" -m control --port "$TH_ADMIN_PORT" --state "$TH_STATE" >"$TH_STATE/server.log" 2>&1 &
TH_PID=$!
"${PYTHON:-python3}" -c 'import sys,time,urllib.request
for _ in range(80):
 try:
  urllib.request.urlopen("http://127.0.0.1:"+sys.argv[1]+"/",timeout=1);break
 except OSError:time.sleep(.25)
else:raise SystemExit("UI server unavailable")' "$TH_ADMIN_PORT"
kill -0 "$TH_PID"
"${PYTHON:-python3}" -c 'import json,sys;from pathlib import Path
root=Path(sys.argv[1]);port=int(sys.argv[2]);(root/"chat.json").write_text(json.dumps({"state_dir":str(root/"chat"),"bind":"127.0.0.1","port":port,"local_access":True,"clients":{"local":{"sha256":"0"*64,"projects":["sem-projeto"]}},"projects":{"sem-projeto":{}},"services":{},"origins":[f"http://127.0.0.1:{port}"]}))' "$TH_STATE" "$TH_CHAT_PORT"
TAIL_HARNESS_AGENT_CONFIG="$TH_STATE/chat.json" "${PYTHON:-python3}" -m agent_service.app >"$TH_STATE/chat.log" 2>&1 &
TH_CHAT_PID=$!
"${PYTHON:-python3}" -c 'import sys,time,urllib.request
for _ in range(80):
 try:
  urllib.request.urlopen("http://127.0.0.1:"+sys.argv[1]+"/",timeout=1);break
 except OSError:time.sleep(.25)
else:raise SystemExit("Harness test server unavailable")' "$TH_CHAT_PORT"
kill -0 "$TH_CHAT_PID"
"${PYTHON:-python3}" -c 'import json,sys,urllib.request;from pathlib import Path
expected=Path("agent_service/VERSION").read_text().strip()
origin="http://127.0.0.1:"+sys.argv[1]
with urllib.request.urlopen(origin+"/v1/version",timeout=5) as response:actual=json.load(response)["version"]
with urllib.request.urlopen(origin+"/",timeout=5) as response:html=response.read().decode()
if actual!=expected or f"Release: {expected}" not in html:raise SystemExit(f"Served release mismatch: expected {expected}, API {actual}")
print(f"SERVING Tail Harness {actual} at {origin}")' "$TH_CHAT_PORT"
# Enumerate every browser regression so new feature tests cannot be omitted.
# Optional arguments restrict the run to the given spec files.
ui_failures=0
if [ "$#" -gt 0 ]; then set -- "$@"; else set -- tests/*.spec.cjs tests/personas/*.spec.cjs; fi
for test_file in "$@"; do
  [ -e "$test_file" ] || continue
  echo "RUN $test_file"
  if ADMIN_URL="http://127.0.0.1:$TH_ADMIN_PORT" HARNESS_URL="http://127.0.0.1:$TH_CHAT_PORT" node "$test_file"; then
    echo "PASS FILE $test_file"
  else
    echo "FAIL FILE $test_file"
    ui_failures=1
  fi
done
exit "$ui_failures"
