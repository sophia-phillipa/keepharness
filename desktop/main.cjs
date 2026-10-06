// KeepHarness desktop client: starts the local admin when it is not running,
// opens the harness (or the admin when no provider is set up yet) in its own
// window, keeps navigation inside the two local origins and stops the admin it
// started when the app quits. The window is the owner's: it signs itself in with a session
// minted from the per-install secret and enrolls itself for approvals (decisions D09 and D13).
const { app, BrowserWindow, dialog, ipcMain, session, shell, Menu, screen } = require('electron');
const { execFile, spawn, spawnSync } = require('node:child_process');
const fs = require('node:fs');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const {
  productAllowed, runtimePort, clampBounds, redact, logChunk,
  restoredRoute, appRoute, windowTitle, downloadName,
  appOrigins,
  isAppUrl,
  permissionAllowed,
  externalUrl,
  HANDOFF_APPS, HANDOFF_TEXT_MAX, handoffUrl, handoffShortUrl,
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
  runningFromState,
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
  starting = true,
  quitting = false,
  backendReady = false,
  recoveryPending = false,
  harnessVerified = false,
  productVerification = null,
  backend = null,
  adminCookie = null,
  asking = false,
  closeConfirmed = false,
  stderr = '',
  buildLabel = 'development build';

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
async function pythonReady({fatal = true} = {}) {
  // spawnSync resolves PATH names and reports missing/non-executable files without a shell.
  let result;
  try { result = spawnSync(python, ['--version'], { timeout: 5000, windowsHide: true, env: backendEnv(process.env) }); } catch { /* Same actionable hint. */ }
  if (result && !result.error && result.status === 0) return true;
  log('Python could not start. Run install.sh, or set KEEPHARNESS_PYTHON');
  if (!fatal) return false;
  releaseSplash();
  await dialog.showMessageBox({ type: 'error', title: TITLE, message: 'Python could not start.', detail: 'Run install.sh, or set KEEPHARNESS_PYTHON' });
  quit();
  return false;
}
async function versionBody() {
  const cookies = await session.defaultSession.cookies.get({url:harnessUrl, name:'keepharness-local'});
  const owner = cookies.find(cookie => cookie.name === 'keepharness-local');
  const headers = owner ? {cookie:`keepharness-local=${owner.value}`} : {};
  // Node HTTP does not use Electron's cookie jar or follow redirects. Keep the credential
  // on this fixed loopback harness URL, never on a navigation target.
  return new Promise(resolve => {
    const request = http.get(harnessUrl + 'v1/version', {timeout:1500, headers}, response => {
      let body = '';
      response.setEncoding('utf8');
      response.on('data', chunk => {
        body += chunk;
        if (body.length > 65536) { resolve({status:0, body:''}); request.destroy(); }
      });
      response.on('end', () => resolve({status:response.statusCode, body:response.statusCode === 200 ? body : ''}));
      response.on('error', () => resolve({status:0, body:''}));
    });
    request.on('timeout', () => request.destroy());
    request.on('error', () => resolve({status:0, body:''}));
  });
}
async function verifyProduct(target) {
  if (new URL(target).origin !== new URL(harnessUrl).origin || harnessVerified) return true;
  if (quitting) return false;
  if (!productVerification) {
    productVerification = (async () => {
      // A foreign-owned port is refused before the probe, so it never sees the owner cookie.
      while (foreignPort([harnessPort]) === null) {
        const probe = await versionBody();
        if (productAllowed(probe.body)) { harnessVerified = true; return true; }
        if (probe.status !== 401) break;
        // 401 means our own sign-in failed, not that the service is foreign: offer a retry.
        const {response} = await dialog.showMessageBox({type:'error', title:TITLE, message:'Could not sign in to the KeepHarness service.', detail:'The service did not accept this account\'s local session.', buttons:['Retry','Quit'], defaultId:0, cancelId:1});
        if (response !== 0 || quitting) { quit(); return false; }
        await signInWindow();
        if (quitting) return false;
      }
      await dialog.showMessageBox({type:'error', title:TITLE, message:'This is not a KeepHarness service.', detail:'The /v1/version product must be keepharness.'});
      quit();
      return false;
    })().finally(() => { productVerification = null; });
  }
  return productVerification;
}
function saveWindowState(window) {
  try {
    const file = path.join(app.getPath('userData'), 'window-state.json');
    fs.mkdirSync(path.dirname(file), {recursive:true});
    fs.writeFileSync(file + '.tmp', JSON.stringify({...window.getNormalBounds(), maximized:window.isMaximized(), route:appRoute(window.webContents.getURL(), origins)}), {mode:0o600});
    fs.renameSync(file + '.tmp', file);
  } catch (error) { log(error.message); }
}
function readWindowState() {
  try { return JSON.parse(readText(path.join(app.getPath('userData'), 'window-state.json'))); } catch { return null; }
}
function restoredBounds() {
  const state = readWindowState();
  const primary = screen.getPrimaryDisplay().workArea;
  return clampBounds(state, [primary, ...screen.getAllDisplays().map(display => display.workArea)]);
}
function installMenu() {
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    {label:TITLE, submenu:[{id:'about', label:'About KeepHarness', click:() => dialog.showMessageBox({type:'info', title:'About KeepHarness', message:TITLE, detail:buildLabel})}, {label:'Quit', accelerator:'CmdOrCtrl+Q', click:() => app.quit()}]},
    {label:'Edit', submenu:['undo','redo','cut','copy','paste','selectAll'].map(role => ({role}))},
    {label:'View', submenu:[{role:'resetZoom'}, {role:'zoomIn'}, {role:'zoomOut'}, {role:'togglefullscreen'}, ...(!app.isPackaged ? [{role:'toggleDevTools'}] : [])]},
  ]));
}
// The admin is a Settings section of the harness window, never a window of its own: for the admin
// origin, a loaded harness gets the `#open=settings/<section>` hash (its UI opens Settings there on
// `hashchange`). Other harness URLs (a /guide link, a model-reply link) keep the main window on the
// app: the harness root only focuses it, anything else opens in one reusable secondary window.
const ADMIN_SECTIONS = ['providers', 'home', 'runs', 'catalogs', 'connection'];
let secondWindow = null;
function surface(window) {
  if (window.isMinimized()) window.restore();
  window.show();
  window.focus();
}
async function showInSecondWindow(url) {
  if (!secondWindow || secondWindow.isDestroyed()) {
    secondWindow = createWindow('second');
    secondWindow.on('closed', () => { secondWindow = null; });
  } else if (secondWindow.isMinimized()) secondWindow.restore();
  if (secondWindow.isVisible()) secondWindow.focus();
  await secondWindow.loadURL(url);
}
async function showInMain(url) {
  if (!win || win.isDestroyed()) return;
  const target = new URL(url), current = new URL(win.webContents.getURL());
  if (target.origin === new URL(harnessUrl).origin) {
    if (target.pathname !== '/' || target.search) return showInSecondWindow(url);
    return surface(win);
  }
  surface(win);
  if (target.origin !== new URL(adminUrl).origin || current.origin !== new URL(harnessUrl).origin) return win.loadURL(url);
  const section = target.hash.slice(1);
  // Only the hash changes: a same-URL loadURL may not load at all, so the running page gets it directly.
  await win.webContents.executeJavaScript('location.hash = ' + JSON.stringify('#open=settings/' + (ADMIN_SECTIONS.includes(section) ? section : 'providers')));
}
// Continuation hand-off bridge (WP5): two IPC handlers behind the preload. They answer only the
// harness origin, take no URL or scheme from the page, and never log or store the prompt text.
const fromHarness = event => {
  try { return new URL(event.senderFrame.url).origin === new URL(harnessUrl).origin; } catch { return false; }
};
// Inside a distrobox container the link opens on the host, so the host is asked (D-030): host-spawn
// runs xdg-mime/xdg-open there with constant arguments only; no prompt text ever reaches a host command.
const HOST_SPAWN = '/usr/bin/host-spawn';
const HOST_EXEC = '/usr/bin/distrobox-host-exec';
const DESKTOP_ID = /^[A-Za-z0-9][A-Za-z0-9._-]{0,250}\.desktop$/;
const DBUS_USER_BUS = /^unix:path=(\/run\/host)?\/run\/user\/\d+\/bus$/;
const HOST_URLS = Object.freeze(Object.keys(HANDOFF_APPS).map(handoffShortUrl));
let hostMode;
// A heuristic, not a security control: the container marker, host-spawn and the distrobox xdg-open shim.
function hostHandoff() {
  if (hostMode !== undefined) return hostMode;
  try {
    const shim = (process.env.PATH ?? '').split(':').filter(dir => path.isAbsolute(dir)).map(dir => path.join(dir, 'xdg-open')).find(file => fs.existsSync(file));
    hostMode = fs.existsSync('/run/.containerenv') && fs.existsSync(HOST_SPAWN) && !!shim && fs.realpathSync(shim) === HOST_EXEC;
  } catch { hostMode = false; }
  return hostMode;
}
// Runs one host command and resolves its stdout, or null on any error, timeout or oversized output.
function hostRun(args) {
  const home = os.homedir();
  const bus = process.env.DBUS_SESSION_BUS_ADDRESS;
  const env = {PATH: '/usr/bin:/bin', HOME: home};
  if (process.env.XDG_RUNTIME_DIR) env.XDG_RUNTIME_DIR = process.env.XDG_RUNTIME_DIR;
  if (DBUS_USER_BUS.test(bus ?? '')) env.DBUS_SESSION_BUS_ADDRESS = bus;
  const options = {timeout: 3000, killSignal: 'SIGTERM', maxBuffer: 1024, windowsHide: true, cwd: home, stdio: ['ignore', 'pipe', 'ignore'], env};
  return new Promise(resolve => {
    try { execFile(HOST_SPAWN, ['--no-pty', ...args], options, (error, stdout) => resolve(error ? null : String(stdout))); } catch { resolve(null); }
  });
}
async function handoffInstalled(target) {
  if (!Object.hasOwn(HANDOFF_APPS, target)) return false;
  if (!hostHandoff()) {
    try { return !!app.getApplicationNameForProtocol(HANDOFF_APPS[target]); } catch { return false; }
  }
  const out = await hostRun(['xdg-mime', 'query', 'default', 'x-scheme-handler/' + HANDOFF_APPS[target].replace('://', '')]);
  return out !== null && DESKTOP_ID.test(out.trim());
}
async function handoffApps(event) {
  if (!fromHarness(event)) return {apps: [], error: 'handoff_forbidden'};
  const targets = Object.keys(HANDOFF_APPS);
  const found = await Promise.all(targets.map(handoffInstalled));
  return {apps: targets.filter((_, i) => found[i])};
}
async function handoffOpen(event, payload) {
  if (!fromHarness(event)) return {opened: false, error: 'handoff_forbidden'};
  const {target, text} = payload ?? {};
  if (!Object.hasOwn(HANDOFF_APPS, target) || typeof text !== 'string' || !text || !text.isWellFormed() || text.length > HANDOFF_TEXT_MAX) return {opened: false, error: 'handoff_invalid'};
  if (!await handoffInstalled(target)) return {opened: false, error: 'handoff_app_missing'};
  const onHost = hostHandoff();
  // On the host the URL is one of the constants, never built from the text; the clipboard flow carries the prompt.
  const {url, mode} = onHost ? {url: handoffShortUrl(target), mode: 'short'} : handoffUrl(target, text);
  if (onHost && !HOST_URLS.includes(url)) return {opened: false, error: 'handoff_invalid'};
  // A fixed line: the error message may carry the URL, and with it the prompt.
  try {
    if (onHost) { if (await hostRun(['xdg-open', url]) === null) throw new Error('host open failed'); }
    else await shell.openExternal(url);
  } catch { log('handoff open failed'); return {opened: false, error: 'handoff_open_failed'}; }
  return {opened: true, mode};
}
function registerHandoff() {
  ipcMain.handle('keepharness:handoff-apps', handoffApps);
  ipcMain.handle('keepharness:handoff-open', handoffOpen);
}
// All BrowserWindows, including the splash, share security and lifecycle policies.
function createWindow(kind) {
  const isSplash = kind === 'splash';
  const state = kind === 'main' ? restoredBounds() : null;
  const options = isSplash ? splashOptions(TITLE, icon) : {...windowOptions(TITLE, icon), ...state};
  // The hand-off bridge exists only in the main harness window (see registerHandoff).
  if (kind === 'main') options.webPreferences = {...options.webPreferences, preload: path.join(__dirname, 'preload.cjs')};
  const window = new BrowserWindow(options);
  const show = () => {
    window.removeListener('ready-to-show', show);
    window.webContents.removeListener('did-finish-load', show);
    if (!quitting && !window.isDestroyed()) {
      if (state?.maximized) window.maximize();
      window.show();
      // The splash covers the whole wait: it goes only once the first window is on screen.
      if (kind === 'main') releaseSplash();
    }
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
  window.webContents.on('page-title-updated', (event, title) => {
    event.preventDefault();
    window.setTitle(windowTitle(title, window.webContents.getURL(), origins));
  });
  window.webContents.on('will-navigate', navigate);
  window.webContents.on('will-redirect', navigate);
  window.webContents.on('will-attach-webview', event => event.preventDefault());
  window.webContents.setWindowOpenHandler(({url}) => {
    if (isSplash || quitting) return {action:'deny'};
    if (isAppUrl(url, origins)) {
      void (async () => {
        if (!(await verifyProduct(url)) || quitting) return;
        await showInMain(url);
      })().catch(error => log(error.message));
    } else {
      const safe = externalUrl(url);
      if (safe) void shell.openExternal(safe).catch(error => log(error.message));
    }
    return {action:'deny'};
  });
  let prompting = false, pendingCrash = null;
  const recover = async (message, buttons, reloadResponse, detail = '', crashed = false) => {
    if (isSplash || quitting || window.isDestroyed()) return;
    if (prompting) {
      if (crashed) pendingCrash = detail;
      return;
    }
    prompting = true;
    log(message + ' ' + detail);
    try {
      const {response} = await dialog.showMessageBox(window, {type:'warning', title:TITLE, message, detail:safeText(detail), buttons, defaultId:0, cancelId:buttons[0] === 'Wait' ? 0 : buttons.length - 1});
      if (quitting || window.isDestroyed()) return;
      if (response === reloadResponse) { pendingCrash = null; window.reload(); }
      else if (buttons[response] === 'Quit') quit();
    } finally {
      prompting = false;
      const reason = pendingCrash;
      pendingCrash = null;
      if (reason !== null) void recover('The KeepHarness page stopped.', ['Reload','Quit'], 0, reason, true);
    }
  };
  window.webContents.on('render-process-gone', (_event, details) => void recover('The KeepHarness page stopped.', ['Reload','Quit'], 0, details.reason, true));
  window.on('unresponsive', () => void recover('KeepHarness is not responding.', ['Wait','Reload'], 1));
  if (kind === 'main') {
    monitorWork(window);
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
async function waitFor(url, seconds, giveUp = null) {
  // A deadline, not a count: a slow giveUp probe must not stretch the wait.
  const deadline = Date.now() + seconds * 1000;
  while (Date.now() < deadline) {
    if (await reachable(url)) return true;
    if (backend && !processRunning(backend)) return false;
    if (giveUp && await giveUp()) return false;
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
  backend.on('exit', () => setWorkProgress(win, false));
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
    const target = await chooseTarget();
    if (foreignPort(target === harnessUrl ? [adminPort, harnessPort] : [adminPort]) !== null) throw new Error('The service port is not owned by your account.');
    requireBackendAlive();
    if (!(await verifyProduct(target)) || quitting) return;
    if (quitting) return;
    requireBackendAlive();
    backendReady = true;
    if (win && !win.isDestroyed()) await win.loadURL(target);
    if (quitting) return;
    requireBackendAlive();
    if (secondWindow && !secondWindow.isDestroyed()) secondWindow.reload();
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
// What the admin's state says through `read` (true, false or null when it cannot say).
function adminState(read, timeout) {
  if (!adminCookie) return Promise.resolve(null);
  return new Promise((resolve) => {
    const request = http.get(`${adminUrl}api/state`, { timeout, headers: { cookie: `admin=${adminCookie}` } }, (response) => {
      let body = '';
      response.setEncoding('utf8');
      response.on('data', (chunk) => {
        body += chunk;
        if (body.length > 1048576) { resolve(null); request.destroy(); }
      });
      response.on('error', () => resolve(null));
      response.on('end', () => resolve(response.statusCode === 200 ? read(body) : null));
    });
    request.on('timeout', () => request.destroy());
    request.on('error', () => resolve(null));
  });
}
const backendBusy = (timeout = 3000) => adminState(busyFromState, timeout);
const harnessRunning = (timeout = 1500) => adminState(runningFromState, timeout);
function setWorkProgress(window, busy) {
  if (window && !window.isDestroyed()) window.setProgressBar(busy ? 2 : -1);
  if (process.platform === 'linux' && typeof app.setBadgeCount === 'function') {
    try { app.setBadgeCount(busy ? 1 : 0); } catch { /* Desktop shell may not support badges. */ }
  }
}
// One request at a time; unknown state clears indicators and backs off up to a minute.
function monitorWork(window) {
  let timer, delay = 5000, stopped = false;
  const poll = async () => {
    if (stopped || quitting || window.isDestroyed()) return;
    const polledBackend = backend;
    let busy = await backendBusy();
    if (stopped || quitting || window.isDestroyed()) return;
    if (polledBackend !== backend || (backend && !processRunning(backend))) busy = null;
    setWorkProgress(window, busy === true);
    delay = busy === null ? Math.min(delay * 2, 60000) : 5000;
    timer = setTimeout(poll, delay);
    timer.unref?.();
  };
  window.on('close', () => setWorkProgress(window, false));
  window.once('closed', () => { stopped = true; clearTimeout(timer); setWorkProgress(window, false); });
  void poll();
}
function limitDownloads() {
  session.defaultSession.on('will-download', (event, item, contents) => {
    const url = item.getURL();
    let downloadUrl = url;
    try {
      const parsed = new URL(url);
      if (parsed.protocol === 'blob:') downloadUrl = parsed.origin;
    } catch { /* Invalid URLs are refused by the origin policy below. */ }
    if (!isAppUrl(downloadUrl, origins) || !isAppUrl(contents?.getURL(), origins)) {
      event.preventDefault();
      const safe = externalUrl(url);
      if (safe && !isAppUrl(url, origins)) void shell.openExternal(safe).catch(error => log(error.message));
      return;
    }
    item.setSaveDialogOptions({defaultPath:path.join(app.getPath('downloads'), downloadName(item.getFilename()))});
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
  if (!(await pythonReady({fatal:false}))) return '';
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
function confirmEnrollment(link, route) {
  const window = win;
  window.webContents.once('did-finish-load', () => {
    if (window.webContents.getURL() !== link) return;
    // Single-use: the first load after the submit ends the restore, whatever its URL.
    const restore = () => {
      if (window.webContents.getURL() !== harnessUrl || quitting || window.isDestroyed()) return;
      void window.loadURL(route).catch(error => log(error.message));
    };
    if (route) window.webContents.once('did-finish-load', restore);
    window.webContents
      .executeJavaScript("document.querySelector('form[action^=\"/approve-device\"]')?.requestSubmit()")
      .catch(() => window.webContents.removeListener('did-finish-load', restore));
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

// A configured admin starts the harness before it answers, so one that reports the harness as
// not running (no provider yet) has nothing to wait for: open Admin to set it up. Unknown state
// keeps waiting. The sign-in comes first because the state needs the admin session; a foreign
// admin port gets none and is refused by the caller.
async function chooseTarget() {
  if (foreignPort([adminPort]) !== null) return adminUrl;
  requireBackendAlive();
  await signInWindow();
  const notRunning = async () => (await harnessRunning()) === false;
  return (await waitFor(harnessUrl + 'v1/version', 15, notRunning)) ? harnessUrl : adminUrl;
}

async function start() {
  log(`KeepHarness starting from ${__dirname}`);
  if (app.isPackaged) {
    try {
      const manifest = JSON.parse(fs.readFileSync(path.join(project, '..', 'build-manifest.json'), 'utf8'));
      if (manifest.product !== 'keepharness' || manifest.dirty !== false ||
          !/^[0-9]+\.[0-9]+\.[0-9]+(?:[+-][0-9A-Za-z.]+)?$/.test(manifest.version) ||
          !/^[0-9a-f]{40}$/.test(manifest.commit) ||
          (manifest.built_at !== undefined && (typeof manifest.built_at !== 'string' ||
          !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/.test(manifest.built_at) ||
          !Number.isFinite(Date.parse(manifest.built_at))))) throw new Error('Invalid build manifest');
      buildLabel = `${manifest.version} (${manifest.commit.slice(0,7)}) · ${manifest.built_at || 'build date unavailable'}`;
      log('Build: ' + JSON.stringify({version:manifest.version, commit:manifest.commit}));
    } catch (error) {
      log('Packaged provenance refused: ' + error.message);
      await dialog.showMessageBox({type:'error', title:TITLE, message:'KeepHarness package provenance is invalid.', detail:'Reinstall a verified desktop package.'});
      app.quit();
      return;
    }
  }
  installMenu();
  registerHandoff();
  limitPermissions();
  limitDownloads();
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
  const target = await chooseTarget();
  const foreign = foreignPort(target === harnessUrl ? [adminPort, harnessPort] : [adminPort]);
  if (foreign !== null) {
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
  if (quitting) return;
  requireBackendAlive();
  win = createWindow('main');
  const enrollment = target === harnessUrl ? await enrollmentTarget() : null;
  if (quitting) return;
  requireBackendAlive();
  const route = restoredRoute(readWindowState()?.route, target, origins);
  if (enrollment) confirmEnrollment(enrollment, route);
  await win.loadURL(enrollment || route || target);
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
