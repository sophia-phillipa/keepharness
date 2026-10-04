#!/usr/bin/env bash
# Build an immutable portable desktop package from a clean, verified source tree.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
s=$(git status --porcelain --untracked-files=normal) || exit 1
if [ -n "$s" ]; then
  echo 'Dirty source tree refused; commit or export a clean snapshot before packaging.' >&2
  exit 1
fi
commit=$(git rev-parse --verify HEAD) || exit 1
ELECTRON_DIST="${KEEPHARNESS_ELECTRON_DIST:-$ROOT/desktop/node_modules/electron/dist}"
python3 scripts/verify_electron.py "$ROOT" "$ELECTRON_DIST"
VERSION="$(python3 -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')"
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+([+-][0-9A-Za-z.]+)?$ ]] || { echo 'Invalid version.' >&2; exit 1; }
OUT="$ROOT/dist/keepharness-$VERSION-linux-x64"
if [ -e "$OUT" ] || [ -L "$OUT" ]; then
  echo "Output already exists: $OUT" >&2; exit 1
fi
mkdir -p "$OUT"
cp -a "$ELECTRON_DIST/." "$OUT/"
mv "$OUT/electron" "$OUT/keepharness-bin"
mv "$OUT/LICENSE" "$OUT/LICENSE.electron.txt"
rm -- "$OUT/version" "$OUT/resources/default_app.asar"
install -Dm644 LICENSE "$OUT/LICENSE"
printf '%s\n' "$VERSION" > "$OUT/VERSION"
APP="$(mktemp -d "${TMPDIR:-/tmp}/keepharness-asar-XXXXXX")"
trap 'chmod -R u+w "$APP"; rm -rf -- "$APP"' EXIT
for file in main.cjs policy.cjs splash.html build/icon.png build/splash.jpg; do
  install -Dm644 "desktop/$file" "$APP/$file"
done
printf '{"name":"keepharness","version":"%s","main":"main.cjs"}\n' "$VERSION" > "$APP/package.json"
node --input-type=module - "$APP" "$OUT" <<'JS'
import {createPackage} from './desktop/node_modules/@electron/asar/lib/asar.js';
import {flipFuses, FuseVersion, FuseV1Options as F} from './desktop/node_modules/@electron/fuses/dist/index.js';
const [app,out]=process.argv.slice(2);
await createPackage(app, `${out}/resources/app.asar`);
await flipFuses(`${out}/keepharness-bin`, {version:FuseVersion.V1,
  [F.RunAsNode]:false,[F.EnableNodeOptionsEnvironmentVariable]:false,[F.EnableNodeCliInspectArguments]:false,
  [F.EnableEmbeddedAsarIntegrityValidation]:true,[F.OnlyLoadAppFromAsar]:true});
JS
install -m755 desktop/linux/launcher.sh "$OUT/keepharness"
install -m755 desktop/linux/install-desktop-linux.sh "$OUT/install-desktop-linux.sh"
install -m644 desktop/linux/install_desktop_linux.py "$OUT/install_desktop_linux.py"
install -Dm644 desktop/linux/keepharness.desktop "$OUT/share/applications/keepharness.desktop"
for size in 16 24 32 48 64 128 256 512; do
  install -Dm644 "desktop/build/icons/${size}x${size}.png" "$OUT/share/icons/hicolor/${size}x${size}/apps/keepharness.png"
done
python3 - "$OUT" "$commit" "$VERSION" <<'PY'
import datetime, hashlib, json, pathlib, sys
out, commit, version = sys.argv[1:]
out = pathlib.Path(out)
manifest = {'product':'keepharness','version':version,'commit':commit,'dirty':False,
            'built_at': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            'electron': json.loads(pathlib.Path('desktop/package.json').read_text())['devDependencies']['electron']}
for name in ('keepharness-bin', 'resources/app.asar'):
    manifest[name.replace('/', '_').replace('-', '_') + '_sha256'] = hashlib.file_digest((out/name).open('rb'),'sha256').hexdigest()
(out/'build-manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
files = sorted(p for p in out.rglob('*') if p.is_file())
(out/'SHA256SUMS').write_text(''.join(hashlib.file_digest(p.open('rb'),'sha256').hexdigest()+'  '+p.relative_to(out).as_posix()+'\n' for p in files))
PY
echo "Linux desktop package created at $OUT"
