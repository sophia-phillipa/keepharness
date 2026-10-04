// KeepHarness desktop client: starts the local admin when it is not running,
// opens the harness (or the admin when no provider is set up yet) in its own
// window, keeps navigation inside the two local origins and stops the admin it
// started when the app quits. The window is the owner's: it signs itself in with a session
// minted from the per-install secret and enrolls itself for approvals (decisions D09 and D13).
const { app, BrowserWindow, dialog, session, shell, Menu, screen } = require('electron');
const { spawn, spawnSync } = require('node:child_process');
const fs = require('node:fs');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const {
  productAllowed, runtimePort, clampBounds, redact, logChunk,
  appOrigins,
  isAppUrl,
  permissionAllowed,
  externalUrl,
  windowOptions,
  splashOptions,
  backendEnv,
  processRunning,
  portOwnedByUser,
  localKeyPath,
  openTicket,
  sessionCookie,
  enrollmentLink,
  cookieValue,
  busyFromState,
  closeAllowed,
  closePrompt,
} = require('./policy.cjs');

const TITLE = 'KeepHarness';
const project = path.resolve(__dirname, '..');
// build/ and splash.html sit next to this file, both in the repository and in the packaged app.
const icon = path.join(__dirname, 'build', 'icon.png');
const adminPort = Number(process.env.KEEPHARNESS_ADMIN_PORT || 8094);
const harnessPort = runtimePort(process.env.KEEPHARNESS_PORT, readText(path.join(os.homedir(), '.local/share/keepharness/runtime.json')));
const adminUrl = `http://127.0.0.1:${adminPort}/`;
const harnessUrl = `http://127.0.0.1:${harnessPort}/`;
const origins = appOrigins([adminPort, harnessPort]);
const python = process.env.KEEPHARNESS_PYTHON || path.join(project, '.venv', 'bin', 'python');
let win = null,
  splash = null,
  adminWindow = null,
  starting = true,
  quitting = false,
  backendReady = false,
  recoveryPending = false,
  harnessVerified = false,
  backend = null,
  adminCookie = null,
  asking = false,
  closeConfirmed = false,
  stderr = '';

app.setName(TITLE);
const knownSecrets = new Set();
function readText(file) {
  try { return fs.readFileSync(file, 'utf8'); } catch { return ''; }
}
function safeText(value) { return redact(value, [...knownSecrets]); }
function log(value) {
  try {
    const file = path.join(app.getPath('userData'), 'logs', 'main.log');
    fs.mkdirSync(path.dirname(file), { recursive: true });
    const size = fs.existsSync(file) ? fs.statSync(file).size : 0;
    const chunk = logChunk(size, safeText(value));
    if (chunk.rotate && fs.existsSync(file)) fs.renameSync(file, file + '.1');
    fs.appendFileSync(file, chunk.text, { mode: 0o600 });
  } catch { /* Logging must not prevent recovery. */ }
}
function quit() {
  closeConfirmed = true;
  quitting = true;
  app.quit();
}
async function pythonReady() {
  // spawnSync resolves PATH names and reports missing/non-executable files without a shell.
  let result;
  try { result = spawnSync(python, ['--version'], { timeout: 5000, windowsHide: true, env: backendEnv(process.env) }); } catch { /* Same actionable hint. */ }
  if (result && !result.error && result.status === 0) return true;
  releaseSplash();
  await dialog.showMessageBox({ type: 'error', title: TITLE, message: 'Python could not start.', detail: 'Run install.sh, or set KEEPHARNESS_PYTHON' });
  quit();
  return false;
}
function versionBody(url) {
  return new Promise(resolve => {
    const request = http.get(url, {timeout:1500}, response => {
      let body = '';
      response.setEncoding('utf8');
      response.on('data', chunk => {
        body += chunk;
        if (body.length > 65536) { resolve(''); request.destroy(); }
      });
      response.on('end', () => resolve(response.statusCode === 200 ? body : ''));
      response.on('error', () => resolve(''));
    });
    request.on('timeout', () => request.destroy());
    request.on('error', () => resolve(''));
  });
}
async function verifyProduct(target) {
  if (new URL(target).origin !== new URL(harnessUrl).origin || harnessVerified) return true;
  if (foreignPort([harnessPort]) === null && productAllowed(await versionBody(harnessUrl + 'v1/version'))) { harnessVerified = true; return true; }
  await dialog.showMessageBox({type:'error', title:TITLE, message:'This is not a KeepHarness service.', detail:'The /v1/version product must be keepharness.'});
  quit();
  return false;
}
function saveWindowState(window) {
  try {
    const file = path.join(app.getPath('userData'), 'window-state.json');
    fs.mkdirSync(path.dirname(file), {recursive:true});
    fs.writeFileSync(file + '.tmp', JSON.stringify({...window.getNormalBounds(), maximized:window.isMaximized()}), {mode:0o600});
    fs.renameSync(file + '.tmp', file);
  } catch (error) { log(error.message); }
}
function restoredBounds() {
  let state;
  try { state = JSON.parse(readText(path.join(app.getPath('userData'), 'window-state.json'))); } catch { /* Defaults. */ }
  const primary = screen.getPrimaryDisplay().workArea;
  return clampBounds(state, [primary, ...screen.getAllDisplays().map(display => display.workArea)]);
}
function installMenu() {
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    {label:TITLE, submenu:[{role:'about'}, {label:'Quit', accelerator:'CmdOrCtrl+Q', click:() => app.quit()}]},
    {label:'Edit', submenu:['undo','redo','cut','copy','paste','selectAll'].map(role => ({role}))},
    {label:'View', submenu:[{role:'resetZoom'}, {role:'zoomIn'}, {role:'zoomOut'}, {role:'togglefullscreen'}, ...(!app.isPackaged ? [{role:'toggleDevTools'}] : [])]},
  ]));
}
// All BrowserWindows, including the splash, share security and lifecycle policies.
function createWindow(kind) {
  const isSplash = kind === 'splash';
  const state = kind === 'main' ? restoredBounds() : null;
  const window = new BrowserWindow(isSplash ? splashOptions(TITLE, icon) : {...windowOptions(TITLE, icon), ...state});
  if (state?.maximized) window.maximize();
  const show = () => {
    window.removeListener('ready-to-show', show);
    window.webContents.removeListener('did-finish-load', show);
    if (!quitting && !window.isDestroyed()) window.show();
  };
  window.once('ready-to-show', show);
  window.webContents.once('did-finish-load', show);
  const navigate = (event, url) => {
    if (!isSplash && isAppUrl(url, origins)) {
      if (new URL(url).origin === new URL(harnessUrl).origin && !harnessVerified) {
        event.preventDefault();
        void verifyProduct(url).then(allowed => { if (allowed && !quitting && !window.isDestroyed()) return window.loadURL(url); }).catch(error => log(error.message));
      }
      return;
    }
    event.preventDefault();
    const safe = !isSplash && externalUrl(url);
    if (safe) void shell.openExternal(safe).catch(error => log(error.message));
  };
  window.webContents.on('will-navigate', navigate);
  window.webContents.on('will-redirect', navigate);
  window.webContents.on('will-attach-webview', event => event.preventDefault());
  window.webContents.setWindowOpenHandler(({url}) => {
    if (isSplash || quitting) return {action:'deny'};
    if (isAppUrl(url, origins)) {
      void (async () => {
        if (!(await verifyProduct(url)) || quitting) return;
        if (!adminWindow || adminWindow.isDestroyed()) {
          adminWindow = createWindow('admin');
          adminWindow.on('closed', () => { adminWindow = null; });
        } else if (adminWindow.isVisible()) {
          adminWindow.focus();
        }
        await adminWindow.loadURL(url);
      })().catch(error => log(error.message));
    } else {
      const safe = externalUrl(url);
      if (safe) void shell.openExternal(safe).catch(error => log(error.message));
    }
    return {action:'deny'};
  });
  let prompting = false;
  const recover = async (message, buttons, reloadResponse, detail = '') => {
    if (isSplash || prompting || quitting || window.isDestroyed()) return;
    prompting = true;
    log(message + ' ' + detail);
    try {
      const {response} = await dialog.showMessageBox(window, {type:'warning', title:TITLE, message, detail:safeText(detail), buttons, defaultId:0, cancelId:buttons[0] === 'Wait' ? 0 : buttons.length - 1});
      if (quitting || window.isDestroyed()) return;
      if (response === reloadResponse) window.reload();
      else if (buttons[response] === 'Quit') app.quit();
    } finally { prompting = false; }
  };
  window.webContents.on('render-process-gone', (_event, details) => void recover('The KeepHarness page stopped.', ['Reload','Quit'], 0, details.reason));
  window.on('unresponsive', () => void recover('KeepHarness is not responding.', ['Wait','Reload'], 1));
  if (kind === 'main') {
    window.on('close', event => {
      saveWindowState(window);
      if (closeConfirmed || !processRunning(backend)) return;
      event.preventDefault(); void closeWindow();
    });
    window.on('closed', () => { win = null; if (!quitting) quit(); });
  }
  return window;
}


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
// Only the app origins may write the clipboard and show notifications; every other permission
// (camera, microphone, location, clipboard read...) stays denied.
function limitPermissions() {
  session.defaultSession.setPermissionRequestHandler((_contents, permission, callback, details) =>
    callback(permissionAllowed(permission, details?.requestingUrl, origins)),
  );
  session.defaultSession.setPermissionCheckHandler((_contents, permission, requestingOrigin) =>
    permissionAllowed(permission, requestingOrigin, origins),
  );
}
async function waitFor(url, seconds) {
  for (let i = 0; i < seconds * 4; i++) {
    if (await reachable(url)) return true;
    if (backend && !processRunning(backend)) return false;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  return false;
}
async function startAdmin() {
  if (!(await pythonReady()) || quitting) return false;
  stderr = '';
  backendReady = false;
  harnessVerified = false;
  backend = spawn(python, ['-m', 'control', '--port', String(adminPort)], {
    cwd: project, env: backendEnv(process.env), stdio: ['ignore', 'ignore', 'pipe'],
  });
  // stderr chunks are not records: keep split credentials together before redaction.
  let pending = '', oversized = false;
  const flush = () => {
    const clean = oversized ? '[stderr line omitted: exceeds 16 KiB]' : safeText(pending);
    if (clean) { stderr = (stderr + clean + '\n').slice(-4000); log(clean); }
    pending = ''; oversized = false;
  };
  backend.stderr.setEncoding?.('utf8');
  backend.stderr.on('data', chunk => {
    const lines = String(chunk).split('\n');
    for (let i = 0; i < lines.length; i++) {
      if (!oversized) {
        if (pending.length + lines[i].length > 16384) { pending = ''; oversized = true; }
        else pending += lines[i];
      }
      if (i < lines.length - 1) flush();
    }
  });
  backend.stderr.on('end', flush);
  backend.on('error', error => { stderr = (stderr + safeText(error.message)).slice(-4000); log(error.message); });
  // close follows stdio drainage; exit alone can arrive between credential chunks.
  backend.on('close', (code, signal) => {
    flush();
    log(`Backend exited: ${code} ${signal || ''}`);
    if (backendReady && !quitting) void recoverBackend();
  });
  return true;
}
function requireBackendAlive() {
  if (backend && !processRunning(backend)) throw new Error('The KeepHarness service stopped during startup.');
}
async function recoverBackend() {
  if (recoveryPending || quitting) return;
  recoveryPending = true;
  try {
    const {response} = await dialog.showMessageBox({type:'error', title:TITLE, message:'The KeepHarness service stopped.', detail:stderr.slice(-2000), buttons:['Restart service','Quit'], defaultId:0, cancelId:1});
    if (quitting) return;
    if (response !== 0) { quit(); return; }
    adminCookie = null;
    if (!(await startAdmin())) return;
    if (!(await waitFor(adminUrl, 40))) throw new Error('The KeepHarness service did not restart.');
    const target = (await waitFor(harnessUrl + 'v1/version', 15)) ? harnessUrl : adminUrl;
    if (foreignPort(target === harnessUrl ? [adminPort, harnessPort] : [adminPort]) !== null) throw new Error('The service port is not owned by your account.');
    requireBackendAlive();
    if (!(await verifyProduct(target)) || quitting) return;
    await signInWindow();
    if (quitting) return;
    requireBackendAlive();
    backendReady = true;
    if (win && !win.isDestroyed()) await win.loadURL(target);
    if (quitting) return;
    requireBackendAlive();
    if (adminWindow && !adminWindow.isDestroyed()) adminWindow.reload();
  } catch (error) {
    log(error.message);
    await dialog.showMessageBox({type:'error', title:TITLE, message:safeText(error.message), detail:stderr.slice(-2000)});
    quit();
  } finally { recoveryPending = false; }
}
// Loopback is every account on this computer; the admin and the harness know the owner by a
// session the admin issues for a ticket signed with the secret in its 0600 key file, which only
// this account can read. The secret stays in this process; the window only gets the session.
function openSession(ticket, timeout = 5000) {
  return new Promise((resolve) => {
    const request = http.get(`${adminUrl}open?ticket=${encodeURIComponent(ticket)}`, { timeout }, (response) => {
      response.resume();
      resolve(response.statusCode === 303 ? response.headers['set-cookie'] : null);
    });
    request.on('timeout', () => request.destroy());
    request.on('error', () => resolve(null));
  });
}
async function signInWindow() {
  let secret = '';
  try {
    secret = fs.readFileSync(localKeyPath(os.homedir()), 'utf8');
  } catch {
    return;
  }
  knownSecrets.add(secret.trim());
  const ticket = openTicket(secret);
  if (ticket) knownSecrets.add(ticket);
  const cookies = ticket ? await openSession(ticket) : null;
  adminCookie = cookieValue(cookies, 'admin');
  if (adminCookie) knownSecrets.add(adminCookie);
  const cookie = sessionCookie(cookies, harnessPort);
  if (cookie) { knownSecrets.add(cookie.value); await session.defaultSession.cookies.set(cookie); }
}
// Whether the admin reports queued or running work: true, false, or null when it cannot say.
function backendBusy(timeout = 3000) {
  if (!adminCookie) return Promise.resolve(null);
  return new Promise((resolve) => {
    const request = http.get(`${adminUrl}api/state`, { timeout, headers: { cookie: `admin=${adminCookie}` } }, (response) => {
      let body = '';
      response.setEncoding('utf8');
      response.on('data', (chunk) => {
        body += chunk;
      });
      response.on('end', () => resolve(response.statusCode === 200 ? busyFromState(body) : null));
    });
    request.on('timeout', () => request.destroy());
    request.on('error', () => resolve(null));
  });
}
// Closing the window stops an admin this app started, and the work with it: ask first while that
// work may be running. An admin that was already running (the service) is left alone (D18).
async function closeWindow() {
  if (asking) return;
  asking = true;
  try {
    const allowed = await closeAllowed({
      ownsBackend: processRunning(backend),
      busy: backendBusy,
      confirm: async (working) => {
        const prompt = closePrompt(working);
        const { response } = await dialog.showMessageBox(win, { type: 'warning', title: TITLE, ...prompt, defaultId: 0, cancelId: 0, noLink: true });
        return response === 1;
      },
    });
    if (allowed && win && !win.isDestroyed()) {
      closeConfirmed = true;
      win.close();
    }
  } finally {
    asking = false;
  }
}
// Output of the owner CLI, or '' when it fails or takes too long.
async function runCli(args, seconds = 20) {
  if (!(await pythonReady())) return '';
  return new Promise((resolve) => {
    let output = '';
    const child = spawn(python, ['-m', 'control', ...args], {
      cwd: project,
      env: backendEnv(process.env),
      stdio: ['ignore', 'pipe', 'ignore'],
    });
    const timer = setTimeout(() => child.kill('SIGTERM'), seconds * 1000);
    child.stdout.on('data', (chunk) => {
      output = (output + chunk).slice(-4000);
    });
    child.on('error', () => { clearTimeout(timer); resolve(''); });
    child.on('close', (code) => {
      clearTimeout(timer);
      resolve(code === 0 ? output : '');
    });
  });
}
// Approvals need an enrolled browser. The window enrolls itself for the local owner once, with
// the same single-use link `keepharness approve-device` prints, and never for anyone else.
async function enrollmentTarget() {
  const enrolled = await session.defaultSession.cookies.get({ url: harnessUrl, name: 'harness_session' });
  if (enrolled.length) return null;
  return enrollmentLink(await runCli(['approve-device', '--owner', 'local', '--yes']), new URL(harnessUrl).origin);
}
// The enrollment page asks to confirm with a button; the app generated the link, so it confirms.
function confirmEnrollment(link) {
  win.webContents.once('did-finish-load', () => {
    if (win.webContents.getURL() !== link) return;
    win.webContents
      .executeJavaScript("document.querySelector('form[action^=\"/approve-device\"]')?.requestSubmit()")
      .catch(() => {});
  });
}
// A fixed picture while the backend starts or is attached; it never navigates or opens windows.
function showSplash() {
  splash = createWindow('splash');
  splash.loadFile(path.join(__dirname, 'splash.html')).catch(error => log(error.message));
}
function releaseSplash() {
  if (splash && !splash.isDestroyed()) splash.close();
}

async function start() {
  log('KeepHarness starting');
  installMenu();
  limitPermissions();
  showSplash();
  if (!(await reachable(adminUrl))) {
    if (!(await startAdmin())) return;
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
  releaseSplash();
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
  requireBackendAlive();
  if (!(await verifyProduct(target)) || quitting) return;
  await signInWindow();
  if (quitting) return;
  requireBackendAlive();
  win = createWindow('main');
  const enrollment = target === harnessUrl ? await enrollmentTarget() : null;
  if (quitting) return;
  requireBackendAlive();
  if (enrollment) confirmEnrollment(enrollment);
  await win.loadURL(enrollment || target);
  requireBackendAlive();
  starting = false;
  backendReady = processRunning(backend);
}

if (!app.requestSingleInstanceLock()) app.quit();
else {
  app.on('second-instance', () => {
    if (!win) return;
    if (win.isMinimized()) win.restore();
    win.show();
    win.focus();
  });
  app.on('window-all-closed', () => { if (!starting) app.quit(); });
  app.on('before-quit', event => {
    if (!closeConfirmed && win && processRunning(backend)) { event.preventDefault(); void closeWindow(); return; }
    quitting = true;
  });
  app.on('will-quit', () => {
    // Stop only the admin this app started; the admin stops its harness.
    if (processRunning(backend)) backend.kill('SIGTERM');
  });
  app.whenReady().then(start).catch(async (error) => {
    releaseSplash();
    log(error.message || error);
    await dialog.showMessageBox({ type: 'error', title: TITLE, message: safeText(error.message || error) });
    app.quit();
  });
}
