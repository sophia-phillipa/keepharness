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
module.exports = { appOrigins, isAppUrl, externalUrl, windowOptions, processRunning };
