const test = require('node:test');
const assert = require('node:assert/strict');
const { appOrigins, isAppUrl, externalUrl, windowOptions, processRunning } = require('./policy.cjs');

test('only the local admin and harness origins are app URLs', () => {
  const origins = appOrigins([8094, 8095]);
  assert.equal(isAppUrl('http://127.0.0.1:8095/#x', origins), true);
  assert.equal(isAppUrl('http://localhost:8094/', origins), true);
  assert.equal(isAppUrl('http://127.0.0.1:9999/', origins), false);
  assert.equal(isAppUrl('https://127.0.0.1:8095/', origins), false);
  assert.equal(isAppUrl('http://user:pass@127.0.0.1:8095/', origins), false);
  assert.equal(isAppUrl('not a url', origins), false);
});

test('external URLs are limited to web and mail links without credentials', () => {
  assert.equal(externalUrl('https://example.com/a'), 'https://example.com/a');
  assert.equal(externalUrl('mailto:a@example.com'), 'mailto:a@example.com');
  assert.equal(externalUrl('file:///etc/passwd'), null);
  assert.equal(externalUrl('javascript:alert(1)'), null);
  assert.equal(externalUrl('https://u:p@example.com'), null);
});

test('the window is sandboxed without Node in the page', () => {
  const prefs = windowOptions('KeepHarness').webPreferences;
  assert.equal(prefs.nodeIntegration, false);
  assert.equal(prefs.contextIsolation, true);
  assert.equal(prefs.sandbox, true);
  assert.equal(prefs.webviewTag, false);
});

test('processRunning reflects exit state', () => {
  assert.equal(processRunning(null), false);
  assert.equal(processRunning({ exitCode: null, signalCode: null }), true);
  assert.equal(processRunning({ exitCode: 0, signalCode: null }), false);
});
