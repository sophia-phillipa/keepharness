// Navigation policy for the desktop window: only the local admin and harness
// origins load inside the app; everything else opens in the user's browser.
const { URL } = require('node:url');

function appOrigins(ports) {
  return ports.flatMap((port) => [`http://127.0.0.1:${port}`, `http://localhost:${port}`]);
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
module.exports = { appOrigins, isAppUrl, permissionAllowed, externalUrl, windowOptions, splashOptions, backendEnv, processRunning, portOwnedByUser };
