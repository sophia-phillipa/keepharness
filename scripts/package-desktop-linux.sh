#!/usr/bin/env bash
# Packages the portable Linux desktop client into dist/keepharness-<version>-linux-x64/.
# Nothing is downloaded: it copies an Electron runtime that is already on disk.
#   KEEPHARNESS_ELECTRON_DIST=<electron>/dist scripts/package-desktop-linux.sh
# (default: desktop/node_modules/electron/dist). The backend stays Python; see desktop/linux/launcher.sh.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
ELECTRON_DIST="${KEEPHARNESS_ELECTRON_DIST:-$ROOT/desktop/node_modules/electron/dist}"
if [ ! -x "$ELECTRON_DIST/electron" ] || [ ! -f "$ELECTRON_DIST/version" ]; then
  echo "No Electron runtime in $ELECTRON_DIST. Set KEEPHARNESS_ELECTRON_DIST to its dist folder." >&2
  exit 1
fi
VERSION="$(python3 -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')"
case "$VERSION" in
  '' | *[!0-9A-Za-z.+-]*) echo "Invalid project version: $VERSION" >&2; exit 1 ;;
esac
ELECTRON_VERSION="$(tr -d '[:space:]' < "$ELECTRON_DIST/version")"
OUT="$ROOT/dist/keepharness-$VERSION-linux-x64"
APP="$OUT/resources/app"

rm -rf -- "$OUT"
mkdir -p "$OUT"
cp -a "$ELECTRON_DIST/." "$OUT/"
mv "$OUT/electron" "$OUT/keepharness-bin"
mv "$OUT/LICENSE" "$OUT/LICENSE.electron.txt"
rm "$OUT/version" # recorded in build-manifest.json; VERSION below is KeepHarness's own
install -Dm644 LICENSE "$OUT/LICENSE"
printf '%s\n' "$VERSION" > "$OUT/VERSION"

# The app: resources/app wins over the runtime's default_app.asar.
install -Dm644 desktop/main.cjs "$APP/main.cjs"
install -Dm644 desktop/policy.cjs "$APP/policy.cjs"
install -Dm644 desktop/splash.html "$APP/splash.html"
install -Dm644 desktop/build/icon.png "$APP/build/icon.png"
install -Dm644 desktop/build/splash.jpg "$APP/build/splash.jpg"
printf '{\n  "name": "keepharness",\n  "version": "%s",\n  "description": "KeepHarness desktop client",\n  "main": "main.cjs"\n}\n' \
  "$VERSION" > "$APP/package.json"

install -m755 desktop/linux/launcher.sh "$OUT/keepharness"
install -m755 desktop/linux/install-desktop-linux.sh "$OUT/install-desktop-linux.sh"
install -Dm644 desktop/linux/keepharness.desktop "$OUT/share/applications/keepharness.desktop"
for size in 16 24 32 48 64 128 256 512; do
  install -Dm644 "desktop/build/icons/${size}x${size}.png" "$OUT/share/icons/hicolor/${size}x${size}/apps/keepharness.png"
done

DIRTY=false
if [ -n "$(git status --porcelain)" ]; then DIRTY=true; fi
cat > "$OUT/build-manifest.json" <<JSON
{
  "version": "$VERSION",
  "commit": "$(git rev-parse HEAD)",
  "dirty": $DIRTY,
  "electron": "$ELECTRON_VERSION",
  "keepharness_bin_sha256": "$(sha256sum "$OUT/keepharness-bin" | cut -d' ' -f1)"
}
JSON

echo "Linux desktop package created at $OUT ($(du -sh "$OUT" | cut -f1))"
