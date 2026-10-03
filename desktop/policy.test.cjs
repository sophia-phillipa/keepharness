const test = require('node:test');
const assert = require('node:assert/strict');
const {
  appOrigins,
  isAppUrl,
  externalUrl,
  windowOptions,
  splashOptions,
  backendEnv,
  processRunning,
  portOwnedByUser,
} = require('./policy.cjs');

test('only the local admin and harness origins are app URLs', () => {
  const origins = appOrigins([8094, 8095]);
  assert.equal(isAppUrl('http://127.0.0.1:8095/#x', origins), true);
  assert.equal(isAppUrl('http://localhost:8094/', origins), true);
  assert.equal(isAppUrl('http://127.0.0.1:9999/', origins), false);
  assert.equal(isAppUrl('https://127.0.0.1:8095/', origins), false);
  assert.equal(isAppUrl('http://user:pass@127.0.0.1:8095/', origins), false);
  assert.equal(isAppUrl('not a url', origins), false);
});

test('external URLs are limited to web links without credentials', () => {
  assert.equal(externalUrl('https://example.com/a'), 'https://example.com/a');
  assert.equal(externalUrl('http://example.com/a'), 'http://example.com/a');
  assert.equal(externalUrl('mailto:a@example.com'), null);
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

test('the splash is a fixed frameless picture, as locked down as the main window, and both carry the icon', () => {
  const splash = splashOptions('KeepHarness', '/app/build/icon.png');
  assert.deepEqual([splash.width, splash.height, splash.center, splash.resizable], [800, 500, true, false]);
  assert.equal(splash.frame, false);
  assert.deepEqual(splash.webPreferences, windowOptions('KeepHarness').webPreferences);
  assert.equal(splash.icon, '/app/build/icon.png');
  assert.equal(windowOptions('KeepHarness', '/app/build/icon.png').icon, '/app/build/icon.png');
});

test('the backend gets the cache folder of the user back from the launcher', () => {
  const launched = { PATH: '/bin', XDG_CACHE_HOME: '/private', KEEPHARNESS_HOST_XDG_CACHE_HOME: '/home/me/.cache' };
  assert.deepEqual(backendEnv(launched), { PATH: '/bin', XDG_CACHE_HOME: '/home/me/.cache', PYTHONUNBUFFERED: '1' });
  // The user had none: the backend gets none either.
  assert.deepEqual(backendEnv({ ...launched, KEEPHARNESS_HOST_XDG_CACHE_HOME: '' }), { PATH: '/bin', PYTHONUNBUFFERED: '1' });
  // Not started by the launcher: nothing to restore.
  assert.deepEqual(backendEnv({ XDG_CACHE_HOME: '/x' }), { XDG_CACHE_HOME: '/x', PYTHONUNBUFFERED: '1' });
});

test('processRunning reflects exit state', () => {
  assert.equal(processRunning(null), false);
  assert.equal(processRunning({ exitCode: null, signalCode: null }), true);
  assert.equal(processRunning({ exitCode: 0, signalCode: null }), false);
});

// One /proc/net/tcp row: local address, state and the owning uid are the columns that matter.
const HEADER = '  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n';
const row = (local, state, uid) =>
  `   0: ${local} 00000000:0000 ${state} 00000000:00000000 00:00000000 00000000  ${String(uid).padStart(4)}        0 12345 1 0000000000000000 100 0 0 10 0\n`;
const LOOPBACK_8094 = '0100007F:1F9E';
const ME = 1000;
const OTHER = 1001;

test('a loopback listener owned by the current user is trusted', () => {
  assert.equal(portOwnedByUser([HEADER + row(LOOPBACK_8094, '0A', ME)], 8094, ME), true);
  assert.equal(portOwnedByUser([HEADER + row(LOOPBACK_8094, '0A', OTHER)], 8094, ME), false);
});

test('a listener that cannot be confirmed is not trusted', () => {
  assert.equal(portOwnedByUser([HEADER], 8094, ME), false);
  assert.equal(portOwnedByUser(['', null, undefined], 8094, ME), false);
  assert.equal(portOwnedByUser(['not a table\n'], 8094, ME), false);
  // Another port, another bind address and a connection that is not listening do not count.
  assert.equal(portOwnedByUser([HEADER + row('0100007F:1F9F', '0A', ME)], 8094, ME), false);
  assert.equal(portOwnedByUser([HEADER + row('0501A8C0:1F9E', '0A', ME)], 8094, ME), false);
  assert.equal(portOwnedByUser([HEADER + row(LOOPBACK_8094, '01', ME)], 8094, ME), false);
});

test('any foreign listener that can answer on 127.0.0.1 makes the port untrusted', () => {
  const mine = row(LOOPBACK_8094, '0A', ME);
  assert.equal(portOwnedByUser([HEADER + mine + row('00000000:1F9E', '0A', OTHER)], 8094, ME), false);
  assert.equal(portOwnedByUser([HEADER + mine, HEADER + row('00000000000000000000000000000000:1F9E', '0A', OTHER)], 8094, ME), false);
  assert.equal(portOwnedByUser([HEADER + mine, HEADER + row('0000000000000000FFFF00000100007F:1F9E', '0A', OTHER)], 8094, ME), false);
});

test('a dual-stack listener owned by the current user is trusted, an IPv6-only one is ignored', () => {
  assert.equal(portOwnedByUser([HEADER, HEADER + row('00000000000000000000000000000000:1F9E', '0A', ME)], 8094, ME), true);
  assert.equal(portOwnedByUser([HEADER + row(LOOPBACK_8094, '0A', ME), HEADER + row('00000000000000000000000001000000:1F9E', '0A', OTHER)], 8094, ME), true);
});

test('the port is matched as a zero-padded hexadecimal number', () => {
  assert.equal(portOwnedByUser([HEADER + row('0100007F:0050', '0A', ME)], 80, ME), true);
  assert.equal(portOwnedByUser([HEADER + row('0100007F:0050', '0A', ME)], 8000, ME), false);
});
