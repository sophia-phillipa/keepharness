#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
TH_STATE=$(mktemp -d)
TH_PID=''
TH_CHAT_PID=''
cleanup() { if [ -n "$TH_CHAT_PID" ]; then kill "$TH_CHAT_PID" 2>/dev/null || true; wait "$TH_CHAT_PID" 2>/dev/null || true; fi; if [ -n "$TH_PID" ]; then kill "$TH_PID" 2>/dev/null || true; wait "$TH_PID" 2>/dev/null || true; fi; rm -rf "$TH_STATE"; }
trap cleanup EXIT INT TERM
# Each campaign owns free ports; never attach a test to another checkout's server.
TH_PORTS=$("${PYTHON:-python3}" -c 'import os,socket
sockets=[]
for name in ("TAIL_HARNESS_TEST_ADMIN_PORT", "TAIL_HARNESS_TEST_CHAT_PORT"):
 s=socket.socket();s.bind(("127.0.0.1",int(os.environ.get(name,"0"))));sockets.append(s)
print(*(s.getsockname()[1] for s in sockets))')
TH_ADMIN_PORT=${TH_PORTS% *}
TH_CHAT_PORT=${TH_PORTS#* }
TH_ADMIN_URL="http://127.0.0.1:$TH_ADMIN_PORT"
TH_CHAT_URL="http://127.0.0.1:$TH_CHAT_PORT"
"${PYTHON:-python3}" -m control --port "$TH_ADMIN_PORT" --state "$TH_STATE" >"$TH_STATE/server.log" 2>&1 &
TH_PID=$!
"${PYTHON:-python3}" -c 'import sys,time,urllib.request
from pathlib import Path
url=sys.argv[1];expected=Path("agent_service/VERSION").read_text().strip()
for _ in range(80):
 try:
  content=urllib.request.urlopen(url+"/",timeout=1).read().decode();break
 except OSError:time.sleep(.25)
else:raise SystemExit("UI server unavailable")
assert "v"+expected in content,"Wrong admin version served"
assert urllib.request.urlopen(url+"/admin.js").read()==Path("control/admin.js").read_bytes(),"Wrong admin checkout served"' "$TH_ADMIN_URL"
kill -0 "$TH_PID"
"${PYTHON:-python3}" -c 'import json,sys,uuid;from pathlib import Path
root=Path(sys.argv[1]);port=int(sys.argv[2]);(root/"chat.json").write_text(json.dumps({"state_dir":str(root/"chat"),"bind":"127.0.0.1","port":port,"local_access":True,"clients":{"local":{"sha256":"0"*64,"projects":["sem-projeto"]}},"projects":{"sem-projeto":{}},"services":{},"origins":[f"http://127.0.0.1:{port}"],"config_revision":uuid.uuid4().hex}))' "$TH_STATE" "$TH_CHAT_PORT"
TAIL_HARNESS_AGENT_CONFIG="$TH_STATE/chat.json" "${PYTHON:-python3}" -m agent_service.app >"$TH_STATE/chat.log" 2>&1 &
TH_CHAT_PID=$!
"${PYTHON:-python3}" -c 'import json,sys,time,urllib.request
from pathlib import Path
for _ in range(80):
 try:
  served=json.load(urllib.request.urlopen(sys.argv[1]+"/v1/version",timeout=1));break
 except OSError:time.sleep(.25)
else:raise SystemExit("Harness test server unavailable")
assert served["version"]==Path("agent_service/VERSION").read_text().strip(),"Wrong harness version served"
assert served["config_revision"]==json.loads(Path(sys.argv[2]).read_text())["config_revision"],"Wrong harness instance served"
print("VERIFIED UI servers",sys.argv[1],"version",served["version"],"build",served["build"])' "$TH_CHAT_URL" "$TH_STATE/chat.json"
kill -0 "$TH_CHAT_PID"
echo "ADMIN $TH_ADMIN_URL"
# Enumerate every regression, including the persona matrix.
ui_failures=0
if [ "$#" -gt 0 ]; then set -- "$@"; else set -- tests/*.spec.cjs tests/personas/*.spec.cjs; fi
for test_file in "$@"; do
  [ -e "$test_file" ] || continue
  echo "RUN $test_file"
  if ADMIN_URL="$TH_ADMIN_URL" HARNESS_URL="$TH_CHAT_URL" node "$test_file"; then
    echo "PASS FILE $test_file"
  else
    echo "FAIL FILE $test_file"
    ui_failures=1
  fi
done
exit "$ui_failures"
