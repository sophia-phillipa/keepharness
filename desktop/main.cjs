// KeepHarness desktop client: starts the local admin when it is not running,
// opens the harness (or the admin when no provider is set up yet) in its own
// window, keeps navigation inside the two local origins and stops the admin it
// started when the app quits.
const { app, BrowserWindow, dialog, session, shell } = require('electron');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
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

const TITLE = 'KeepHarness';
const project = path.resolve(__dirname, '..');
// build/ and splash.html sit next to this file, both in the repository and in the packaged app.
const icon = path.join(__dirname, 'build', 'icon.png');
const adminPort = Number(process.env.KEEPHARNESS_ADMIN_PORT || 8094);
const harnessPort = Number(process.env.KEEPHARNESS_PORT || 8095);
const adminUrl = `http://127.0.0.1:${adminPort}/`;
const harnessUrl = `http://127.0.0.1:${harnessPort}/`;
const origins = appOrigins([adminPort, harnessPort]);
let win = null,
  splash = null,
  backend = null,
  stderr = '';

app.setName(TITLE);

function reachable(url, timeout = 1500) {
  return new Promise((resolve) => {
    const request = http.get(url, { timeout }, (response) => {
      response.resume();
      resolve(response.statusCode > 0 && response.statusCode < 500);
    });
    request.on('timeout', () => request.destroy());
    request.on('error', () => resolve(false));
  });
}
// Linux only: the services behind the two ports must be this user's own processes, not
// whatever else answers on loopback. Without /proc/net/tcp (macOS, Windows) the check is skipped.
function foreignPort(ports) {
  if (!fs.existsSync('/proc/net/tcp') || typeof process.getuid !== 'function') return null;
  const tables = ['/proc/net/tcp', '/proc/net/tcp6'].map((file) => {
    try {
      return fs.readFileSync(file, 'utf8');
    } catch {
      return '';
    }
  });
  return ports.find((port) => !portOwnedByUser(tables, port, process.getuid())) ?? null;
}
// The app needs no camera, microphone, location, notifications or clipboard access.
function denyPermissions() {
  session.defaultSession.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  session.defaultSession.setPermissionCheckHandler(() => false);
}
async function waitFor(url, seconds) {
  for (let i = 0; i < seconds * 4; i++) {
    if (await reachable(url)) return true;
    if (backend && !processRunning(backend)) return false;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  return false;
}
function startAdmin() {
  const python = process.env.KEEPHARNESS_PYTHON || path.join(project, '.venv', 'bin', 'python');
  backend = spawn(python, ['-m', 'control', '--port', String(adminPort)], {
    cwd: project,
    env: backendEnv(process.env),
    stdio: ['ignore', 'ignore', 'pipe'],
  });
  backend.stderr.on('data', (chunk) => {
    stderr = (stderr + chunk).slice(-4000);
  });
  backend.on('error', (error) => {
    stderr += '\n' + error.message;
  });
}
// A fixed picture while the backend starts or is attached; it never navigates or opens windows.
function showSplash() {
  splash = new BrowserWindow(splashOptions(TITLE, icon));
  splash.once('ready-to-show', () => splash.show());
  splash.webContents.on('will-navigate', (event) => event.preventDefault());
  splash.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  splash.loadFile(path.join(__dirname, 'splash.html')).catch(() => splash.hide());
}
// Once the main window exists the splash closes. On a failure it is only hidden: closing the
// last window would quit the app (window-all-closed) before the error dialog could be shown.
function releaseSplash() {
  if (!splash || splash.isDestroyed()) return;
  if (win) splash.close();
  else splash.hide();
}

async function start() {
  denyPermissions();
  showSplash();
  if (!(await reachable(adminUrl))) {
    startAdmin();
    if (!(await waitFor(adminUrl, 40))) {
      releaseSplash();
      await dialog.showMessageBox({
        type: 'error',
        title: TITLE,
        message: 'The KeepHarness service did not start.',
        detail: stderr.slice(-2000) || 'Set KEEPHARNESS_PYTHON to a Python environment with KeepHarness installed.',
      });
      app.quit();
      return;
    }
  }
  // A configured admin starts the harness on its own; give it a moment.
  const target = (await waitFor(harnessUrl + 'v1/version', 15)) ? harnessUrl : adminUrl;
  const foreign = foreignPort(target === harnessUrl ? [adminPort, harnessPort] : [adminPort]);
  if (foreign !== null) {
    releaseSplash();
    await dialog.showMessageBox({
      type: 'error',
      title: TITLE,
      message: `The program on 127.0.0.1:${foreign} is not yours, so KeepHarness will not open it.`,
      detail:
        'Only a KeepHarness service started by your own account is loaded. Stop the other program, or set KEEPHARNESS_ADMIN_PORT and KEEPHARNESS_PORT to free ports.',
    });
    app.quit();
    return;
  }
  win = new BrowserWindow(windowOptions(TITLE, icon));
  win.once('ready-to-show', () => {
    win.show();
    releaseSplash();
  });
  win.webContents.on('will-navigate', (event, url) => {
    if (isAppUrl(url, origins)) return;
    event.preventDefault();
    const safe = externalUrl(url);
    if (safe) void shell.openExternal(safe);
  });
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (isAppUrl(url, origins)) void win.loadURL(url);
    else {
      const safe = externalUrl(url);
      if (safe) void shell.openExternal(safe);
    }
    return { action: 'deny' };
  });
  await win.loadURL(target);
}

if (!app.requestSingleInstanceLock()) app.quit();
else {
  app.on('second-instance', () => {
    if (!win) return;
    if (win.isMinimized()) win.restore();
    win.show();
    win.focus();
  });
  app.on('window-all-closed', () => app.quit());
  app.on('will-quit', () => {
    // Stop only the admin this app started; the admin stops its harness.
    if (processRunning(backend)) backend.kill('SIGTERM');
  });
  app.whenReady().then(start).catch(async (error) => {
    releaseSplash();
    await dialog.showMessageBox({ type: 'error', title: TITLE, message: String(error.message || error) });
    app.quit();
  });
}
