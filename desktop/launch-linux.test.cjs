// launch-linux.sh picks the Python environment the installer created, then the repository .venv.
const test = require('node:test');
const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const root = fs.mkdtempSync(path.join(os.tmpdir(), 'keepharness-launch-'));
test.after(() => fs.rmSync(root, { recursive: true, force: true }));

// A copy of the script in a fake checkout: product.py prints $FAKE_VENV (and fails when it is unset)
// and the fake electron prints the Python environment it was started with.
fs.mkdirSync(path.join(root, 'desktop/node_modules/electron/dist'), { recursive: true });
fs.mkdirSync(path.join(root, 'control'));
fs.copyFileSync(path.join(__dirname, 'launch-linux.sh'), path.join(root, 'desktop/launch-linux.sh'));
fs.writeFileSync(path.join(root, 'control/product.py'), 'import os\nprint(os.environ["FAKE_VENV"])\n');
fs.writeFileSync(path.join(root, 'desktop/node_modules/electron/dist/electron'), '#!/bin/sh\necho "$KEEPHARNESS_PYTHON"\n', { mode: 0o755 });

function launchedPython(env) {
  const clean = { PATH: process.env.PATH, ...env };
  return execFileSync('bash', [path.join(root, 'desktop/launch-linux.sh')], { env: clean, encoding: 'utf8' }).trim();
}
function installedVenv(name) {
  const venv = path.join(root, name);
  fs.mkdirSync(path.join(venv, 'bin'), { recursive: true });
  fs.writeFileSync(path.join(venv, 'bin/python'), '#!/bin/sh\n', { mode: 0o755 });
  return venv;
}

test('the environment made by install.sh is the default', () => {
  const venv = installedVenv('installed');
  assert.equal(launchedPython({ FAKE_VENV: venv }), path.join(venv, 'bin/python'));
});

test('an explicit KEEPHARNESS_PYTHON wins', () => {
  assert.equal(launchedPython({ FAKE_VENV: installedVenv('other'), KEEPHARNESS_PYTHON: '/custom/python' }), '/custom/python');
});

test('the repository .venv is the fallback when there is no installed environment', () => {
  const repoPython = path.join(root, '.venv/bin/python');
  assert.equal(launchedPython({ FAKE_VENV: path.join(root, 'missing') }), repoPython);
  assert.equal(launchedPython({}), repoPython); // product.py fails
});
