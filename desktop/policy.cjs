// Navigation policy for the desktop window: only the local admin and harness
// origins load inside the app; everything else opens in the user's browser.
const crypto = require('node:crypto');
const path = require('node:path');
const { URL } = require('node:url');

// Only 127.0.0.1: these origins get the clipboard and notification permissions, and a
// `localhost` name can resolve elsewhere than loopback.
function appOrigins(ports) {
  return ports.map((port) => `http://127.0.0.1:${port}`);
}
function isAppUrl(url, origins) {
  try {
    const parsed = new URL(url);
    return origins.includes(parsed.origin) && !parsed.username && !parsed.password;
  } catch {
    return false;
  }
}
// The only permissions the app pages get, and only on the app origins: writing the clipboard
// (Copy on answers and code) and OS notifications (a run needs you, failed or finished).
const APP_PERMISSIONS = new Set(['clipboard-sanitized-write', 'notifications']);
function permissionAllowed(permission, url, origins) {
  return APP_PERMISSIONS.has(permission) && isAppUrl(url, origins);
}
function externalUrl(url) {
  try {
    const parsed = new URL(url);
    if (!['https:', 'http:'].includes(parsed.protocol)) return null;
    if (parsed.username || parsed.password) return null;
    return parsed.href;
  } catch {
    return null;
  }
}
// Every window of the app: sandboxed, no Node in the page.
const PAGE_PREFERENCES = {
  nodeIntegration: false,
  contextIsolation: true,
  sandbox: true,
  webSecurity: true,
  allowRunningInsecureContent: false,
  webviewTag: false,
};
const BACKGROUND = '#141414';
function windowOptions(title, icon) {
  return {
    width: 1440,
    height: 900,
    minWidth: 720,
    minHeight: 500,
    show: false,
    title,
    icon,
    backgroundColor: BACKGROUND,
    webPreferences: { ...PAGE_PREFERENCES },
  };
}
// The splash shown while the backend starts or is attached: a fixed, frameless picture.
function splashOptions(title, icon) {
  return {
    width: 800,
    height: 500,
    center: true,
    frame: false,
    resizable: false,
    minimizable: false,
    maximizable: false,
    fullscreenable: false,
    show: false,
    title,
    icon,
    backgroundColor: BACKGROUND,
    webPreferences: { ...PAGE_PREFERENCES },
  };
}
// The packaged launcher gives Electron a private XDG cache and keeps the user's own in
// KEEPHARNESS_HOST_XDG_CACHE_HOME (empty when unset); the backend and the CLIs it starts get it back.
function backendEnv(env) {
  const { KEEPHARNESS_HOST_XDG_CACHE_HOME: host, ...rest } = env;
  const result = { ...rest, PYTHONUNBUFFERED: '1' };
  if (host === undefined) return result;
  if (host) result.XDG_CACHE_HOME = host;
  else delete result.XDG_CACHE_HOME;
  return result;
}
function processRunning(child) {
  return !!child && child.exitCode === null && child.signalCode === null;
}
// Linux: who is listening where 127.0.0.1:<port> is served? `tables` are the texts of
// /proc/net/tcp and /proc/net/tcp6 (rows: sl local_address rem_address st ... uid ...).
// True only when a listener exists and every one that can answer on 127.0.0.1 belongs to `uid`.
const LISTEN_STATE = '0A';
const LOOPBACK_HOSTS = new Set([
  '0100007F', // 127.0.0.1
  '00000000', // 0.0.0.0
  '00000000000000000000000000000000', // :: (dual stack)
  '0000000000000000FFFF00000100007F', // ::ffff:127.0.0.1
]);
function portOwnedByUser(tables, port, uid) {
  const wanted = port.toString(16).toUpperCase().padStart(4, '0');
  const owners = [];
  for (const table of tables) {
    for (const line of String(table || '').split('\n')) {
      const columns = line.trim().split(/\s+/);
      const [host, hexPort] = (columns[1] || '').toUpperCase().split(':');
      if (columns[3] === LISTEN_STATE && hexPort === wanted && LOOPBACK_HOSTS.has(host)) owners.push(Number(columns[7]));
    }
  }
  return owners.length > 0 && owners.every((owner) => owner === uid);
}
// The local owner holds a per-install secret (decision D09) in the admin's 0600 state file. It
// never becomes a cookie: the main process signs a one-time `/open` ticket with it, exactly as
// `keepharness open` does (control/local_access.py open_ticket), and the admin answers with a
// session token of the window's own. Cookies ignore the port, so that one session cookie on
// 127.0.0.1 reaches both the admin and the harness.
const LOCAL_COOKIE = 'keepharness-local';
const OPEN_SECONDS = 300;
const TOKEN = /^[A-Za-z0-9_-]{16,256}$/;
function localKeyPath(home) {
  return path.join(home, '.local', 'share', 'keepharness', 'local.key');
}
function openTicket(secret, nowSeconds = Date.now() / 1000, nonce = crypto.randomBytes(16).toString('base64url')) {
  const key = String(secret || '').trim();
  if (!TOKEN.test(key)) return null;
  const expires = Math.floor(nowSeconds) + OPEN_SECONDS;
  const signature = crypto.createHmac('sha256', key).update(`open:${expires}.${nonce}`).digest('hex');
  return `${expires}.${nonce}.${signature}`;
}
// The session cookie from the admin's `/open` answer (its Set-Cookie headers), for the window's
// cookie jar; null when the answer holds none.
function sessionCookie(setCookies, port, nowSeconds = Date.now() / 1000) {
  for (const header of [].concat(setCookies || [])) {
    const [pair, ...attributes] = String(header).split(';');
    const at = pair.indexOf('=');
    if (pair.slice(0, at).trim() !== LOCAL_COOKIE) continue;
    const value = pair.slice(at + 1).trim();
    if (!TOKEN.test(value)) return null;
    const maxAge = attributes.map((a) => a.trim().match(/^max-age=(\d+)$/i)).find(Boolean);
    const cookie = { url: `http://127.0.0.1:${port}/`, name: LOCAL_COOKIE, value, path: '/', httpOnly: true, sameSite: 'strict' };
    return maxAge ? { ...cookie, expirationDate: Math.floor(nowSeconds) + Number(maxAge[1]) } : cookie;
  }
  return null;
}
// The value of the cookie `name` in a Set-Cookie answer, or null when it is missing or not a token.
function cookieValue(setCookies, name) {
  for (const header of [].concat(setCookies || [])) {
    const [pair] = String(header).split(';');
    const at = pair.indexOf('=');
    if (pair.slice(0, at).trim() !== name) continue;
    const value = pair.slice(at + 1).trim();
    return TOKEN.test(value) ? value : null;
  }
  return null;
}
// `status.busy` of the admin's /api/state (queued or running work): true, false, or null when
// the body cannot say.
function busyFromState(body) {
  try {
    const busy = JSON.parse(body)?.status?.busy;
    return typeof busy === 'boolean' ? busy : null;
  } catch {
    return null;
  }
}
// Closing the window is only a client leaving (D18) unless this app started the admin: quitting
// then stops the service and its work. `busy()` answers true, false or null (unknown); only a
// known-idle service closes without asking, and `confirm(busy)` says whether the user agreed.
async function closeAllowed({ ownsBackend, busy, confirm }) {
  if (!ownsBackend) return true;
  const working = await busy();
  if (working === false) return true;
  return confirm(working);
}
function closePrompt(working) {
  return {
    message: 'Closing KeepHarness stops the service.',
    detail:
      working === true
        ? 'Tasks are running or queued; the running and queued tasks stop with it.'
        : 'KeepHarness could not confirm that nothing is running.',
    buttons: ['Keep working', 'Close and stop the work'],
  };
}
// `keepharness approve-device --owner local --yes` prints the one-time enrollment link last. The
// window loads it only when it is an enrollment link on the harness origin the window shows.
function enrollmentLink(output, harnessOrigin) {
  const line = String(output || '').trim().split('\n').pop().trim();
  try {
    const url = new URL(line);
    const valid = url.origin === harnessOrigin && url.pathname === '/approve-device' && url.searchParams.get('nonce') && !url.username && !url.password;
    return valid ? url.href : null;
  } catch {
    return null;
  }
}
module.exports = {
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
};
