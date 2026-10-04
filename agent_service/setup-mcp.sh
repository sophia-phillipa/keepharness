#!/bin/sh
# Linux and macOS: chmod +x setup-mcp.sh
# Run: ./setup-mcp.sh 'https://your-tailscale-server'
set -eu
# Generated identity block; the installer is downloadable on its own.
# The bridge lives in its own folder: the product state folder and its venv belong to the server.
TH_PRODUCT_SLUG=keepharness
TH_PRODUCT_ENV=KEEPHARNESS
TH_PRODUCT_BRIDGE=.local/share/keepharness-mcp
TH_PRODUCT_MCP=keepharness
if [ "$#" -ne 1 ]; then
    printf '%s\n' 'Usage: ./setup-mcp.sh HARNESS_URL' >&2
    exit 1
fi
server=${1%/}
case "$server" in
    http://?*|https://?*) ;;
    *) printf '%s\n' 'Provide the http:// or https:// URL of the harness.' >&2; exit 1 ;;
esac
for dependency in python3 curl claude; do
    if ! command -v "$dependency" >/dev/null 2>&1; then
        printf 'Install %s and run again. Requirements: Python 3.10+, curl and Claude Code.\n' "$dependency" >&2
        exit 1
    fi
done
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else "Install Python 3.10 or newer.")'
folder="$HOME/$TH_PRODUCT_BRIDGE"
mkdir -p "$folder"
python3 -m venv "$folder/venv"
temporary=$(mktemp "$folder/mcp_bridge.XXXXXX")
lock=$(mktemp "$folder/bridge-requirements.XXXXXX")
trap 'rm -f "$temporary" "$lock"' EXIT
trap 'exit 1' HUP INT TERM
# The server publishes the bridge's dependencies as a hashed lock: pip refuses anything else.
curl -fS --connect-timeout 15 --max-time 120 "$server/bridge-requirements.txt" -o "$lock"
"$folder/venv/bin/python" -m pip install --require-hashes -r "$lock"
curl -fS --connect-timeout 15 --max-time 120 "$server/mcp_bridge.py" -o "$temporary"
"$folder/venv/bin/python" -c 'import ast, pathlib, sys; ast.parse(pathlib.Path(sys.argv[1]).read_text())' "$temporary"
mv "$lock" "$folder/bridge-requirements.txt"
mv "$temporary" "$folder/mcp_bridge.py"
claude mcp add --transport stdio --scope user --env "${TH_PRODUCT_ENV}_AGENT_URL=$server" "$TH_PRODUCT_MCP" -- "$folder/venv/bin/python" "$folder/mcp_bridge.py"
printf '%s\n' 'Connector registered. Open Claude Code and use /mcp to verify the connection.'
