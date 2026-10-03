// The packaged launcher and installer, run against a fake package and a throwaway HOME.
const test = require('node:test');
const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const root = fs.mkdtempSync(path.join(os.tmpdir(), 'keepharness-package-'));
test.after(() => fs.rmSync(root, { recursive: true, force: true }));

// A package folder as scripts/package-desktop-linux.sh lays it out, with a fake keepharness-bin
// that reports the Python environment, the cache folders and the arguments it was started with.
function fakePackage(name, version) {
  const dir = path.join(root, name);
  const copy = (from, to, mode) => {
    fs.mkdirSync(path.dirname(path.join(dir, to)), { recursive: true });
    fs.copyFileSync(path.join(__dirname, 'linux', from), path.join(dir, to));
    if (mode) fs.chmodSync(path.join(dir, to), mode);
  };
  copy('launcher.sh', 'keepharness', 0o755);
  copy('install-desktop-linux.sh', 'install-desktop-linux.sh', 0o755);
  copy('keepharness.desktop', 'share/applications/keepharness.desktop');
  fs.mkdirSync(path.join(dir, 'share/icons/hicolor/256x256/apps'), { recursive: true });
  fs.writeFileSync(path.join(dir, 'share/icons/hicolor/256x256/apps/keepharness.png'), '');
  fs.writeFileSync(path.join(dir, 'VERSION'), version + '\n');
  fs.writeFileSync(
    path.join(dir, 'keepharness-bin'),
    '#!/bin/sh\nprintf "%s|%s|%s|%s\\n" "$KEEPHARNESS_PYTHON" "$XDG_CACHE_HOME" "$KEEPHARNESS_HOST_XDG_CACHE_HOME" "$*"\n',
    { mode: 0o755 },
  );
  return dir;
}
function newHome(name) {
  const dir = path.join(root, name);
  fs.mkdirSync(dir);
  return dir;
}
function run(file, args, env) {
  return execFileSync(file, args, { env: { PATH: process.env.PATH, ...env }, encoding: 'utf8' }).trim();
}
function installedVenv(home) {
  const python = path.join(home, '.local/share/keepharness/venv/bin/python');
  fs.mkdirSync(path.dirname(python), { recursive: true });
  fs.writeFileSync(python, '#!/bin/sh\n', { mode: 0o755 });
  return python;
}

test('the launcher uses the environment install.sh made, a private cache and forwards the arguments', () => {
  const home = newHome('launch-home');
  const python = installedVenv(home);
  const launcher = path.join(fakePackage('launch', '1.0.0'), 'keepharness');
  const out = run(launcher, ['--flag', 'x'], { HOME: home, XDG_CACHE_HOME: '/host/cache' });
  const cache = path.join(home, '.config/keepharness/electron/cache');
  assert.equal(out, `${python}|${cache}|/host/cache|--flag x`);
  assert.equal(fs.statSync(cache).mode & 0o777, 0o700);
});

test('KEEPHARNESS_PYTHON wins, KEEPHARNESS_VENV moves the search, and no environment leaves it unset', () => {
  const home = newHome('python-home');
  const launcher = path.join(fakePackage('python', '1.0.0'), 'keepharness');
  const first = (out) => out.split('|')[0];
  assert.equal(first(run(launcher, [], { HOME: home })), '');
  installedVenv(home);
  assert.equal(first(run(launcher, [], { HOME: home, KEEPHARNESS_PYTHON: '/custom/python' })), '/custom/python');
  const elsewhere = path.join(root, 'elsewhere');
  fs.mkdirSync(path.join(elsewhere, 'bin'), { recursive: true });
  fs.writeFileSync(path.join(elsewhere, 'bin/python'), '#!/bin/sh\n', { mode: 0o755 });
  assert.equal(first(run(launcher, [], { HOME: home, KEEPHARNESS_VENV: elsewhere })), path.join(elsewhere, 'bin/python'));
});

test('the installer copies the package, writes the menu entry and keeps the previous one', () => {
  const home = newHome('install-home');
  const entry = path.join(home, '.local/share/applications/keepharness.desktop');
  const first = path.join(home, '.local/opt/keepharness-1.0.0');
  run(path.join(fakePackage('first', '1.0.0'), 'install-desktop-linux.sh'), [], { HOME: home });
  assert.ok(fs.existsSync(path.join(first, 'keepharness-bin')));
  const text = fs.readFileSync(entry, 'utf8');
  assert.match(text, new RegExp(`^Exec="${first}/keepharness"$`, 'm'));
  assert.match(text, new RegExp(`^Icon=${first}/share/icons/hicolor/256x256/apps/keepharness\\.png$`, 'm'));
  assert.match(text, /^Name=KeepHarness$/m);
  assert.ok(!fs.existsSync(path.join(first, 'previous.desktop')));

  const second = path.join(home, '.local/opt/keepharness-1.1.0');
  run(path.join(fakePackage('second', '1.1.0'), 'install-desktop-linux.sh'), [], { HOME: home });
  assert.equal(fs.readFileSync(path.join(second, 'previous.desktop'), 'utf8'), text);
  assert.match(fs.readFileSync(entry, 'utf8'), new RegExp(`^Exec="${second}/keepharness"$`, 'm'));
  assert.ok(fs.existsSync(first), 'the older version stays installed');
});

test('the installer never overwrites a folder of the same version', () => {
  const home = newHome('twice-home');
  const installer = path.join(fakePackage('twice', '2.0.0'), 'install-desktop-linux.sh');
  const target = path.join(home, '.local/opt/keepharness-2.0.0');
  run(installer, [], { HOME: home });
  fs.writeFileSync(path.join(target, 'marker'), 'mine');
  assert.throws(() => run(installer, [], { HOME: home }), (error) => /already installed/.test(error.stderr));
  assert.equal(fs.readFileSync(path.join(target, 'marker'), 'utf8'), 'mine');
  assert.deepEqual(fs.readdirSync(path.join(home, '.local/opt')), ['keepharness-2.0.0'], 'no half-installed leftovers');
});
