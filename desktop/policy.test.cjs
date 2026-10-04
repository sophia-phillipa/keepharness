const test = require('node:test');
const assert = require('node:assert/strict');
const {
  appOrigins,
  isAppUrl,
  permissionAllowed,
  externalUrl,
  windowOptions,
  splashOptions,
  backendEnv,
  processRunning,
  portOwnedByUser,
  LOCAL_COOKIE,
  localKeyPath,
  openTicket,
  sessionCookie,
  enrollmentLink,
  cookieValue,
  busyFromState,
  closeAllowed,
  closePrompt,
} = require('./policy.cjs');

test('only the local admin and harness origins are app URLs', () => {
  const origins = appOrigins([8094, 8095]);
  assert.equal(isAppUrl('http://127.0.0.1:8095/#x', origins), true);
  // RC-13: `localhost` may resolve away from loopback, and app origins get permissions.
  assert.equal(isAppUrl('http://localhost:8094/', origins), false);
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

test('the secret only signs a one-time open ticket, as keepharness open does', () => {
  assert.equal(localKeyPath('/home/owner'), '/home/owner/.local/share/keepharness/local.key');
  const secret = 'Abc_def-0123456789xyzXYZ';
  // Reference from control/local_access.py open_ticket(secret, now=1000) with this nonce.
  assert.equal(
    openTicket(secret + '\n', 1000, 'nonce_0123'),
    '1300.nonce_0123.0fd978c55fea1d69cf2ad6b60cb82fb9eebf84c090f1b7da89901be5182a8acc',
  );
  assert.ok(!openTicket(secret).includes(secret));
  for (const bad of ['', 'short', 'has space in it here', 'semi;colon-0123456789', null]) assert.equal(openTicket(bad), null);
});

test('the window keeps only the session token the admin issued', () => {
  // Matches control/local_access.py: PRODUCT.slug + "-local".
  assert.equal(LOCAL_COOKIE, 'keepharness-local');
  const token = 'Tok_en-0123456789abcdefABCDEF0123456789abcde';
  const answer = [
    'admin=other; HttpOnly; Path=/; SameSite=strict',
    `keepharness-local=${token}; HttpOnly; Max-Age=2592000; Path=/; SameSite=strict`,
  ];
  assert.deepEqual(sessionCookie(answer, 8095, 1000), {
    url: 'http://127.0.0.1:8095/',
    name: LOCAL_COOKIE,
    value: token,
    path: '/',
    httpOnly: true,
    sameSite: 'strict',
    expirationDate: 1000 + 2592000,
  });
  assert.equal(sessionCookie(['admin=x; Path=/'], 8095), null);
  assert.equal(sessionCookie(['keepharness-local=bad value; Path=/'], 8095), null);
  assert.equal(sessionCookie(null, 8095), null);
});

test('the window enrolls itself only through a harness enrollment link printed by the CLI', () => {
  const origin = 'http://127.0.0.1:8095';
  const printed = 'Open this single-use link in the owner\'s browser within 10 minutes:\nhttp://127.0.0.1:8095/approve-device?nonce=abc_DEF-123\n';
  assert.equal(enrollmentLink(printed, origin), 'http://127.0.0.1:8095/approve-device?nonce=abc_DEF-123');
  // The CLI prints the tailnet origin when sharing is on: the window does not show that origin.
  assert.equal(enrollmentLink('http://host.example.ts.net:8093/approve-device?nonce=x', origin), null);
  assert.equal(enrollmentLink('http://127.0.0.1:8095/elsewhere?nonce=x', origin), null);
  assert.equal(enrollmentLink('http://127.0.0.1:8095/approve-device', origin), null);
  assert.equal(enrollmentLink('http://u:p@127.0.0.1:8095/approve-device?nonce=x', origin), null);
  assert.equal(enrollmentLink('error: Could not update approval authority', origin), null);
  assert.equal(enrollmentLink('', origin), null);
});

test('the clipboard write and notifications are allowed on the app origins only', () => {
  const origins = appOrigins([8094, 8095]);
  for (const permission of ['clipboard-sanitized-write', 'notifications']) {
    assert.equal(permissionAllowed(permission, 'http://127.0.0.1:8095/', origins), true);
    assert.equal(permissionAllowed(permission, 'http://localhost:8094', origins), false);
    assert.equal(permissionAllowed(permission, 'https://example.com/', origins), false);
    assert.equal(permissionAllowed(permission, 'file:///splash.html', origins), false);
    assert.equal(permissionAllowed(permission, undefined, origins), false);
  }
  for (const permission of ['media', 'geolocation', 'clipboard-read', 'midi', 'openExternal'])
    assert.equal(permissionAllowed(permission, 'http://127.0.0.1:8095/', origins), false);
});

test('the admin cookie is read from the sign-in answer, and only when it is a token', () => {
  const token = 'Adm_in-0123456789abcdefABCDEF0123456789abcde';
  const answer = [`keepharness-local=other; Path=/`, `admin=${token}; HttpOnly; Path=/; SameSite=strict`];
  assert.equal(cookieValue(answer, 'admin'), token);
  assert.equal(cookieValue(answer, 'missing'), null);
  assert.equal(cookieValue(['admin=bad value; Path=/'], 'admin'), null);
  assert.equal(cookieValue(undefined, 'admin'), null);
});

test('busy is read from the admin state, and unknown when the answer cannot say', () => {
  assert.equal(busyFromState('{"status":{"busy":true}}'), true);
  assert.equal(busyFromState('{"status":{"busy":false}}'), false);
  for (const body of ['', 'not json', '{}', '{"status":{}}', '{"status":{"busy":"yes"}}', 'null']) assert.equal(busyFromState(body), null);
});

test('closing the window asks first only when the app owns a backend that may be working (D18)', async () => {
  const run = async (ownsBackend, working) => {
    const calls = { busy: 0, asked: [] };
    const allowed = await closeAllowed({
      ownsBackend,
      busy: async () => (calls.busy++, working),
      confirm: async (state) => (calls.asked.push(state), false),
    });
    return { allowed, ...calls };
  };
  // The service belongs to systemd (or someone else): the window is only a client and closes.
  assert.deepEqual(await run(false, true), { allowed: true, busy: 0, asked: [] });
  // Owned and idle: closes without asking.
  assert.deepEqual(await run(true, false), { allowed: true, busy: 1, asked: [] });
  // Owned and busy, or not known to be idle: asks, and the answer decides.
  assert.deepEqual(await run(true, true), { allowed: false, busy: 1, asked: [true] });
  assert.deepEqual(await run(true, null), { allowed: false, busy: 1, asked: [null] });
  assert.equal(await closeAllowed({ ownsBackend: true, busy: async () => true, confirm: async () => true }), true);
});

test('the close prompt keeps working by default and names what stops', () => {
  for (const working of [true, null]) {
    const prompt = closePrompt(working);
    assert.deepEqual(prompt.buttons, ['Keep working', 'Close and stop the work']);
    assert.match(prompt.message, /stops the service/i);
  }
  assert.match(closePrompt(true).detail, /running and queued tasks/);
  assert.match(closePrompt(null).detail, /could not confirm/i);
});
