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
    return parsed.hostname === '127.0.0.1' && origins.includes(parsed.origin) && !parsed.username && !parsed.password;
  } catch {
    return false;
  }
}
// Persist paths only, never a foreign origin or a one-time authentication link.
function restoredRoute(route, base, origins) {
  if (typeof route !== 'string' || route.length > 2048 || !route.startsWith('/') ||
      route.startsWith('//') || /[\\\x00-\x1f\x7f]/.test(route)) return null;
  try {
    const url = new URL(route, base);
    if (!isAppUrl(url.href, origins) || url.origin !== new URL(base).origin ||
        /^\/(?:open|approve-device)(?:\/|$)/.test(url.pathname) ||
        [...url.searchParams.keys()].some(key => /ticket|token|secret|password|cookie/i.test(key))) return null;
    return url.href;
  } catch { return null; }
}
function appRoute(url, origins) {
  if (!isAppUrl(url, origins)) return null;
  const parsed = new URL(url);
  const route = parsed.pathname + parsed.search + parsed.hash;
  return restoredRoute(route, parsed.origin, origins) ? route : null;
}
function windowTitle(title, url, origins) {
  if (!isAppUrl(url, origins) || typeof title !== 'string') return 'KeepHarness';
  return [...title.replace(/[\p{Cc}\p{Cf}\p{Zl}\p{Zp}]/gu, '').trim()].slice(0,160).join('') || 'KeepHarness';
}
function downloadName(name) {
  return String(name || '').replace(/[\\/:*?"<>|\x00-\x1f\x7f-\x9f]/g, '_')
    .replace(/^[. ]+|[. ]+$/g, '').slice(0,180) || 'download';
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
    if (parsed.username || parsed.password || parsed.hostname === '[::1]') return null;
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
  spellcheck: false,
};
const BACKGROUND = '#141414';
function windowOptions(title, icon) {
  return {
    width: 1440,
    height: 900,
    minWidth: 960,
    minHeight: 640,
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
// Whether the admin reports the harness as running: true, false, or null when it cannot say.
function runningFromState(body) {
  try {
    const running = JSON.parse(body)?.status?.running;
    return typeof running === 'boolean' ? running : null;
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
// Runtime identity is explicit: a responding web server alone is not a harness.
function productAllowed(body) {
  try { return JSON.parse(body)?.product === 'keepharness'; } catch { return false; }
}
function runtimePort(override, body) {
  let value = override;
  if (value === undefined) {
    try { value = JSON.parse(body)?.port; } catch { return 8095; }
  }
  if (!['number', 'string'].includes(typeof value) || !/^\d+$/.test(String(value))) return 8095;
  const port = Number(value);
  return Number.isInteger(port) && port > 0 && port <= 65535 ? port : 8095;
}
// Preserve partly visible bounds on their greatest-overlap display; removed displays use defaults.
function clampBounds(state, areas) {
  const valid = state && ['x', 'y', 'width', 'height'].every(key => Number.isFinite(state[key])) && state.width > 0 && state.height > 0;
  let area = null, greatestOverlap = 0;
  if (valid) for (const candidate of areas) {
    const width = Math.max(0, Math.min(state.x + state.width, candidate.x + candidate.width) - Math.max(state.x, candidate.x));
    const height = Math.max(0, Math.min(state.y + state.height, candidate.y + candidate.height) - Math.max(state.y, candidate.y));
    const overlap = width * height;
    if (overlap > greatestOverlap) { area = candidate; greatestOverlap = overlap; }
  }
  const target = area || areas[0] || {x:0, y:0, width:1440, height:900};
  const width = Math.min(target.width, Math.max(960, area ? state.width : 1440));
  const height = Math.min(target.height, Math.max(640, area ? state.height : 900));
  return {
    x: area ? Math.max(target.x, Math.min(state.x, target.x + target.width - width)) : target.x + Math.floor((target.width - width) / 2),
    y: area ? Math.max(target.y, Math.min(state.y, target.y + target.height - height)) : target.y + Math.floor((target.height - height) / 2),
    width, height, maximized: !!area && state.maximized === true,
  };
}
function redact(value, secrets = []) {
  let text = String(value);
  for (const secret of secrets) if (secret) text = text.split(secret).join('[REDACTED]');
  return text
    .replace(/((?:set-cookie|cookie|authorization|x-api-key)["']?\s*:\s*)[^\r\n]+/gi, '$1[REDACTED]')
    .replace(/(\badmin\s*=\s*)(?:"[^"\r\n]*"|'[^'\r\n]*'|[^\s&;,}]+)/gi, '$1[REDACTED]')
    .replace(/((?:["']?)(?<![A-Za-z0-9])(?:secret|ticket|cookie|keepharness-local|harness_session|api[_-]key|token|password)(?:["']?)\s*[:=]\s*)(?:"[^"\r\n]*"|'[^'\r\n]*'|[^\s&;,}]+)/gi, '$1[REDACTED]');
}
const LOG_LIMIT = 1024 * 1024;
// Return a bounded UTF-8 record and rotation decision; disk writes stay in the runtime.
function logChunk(size, value, timestamp = new Date().toISOString()) {
  const prefix = timestamp + ' ';
  const buffer = Buffer.from(redact(value));
  const budget = LOG_LIMIT - Buffer.byteLength(prefix) - 1;
  let end = Math.min(buffer.length, budget);
  while (end < buffer.length && end > 0 && (buffer[end] & 0xc0) === 0x80) end--;
  const text = prefix + buffer.subarray(0, end).toString('utf8') + '\n';
  return { text, rotate: size + Buffer.byteLength(text) > LOG_LIMIT };
}
module.exports = {
  productAllowed, runtimePort, clampBounds, redact, logChunk, LOG_LIMIT,
  restoredRoute, appRoute, windowTitle, downloadName,
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
  runningFromState,
  closeAllowed,
  closePrompt,
};
