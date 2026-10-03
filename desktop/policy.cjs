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
function windowOptions(title) {
  return {
    width: 1440,
    height: 900,
    minWidth: 720,
    minHeight: 500,
    show: false,
    title,
    backgroundColor: '#141414',
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
      webSecurity: true,
      allowRunningInsecureContent: false,
      webviewTag: false,
    },
  };
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
module.exports = { appOrigins, isAppUrl, externalUrl, windowOptions, processRunning, portOwnedByUser };
