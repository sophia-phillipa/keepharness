const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const { EventEmitter } = require('node:events');
const source = fs.readFileSync(path.join(__dirname, 'main.cjs'), 'utf8');
const root = fs.mkdtempSync(path.join(os.tmpdir(), 'desktop-main-'));
test.after(() => fs.rmSync(root, { recursive: true, force: true }));
let sequence = 0;
const settle = async () => { for (let i = 0; i < 150; i++) await new Promise(resolve=>setTimeout(resolve,1)); };

async function boot(options = {}) {
  const home = options.home || path.join(root, String(sequence++));
  const userData = path.join(home, '.config/KeepHarness');
  fs.mkdirSync(userData, { recursive: true });
  if (options.runtime) {
    fs.mkdirSync(path.join(home, '.local/share/keepharness'), { recursive: true });
    fs.writeFileSync(path.join(home, '.local/share/keepharness/runtime.json'), options.runtime);
  }
  const windows = [], dialogs = [], external = [], requests = [], requestDetails = [], children = [], probes = [], timers = new Map(), badges = [];
  const app = new EventEmitter();
  Object.assign(app, { isPackaged: options.packaged ?? true, setName() {}, setBadgeCount: n => badges.push(n), getPath: name => name === 'downloads' ? path.join(home, 'Downloads') : userData,
    requestSingleInstanceLock: () => true, whenReady: async () => {}, quit: () => { app.quits++; app.emit('before-quit', event()); }, quits: 0 });
  let menu;
  const event = () => ({ prevented: false, preventDefault() { this.prevented = true; } });
  class Window extends EventEmitter {
    constructor(opts) {
      super(); this.options = opts; this.shows = 0; this.destroyed = false; this.bounds = {x:100,y:100,width:1200,height:800}; this.maximized = false;
      this.webContents = new EventEmitter();
      Object.assign(this.webContents, { setWindowOpenHandler: fn => { this.open = fn; }, getURL: () => this.url,
        executeJavaScript: async () => { if (options.failEnrollment) throw new Error("enrollment failed"); }, reload: () => { this.reloads = (this.reloads || 0) + 1; } });
      windows.push(this);
    }
    async loadURL(url) { this.url = url; if (options.dieOnRestartLoad && children.length === 2) { children[1].exitCode=1; children[1].emit('exit',1,null); children[1].emit('close',1,null); } }
    async loadFile(file) { this.file = file; }
    setTitle(title) { this.title = title; }
    setProgressBar(value) { this.progress = value; }
    show() { this.shows++; }
    isVisible() { return this.shows > 0 && !this.hidden; }
    hide() { this.hidden = true; }
    focus() { this.focused = true; this.minimizedAtFocus = this.isMinimized(); }
    isDestroyed() { return this.destroyed; }
    isMinimized() { return !!this.minimized; }
    restore() { this.minimized = false; this.restored = true; }
    reload() { this.webContents.reload(); }
    isMaximized() { return this.maximized; }
    maximize() { this.maximized = true; this.showsAtMaximize = this.shows; }
    getNormalBounds() { return this.bounds; }
    close() { const e = event(); this.emit('close', e); if (!e.prevented) { this.destroyed = true; this.emit('closed'); if (windows.every(w => w.destroyed)) app.emit('window-all-closed'); } }
  }
  const ownerSession = 'owner-session-abcdefghijklmnop';
  const cookies = [];
  const electron = { app, BrowserWindow: Window, shell: { openExternal: async url => external.push(url) },
    screen: { getAllDisplays: () => [{workArea:{x:0,y:0,width:1920,height:1080}}], getPrimaryDisplay: () => ({workArea:{x:0,y:0,width:1920,height:1080}}) },
    Menu: { buildFromTemplate: template => template, setApplicationMenu: template => { menu = template; } },
    dialog: { showMessageBox: async (...args) => { const d = args.at(-1); dialogs.push(JSON.parse(JSON.stringify(d))); if (options.onDialog) return options.onDialog(d, app); return { response: options.response ?? 1 }; }, showAboutPanel() {} },
    session: { defaultSession: Object.assign(new EventEmitter(), { setPermissionRequestHandler(fn) { this.permission = fn; }, setPermissionCheckHandler(fn) { this.check = fn; }, cookies: { set: async cookie => { cookies.push(cookie); }, get: async query => query.name === 'keepharness-local' ? cookies.filter(cookie => cookie.name === query.name && new URL(cookie.url).origin === new URL(query.url).origin) : options.noSession ? [] : [{}] } }) } };
  const clock = options.clock || {t: Date.now()}; // the app's waits run on this clock: 250 ms polls advance it without sleeping
  let adminReady = !options.startBackend;
  const http = { get(url, opts, callback) {
    requests.push(url); requestDetails.push({url, ...opts}); const req = new EventEmitter(); req.destroy = () => req.emit('error', new Error('timeout'));
    queueMicrotask(() => {
      if (url.includes('v1/version')) options.onVersionProbe?.(windows);
      if (options.stateCost && url.endsWith('api/state')) clock.t += options.stateCost; // a probe that runs into its timeout
      if ((options.harnessOffline && url.includes('v1/version')) || (!adminReady && !url.includes('v1/version'))) { req.emit('error', new Error('offline')); return; }
      const res = new EventEmitter(); res.statusCode = options.status || 200; res.headers = {}; if (url.includes('v1/version') && (options.rejectCredential || opts.headers?.cookie !== `keepharness-local=${ownerSession}`)) res.statusCode=401; if (url.includes('/open?')) { res.statusCode=options.failOpen ? 500 : 303; res.headers['set-cookie']=['admin=abcdefghijklmnop; Path=/', `keepharness-local=${ownerSession}; Path=/`]; } res.resume = () => {}; res.setEncoding = () => {};
      callback(res);
      if (options.dieDuringVersion && url.includes('v1/version') && children.length) { children.at(-1).exitCode=1; children.at(-1).emit('exit',1,null); }
      res.emit('data', url.includes('v1/version') ? (options.version ?? '{"product":"keepharness"}') : url.endsWith('api/state') ? JSON.stringify({status:{busy:options.busy,running:options.running}}) : '{}'); res.emit('end');
    }); return req;
  } };
  const childProcess = { spawnSync(executable) { probes.push(executable); return options.badPython ? {error:new Error('ENOENT'),status:null} : {status:0}; },
    spawn(_executable,args) { const child = new EventEmitter(); Object.assign(child, {stderr:new EventEmitter(),stdout:new EventEmitter(),exitCode:null,signalCode:null,kill() { this.signalCode='SIGTERM'; }}); children.push(child); adminReady=true; if (options.enrollment && args.includes('approve-device')) queueMicrotask(() => { child.stdout.emit('data',options.enrollment); child.exitCode=0; child.emit('close',0); }); return child; } };
  const fakeFs = new Proxy(fs, { get(target, key) {
    if (key === 'readFileSync') return (file, ...args) => options.tcp && file === '/proc/net/tcp' ? options.tcp : options.tcp && file === '/proc/net/tcp6' ? '' : String(file).endsWith('build-manifest.json') ? (options.manifest ?? JSON.stringify({product:'keepharness',version:'0.16.0',commit:'a'.repeat(40),dirty:false,built_at:'2026-10-04T12:00:00Z'})) : String(file).endsWith('local.key') ? 'abcdefghijklmnop' : target.readFileSync(file,...args);
    if (key === 'existsSync') return file => file === '/proc/net/tcp' ? !!options.tcp : options.badPython && String(file).includes('python') ? false : target.existsSync(file);
    return target[key];
  } });
  const proc = new EventEmitter(); Object.assign(proc, { env:{ KEEPHARNESS_ADMIN_PORT:'18194', KEEPHARNESS_PYTHON:process.execPath, ...options.env }, platform:'linux', getuid: () => 1000 });
  vm.runInNewContext(source, { require(name) { return ({electron, 'node:fs':fakeFs, 'node:os':{homedir:()=>home}, 'node:http':http, 'node:child_process':childProcess, './policy.cjs':require('./policy.cjs')})[name] || require(name); }, __dirname, process:proc, console, Buffer, URL, Date: {now: () => clock.t, parse: Date.parse}, setTimeout:(fn,ms)=> { if (ms >= 5000) { const timer={fn,ms,unref(){}}; timers.set(timer,timer); return timer; } if (ms === 250) clock.t += 250; return setTimeout(fn,ms===250?0:ms); }, clearTimeout:timer => { timers.delete(timer); clearTimeout(timer); } }, {filename:'main.cjs'});
  await settle();
  return {timers,badges,session:electron.session.defaultSession,home,userData,windows,dialogs,external,requests,requestDetails,children,probes,app,event,get menu(){return menu;},main:windows.find(w=>!w.options.frame && !w.file) || windows.find(w=>w.options.frame !== false)};
}

test('credential-requiring harness is accepted and opens the main window', async () => {
  const h = await boot();
  assert.equal(h.app.quits, 0);
  assert.equal(h.dialogs.length, 0);
  assert.equal(h.main.url, 'http://127.0.0.1:8095/');
  const checks = h.requestDetails.filter(r => r.url.endsWith('/v1/version'));
  assert.ok(checks.some(r => !r.headers?.cookie)); // Readiness may receive 401.
  assert.ok(checks.some(r => r.headers?.cookie === 'keepharness-local=owner-session-abcdefghijklmnop'));
  for (const request of h.requestDetails.filter(r => r.headers?.cookie?.includes('keepharness-local='))) {
    assert.equal(request.url, 'http://127.0.0.1:8095/v1/version');
  }
});
test('401 with the owner credential is refused before a page loads', async () => {
  const h = await boot({rejectCredential:true});
  assert.ok(h.requestDetails.some(r => r.headers?.cookie === 'keepharness-local=owner-session-abcdefghijklmnop'));
  assert.ok(h.dialogs.some(d => /KeepHarness service/.test(d.message)));
  assert.ok(h.app.quits);
  assert.equal(h.windows.filter(w => w.url).length, 0);
});
test('foreign or malformed product is refused before a page loads', async () => {
  for (const version of ['{"product":"foreign"}', '{}', 'broken']) {
    const h = await boot({version});
    assert.ok(h.requestDetails.some(r => r.headers?.cookie === 'keepharness-local=owner-session-abcdefghijklmnop'));
    assert.ok(h.dialogs.some(d => /product|KeepHarness service/i.test(d.message)));
    assert.ok(h.app.quits); assert.equal(h.windows.filter(w=>w.url).length, 0);
  }
});
test('runtime.json port 18195 is used and environment overrides it', async () => {
  const h = await boot({runtime:'{"port":18195}'});
  assert.ok(h.requests.includes('http://127.0.0.1:18195/v1/version'));
  assert.equal(h.main.url, 'http://127.0.0.1:18195/');
  const other = await boot({runtime:'{"port":18195}',env:{KEEPHARNESS_PORT:'18196'}});
  assert.equal(other.main.url, 'http://127.0.0.1:18196/');
});
test('redirects open externally and webviews are prevented on every window', async () => {
  const h = await boot();
  for (const w of h.windows) {
    const e = h.event(); w.webContents.emit('will-redirect', e, 'https://example.com/'); assert.ok(e.prevented);
    const attach = h.event(); w.webContents.emit('will-attach-webview', attach); assert.ok(attach.prevented);
  }
  assert.ok(h.external.includes('https://example.com/'));
});
test('missing or unrunnable Python shows installation hint before spawning', async () => {
  const h = await boot({startBackend:true,badPython:true});
  assert.equal(h.children.length, 0);
  assert.ok(h.dialogs.some(d => `${d.message} ${d.detail}`.includes('Run install.sh, or set KEEPHARNESS_PYTHON')));
});
test('renderer crash offers Reload or Quit and unresponsive offers Wait or Reload', async () => {
  const h = await boot({response:0});
  h.main.webContents.emit('render-process-gone', {}, {reason:'crashed'}); await settle();
  assert.deepEqual(h.dialogs.at(-1).buttons, ['Reload','Quit']); assert.equal(h.main.reloads, 1);
  h.main.emit('unresponsive'); await settle();
  assert.deepEqual(h.dialogs.at(-1).buttons, ['Wait','Reload']); assert.equal(h.main.reloads, 1);
});
test('owned backend exit offers restart with redacted stderr tail', async () => {
  const h = await boot({startBackend:true,response:0});
  h.children[0].stderr.emit('data', 'secret=private-value failure');
  h.children[0].exitCode=1; h.children[0].emit('exit',1,null); h.children[0].emit('close',1,null); await settle();
  assert.deepEqual(h.dialogs.at(-1).buttons, ['Restart service','Quit']);
  assert.match(h.dialogs.at(-1).detail, /failure/); assert.ok(!h.dialogs.at(-1).detail.includes('private-value'));
  assert.equal(h.children.length, 2);
});
test('packaged branded menu omits Reload and DevTools', async () => {
  const h = await boot(); assert.ok(h.menu); assert.equal(h.menu[0].label, 'KeepHarness');
  assert.ok(!/reload|devtools/i.test(JSON.stringify(h.menu)));
  assert.match(JSON.stringify(h.menu), /about/); assert.match(JSON.stringify(h.menu), /zoomIn/);
  const dev = await boot({packaged:false}); assert.match(JSON.stringify(dev.menu), /toggleDevTools/);
});
test('normal bounds and maximized state survive restart atomically', async () => {
  const h = await boot(); h.main.bounds={x:120,y:90,width:1100,height:750}; h.main.maximized=true;
  h.main.emit('resize'); h.main.emit('close',h.event()); await settle();
  const file=path.join(h.userData,'window-state.json'); assert.ok(fs.existsSync(file)); assert.ok(!fs.existsSync(file+'.tmp'));
  const next=await boot({home:h.home});
  next.main.emit('ready-to-show');
  assert.equal(next.main.options.x,120); assert.equal(next.main.options.width,1100); assert.equal(next.main.maximized,true);
});
test('Admin popup is one reusable second window with the same policies', async () => {
  const h=await boot(); const original=h.main.url;
  h.main.open({url:'http://127.0.0.1:18194/'}); await settle();
  const admin=h.windows.at(-1); assert.notEqual(admin,h.main); assert.equal(admin.url,'http://127.0.0.1:18194/');
  h.main.open({url:'http://127.0.0.1:18194/settings'}); await settle(); assert.equal(h.windows.length,3); assert.equal(h.main.url,original);
  assert.deepEqual(admin.options.webPreferences,h.main.options.webPreferences);
  const e=h.event(); admin.webContents.emit('will-redirect',e,'https://example.com/'); assert.ok(e.prevented);
  const attach=h.event(); admin.webContents.emit('will-attach-webview',attach); assert.ok(attach.prevented);
});
test('windows show once in either event order and the splash closes when the first window is shown', async () => {
  for (const reverse of [false,true]) {
    const h=await boot(); assert.ok(!h.windows[0].destroyed);
    if (reverse) h.main.webContents.emit('did-finish-load');
    h.main.emit('ready-to-show'); h.main.webContents.emit('did-finish-load'); assert.equal(h.main.shows,1);
    assert.ok(h.windows[0].destroyed);
    h.windows[0].emit('ready-to-show'); assert.equal(h.windows[0].shows,0);
  }
});
test('the splash stays up while the harness is awaited, and no window is hidden meanwhile', async () => {
  const seen = [];
  const h = await boot({harnessOffline:true, onVersionProbe: windows => seen.push(windows[0].destroyed)});
  assert.ok(seen.length > 1 && seen.every(destroyed => !destroyed));
  assert.equal(h.main.url, 'http://127.0.0.1:18194/');
  assert.ok(!h.windows[0].destroyed);
});
test('an admin that reports the harness is not running opens Admin without waiting out the harness', async () => {
  const h = await boot({harnessOffline:true, running:false});
  assert.equal(h.main.url, 'http://127.0.0.1:18194/');
  assert.ok(h.requests.filter(url => url.endsWith('/v1/version')).length <= 2);
  assert.equal(h.dialogs.length, 0);
});
test('a stuck admin state probe cannot stretch the 15 s harness wait', async () => {
  const clock = {t: 0}; const h = await boot({harnessOffline:true, clock, stateCost:1500});
  assert.equal(h.main.url, 'http://127.0.0.1:18194/');
  assert.ok(h.requests.filter(url => url.endsWith('/v1/version')).length <= 12);
});
test('Restart service with no provider opens Admin without the harness wait', async () => {
  const h = await boot({startBackend:true, response:0, harnessOffline:true, running:false});
  const before = h.requests.filter(url => url.endsWith('/v1/version')).length;
  h.children[0].exitCode=1; h.children[0].emit('exit',1,null); h.children[0].emit('close',1,null); await settle();
  assert.equal(h.children.length, 2);
  assert.ok(h.requests.filter(url => url.endsWith('/v1/version')).length - before <= 2);
  assert.equal(h.main.url, 'http://127.0.0.1:18194/');
});
test('main log rotates after two MiB and never stores sensitive values', async () => {
  const h=await boot({startBackend:true});
  for(let i=0;i<512;i++) h.children[0].stderr.emit('data','ticket=private-ticket '+ 'x'.repeat(4096)+'\n');
  const file=path.join(h.userData,'logs/main.log');
  assert.ok(fs.statSync(file).size <= 1024*1024); assert.ok(fs.existsSync(file+'.1'));
  for(const f of [file,file+'.1']) { const log=fs.readFileSync(f,'utf8'); assert.ok(!log.includes('private-ticket')); assert.match(log,/\d{4}-\d\d-\d\dT/); }
});

test('Admin waits for readiness and unresponsive cancellation defaults to Wait', async () => {
  const h=await boot(); h.main.open({url:'http://127.0.0.1:18194/'}); await settle();
  const admin=h.windows.at(-1); assert.equal(admin.shows,0);
  admin.webContents.emit('did-finish-load'); admin.emit('ready-to-show'); assert.equal(admin.shows,1);
  h.main.emit('unresponsive'); await settle(); assert.equal(h.dialogs.at(-1).cancelId,0);
});
test('stderr redaction survives split chunks and oversized lines', async () => {
  const h=await boot({startBackend:true});
  for(const chunk of ['sec','ret=chunk-secret\nCookie: adm','in=chunk-cookie\n', 'ticket=', 'oversized-ticket'+'x'.repeat(20000), '\n']) h.children[0].stderr.emit('data',chunk);
  const log=fs.readFileSync(path.join(h.userData,'logs/main.log'),'utf8');
  assert.ok(!log.includes('chunk-secret')); assert.ok(!log.includes('chunk-cookie')); assert.ok(!log.includes('oversized-ticket'));
});

test('backend death during bootstrap is reported before any content loads', async () => {
  const h=await boot({startBackend:true,dieDuringVersion:true});
  assert.ok(h.app.quits); assert.ok(h.dialogs.some(d=>/service.*stop|service.*exit/i.test(d.message)));
  assert.equal(h.windows.filter(w=>w.url).length,0);
});
test('a restart dialog resolved after quitting never spawns another backend', async () => {
  let answer;
  const h=await boot({startBackend:true,onDialog:()=>new Promise(resolve=>{answer=resolve;})});
  h.children[0].exitCode=1; h.children[0].emit('exit',1,null); h.children[0].emit('close',1,null); await settle();
  h.app.quit(); answer({response:0}); await settle(); assert.equal(h.children.length,1);
});

test('Python executable names on PATH retain the preflight contract', async () => {
  const h=await boot({startBackend:true,env:{KEEPHARNESS_PYTHON:'python3'}});
  assert.equal(h.children.length,1); assert.ok(h.probes.includes('python3'));
});
test('Admin fallback cannot open a foreign harness later', async () => {
  const options={harnessOffline:true}; const h=await boot(options);
  assert.equal(h.main.url,'http://127.0.0.1:18194/');
  options.harnessOffline=false; options.version='{"product":"foreign"}';
  h.main.open({url:'http://127.0.0.1:8095/'}); await settle();
  assert.ok(h.app.quits); assert.ok(!h.windows.some(w=>w.url==='http://127.0.0.1:8095/'));
});

test('stderr drains after exit without splitting a credential or restarting early', async () => {
  const h=await boot({startBackend:true,response:0});
  h.children[0].stderr.emit('data','tic'); h.children[0].exitCode=1;
  h.children[0].emit('exit',1,null); await settle();
  assert.equal(h.dialogs.length,0);
  h.children[0].stderr.emit('data','ket=late-secret\n'); h.children[0].stderr.emit('end');
  h.children[0].emit('close',1,null); await settle();
  assert.equal(h.children.length,2);
  assert.ok(!fs.readFileSync(path.join(h.userData,'logs/main.log'),'utf8').includes('late-secret'));
});
test('death during restarted window loading reports failure and quits', async () => {
  const h=await boot({startBackend:true,response:0,dieOnRestartLoad:true});
  h.children[0].exitCode=1; h.children[0].emit('exit',1,null); h.children[0].emit('close',1,null); await settle();
  assert.ok(h.app.quits); assert.match(h.dialogs.at(-1).message,/service stopped/);
});


test('attached harness loads without Python or an enrollment cookie', async () => {
  const h = await boot({badPython:true,noSession:true});
  assert.equal(h.main.url, 'http://127.0.0.1:8095/');
  assert.equal(h.app.quits, 0); assert.equal(h.children.length, 0);
  assert.equal(h.dialogs.length, 0);
  assert.match(fs.readFileSync(path.join(h.userData,'logs/main.log'),'utf8'), /Python could not start/);
});
test('crash during Wait dialog is offered again after Wait', async () => {
  const answers = [];
  const h = await boot({onDialog:() => new Promise(resolve => answers.push(resolve))});
  h.main.emit('unresponsive');
  h.main.webContents.emit('render-process-gone', {}, {reason:'killed'});
  assert.equal(h.dialogs.length, 1);
  answers.shift()({response:0}); await settle();
  assert.equal(h.dialogs.length, 2);
  assert.deepEqual(h.dialogs[1].buttons, ['Reload','Quit']);
  answers.shift()({response:0}); await settle();
  assert.equal(h.main.reloads, 1); assert.equal(h.app.quits, 0);
});
test('saved maximization waits for readiness and precedes showing', async () => {
  const first = await boot();
  first.main.maximized = true; first.main.emit('close',first.event());
  for (const signal of ['ready-to-show','did-finish-load']) {
    const h = await boot({home:first.home});
    assert.equal(h.main.maximized, false); assert.equal(h.main.shows, 0);
    (signal === 'ready-to-show' ? h.main : h.main.webContents).emit(signal);
    assert.equal(h.main.maximized, true); assert.equal(h.main.showsAtMaximize, 0);
    assert.equal(h.main.shows, 1);
  }
});
test('concurrent foreign harness navigation shares one verification dialog', async () => {
  let answer;
  const options = {harnessOffline:true,onDialog:() => new Promise(resolve => { answer=resolve; })};
  const h = await boot(options);
  options.harnessOffline=false; options.version='{"product":"foreign"}';
  for (const signal of ['will-navigate','will-redirect']) {
    const e=h.event(); h.main.webContents.emit(signal,e,'http://127.0.0.1:8095/'); assert.ok(e.prevented);
  }
  await settle(); assert.equal(h.dialogs.length,1);
  answer({response:0}); await settle(); assert.equal(h.app.quits,1);
  assert.equal(h.main.url,'http://127.0.0.1:18194/');
});
test('reused minimized Admin is restored before focus', async () => {
  const h=await boot(); h.main.open({url:'http://127.0.0.1:18194/'}); await settle();
  const admin=h.windows.at(-1); admin.emit('ready-to-show'); admin.minimized=true;
  h.main.open({url:'http://127.0.0.1:18194/settings'}); await settle();
  assert.equal(admin.restored,true); assert.equal(admin.minimizedAtFocus,false);
  assert.equal(h.windows.length,3);
});
test('a foreign-port refusal leaves the splash to the quit, which closes it', async () => {
  const h = await boot({tcp: foreignHarnessTable});
  assert.ok(h.dialogs.some(d => /not yours/.test(d.message))); assert.equal(h.app.quits, 1);
  const splash = h.windows.find(w => w.file);
  assert.equal(splash.destroyed, false); // still the picture behind the dialog
  for (const w of h.windows) w.close(); // Electron closes every window on quit
  assert.equal(splash.destroyed, true);
});

test('packaged startup refuses missing or dirty provenance before network access', async () => {
  for (const manifest of ['', '{"dirty":true}']) {
    const h=await boot({manifest}); assert.ok(h.app.quits); assert.equal(h.requests.length,0);
    assert.ok(h.dialogs.some(d=>/provenance/i.test(d.message)));
  }
});
test('Quit in the crash dialog does not re-prompt for a crash queued meanwhile', async () => {
  const answers = [];
  const h = await boot({onDialog:() => new Promise(resolve => answers.push(resolve))});
  h.main.webContents.emit('render-process-gone', {}, {reason:'crashed'});
  h.main.webContents.emit('render-process-gone', {}, {reason:'killed'});
  assert.equal(h.dialogs.length, 1);
  answers.shift()({response:1}); await settle();
  assert.equal(h.dialogs.length, 1); assert.equal(h.app.quits, 1);
});


test('About KeepHarness shows validated manifest build label and development build', async () => {
  for (const packaged of [true,false]) {
    const h=await boot({packaged,startBackend:true});
    const about=h.menu[0].submenu.find(item=>item.label === 'About KeepHarness');
    assert.ok(about); await about.click();
    assert.match(h.dialogs.at(-1).detail, packaged ? /0\.16\.0.*aaaaaaa.*2026-10-04/ : /development build/);
    if (packaged) assert.match(fs.readFileSync(path.join(h.userData,'logs/main.log'),'utf8'), /Build:.*"version":"0.16.0".*"commit":"a{40}"/);
  }
});
test('app-origin downloads use a save dialog with sanitized name and foreign downloads cancel', async () => {
  const h=await boot();
  const item={getURL:()=>h.main.url+'file',getFilename:()=> '../bad\\name\n.txt',setSaveDialogOptions(value){this.save=value;}};
  const e=h.event(); h.session.emit('will-download',e,item,h.main.webContents);
  assert.ok(item.save); assert.equal(e.prevented,false);
  assert.equal(path.dirname(item.save.defaultPath),path.join(h.home,'Downloads'));
  assert.ok(!/[\\\n]/.test(path.basename(item.save.defaultPath)));
  for (const url of ['https://example.com/file','file:///etc/passwd']) {
    const foreign=h.event(); h.session.emit('will-download',foreign,{...item,getURL:()=>url},h.main.webContents); assert.ok(foreign.prevented);
  }
  assert.deepEqual(h.external,['https://example.com/file']);
});
test('busy polling sets progress, clears idle, backs off errors and stops on close', async () => {
  const options={busy:true}; const h=await boot(options);
  assert.equal(h.main.progress,2); assert.equal(h.badges.at(-1),1);
  const tick=async () => { const timer=[...h.timers.values()][0]; assert.ok(timer); h.timers.delete(timer); timer.fn(); await settle(); };
  assert.equal([...h.timers.values()][0].ms,5000);
  options.busy=false; await tick(); assert.equal(h.main.progress,-1); assert.equal(h.badges.at(-1),0);
  options.busy=null; await tick(); assert.equal([...h.timers.values()][0].ms,10000);
  await tick(); assert.equal([...h.timers.values()][0].ms,20000);
  options.busy=true; await tick(); assert.equal([...h.timers.values()][0].ms,5000);
  h.main.close(); assert.equal(h.timers.size,0); assert.equal(h.badges.at(-1),0);
});
test('conversation window titles are capped sanitized and fall back for foreign or empty titles', async () => {
  const h=await boot();
  for (const [url,title,expected] of [[h.main.url,'Project\n review','Project review'],[h.main.url,'x'.repeat(300),'x'.repeat(160)],[h.main.url,'  ','KeepHarness'],['https://example.com/','Foreign','KeepHarness']]) {
    h.main.url=url; const e=h.event(); h.main.webContents.emit('page-title-updated',e,title);
    assert.equal(h.main.title,expected); assert.ok(e.prevented);
  }
});
test('last app route survives restart and foreign or invalid stored routes are ignored', async () => {
  const h=await boot(); h.main.url+='?conversation=chat-123'; h.main.close();
  const saved=JSON.parse(fs.readFileSync(path.join(h.userData,'window-state.json')));
  assert.equal(saved.route,'/?conversation=chat-123');
  const next=await boot({home:h.home}); assert.equal(next.main.url,'http://127.0.0.1:8095/?conversation=chat-123');
  for (const route of ['https://evil.test/','//evil.test/','/\\evil.test/','/open?ticket=secret',42]) {
    fs.writeFileSync(path.join(h.userData,'window-state.json'),JSON.stringify({...saved,route}));
    const invalid=await boot({home:h.home}); assert.equal(invalid.main.url,'http://127.0.0.1:8095/');
  }
});

test('older manifests remain valid while malformed build dates are refused', async () => {
  const base={product:'keepharness',version:'0.16.0',commit:'a'.repeat(40),dirty:false};
  const old=await boot({manifest:JSON.stringify(base)});
  assert.equal(old.app.quits,0);
  await old.menu[0].submenu.find(item=>item.id==='about').click();
  assert.match(old.dialogs.at(-1).detail,/build date unavailable/);
  const invalid=await boot({manifest:JSON.stringify({...base,built_at:'untrusted date'})});
  assert.ok(invalid.app.quits); assert.equal(invalid.requests.length,0);
});

test('saved conversation route resumes after device enrollment completes', async () => {
  const first=await boot(); first.main.url+='?conversation=restored'; first.main.close();
  const link='http://127.0.0.1:8095/approve-device?nonce=abcdefghijklmnop';
  const h=await boot({home:first.home,noSession:true,enrollment:link});
  assert.equal(h.main.url,link);
  h.main.webContents.emit('did-finish-load');
  h.main.url='http://127.0.0.1:8095/'; h.main.webContents.emit('did-finish-load'); await settle();
  assert.equal(h.main.url,'http://127.0.0.1:8095/?conversation=restored');
});


test('app-origin blob downloads use the save dialog and unsafe schemes are refused', async () => {
  const h = await boot();
  for (const origin of ['http://127.0.0.1:18194', 'http://127.0.0.1:8095']) {
    const item = {getURL: () => `blob:${origin}/export-id`, getFilename: () => 'settings.json',
      setSaveDialogOptions(value) { this.save = value; }};
    const event = h.event(); h.session.emit('will-download', event, item, h.main.webContents);
    assert.equal(event.prevented, false);
    assert.equal(item.save.defaultPath, path.join(h.home, 'Downloads', 'settings.json'));
  }
  for (const url of ['blob:https://foreign.test/id', 'blob:http://127.0.0.1:9999/id',
    'blob:null/id', 'blob:file:///id', 'data:application/json,{}']) {
    const event = h.event();
    h.session.emit('will-download', event, {getURL: () => url}, h.main.webContents);
    assert.equal(event.prevented, true, url);
  }
  h.main.url = 'https://foreign.test/';
  const event = h.event();
  h.session.emit('will-download', event, {getURL: () => 'blob:http://127.0.0.1:8095/id'}, h.main.webContents);
  assert.equal(event.prevented, true);
});

test('busy indicators clear when polling fails', async () => {
  const options = {busy: true}; const h = await boot(options);
  assert.equal(h.main.progress, 2);
  options.status = 503;
  const timer = [...h.timers.values()][0]; h.timers.delete(timer); timer.fn(); await settle();
  assert.equal(h.main.progress, -1); assert.equal(h.badges.at(-1), 0);
  assert.equal([...h.timers.values()][0].ms, 10000);
});

test('busy indicators clear immediately when the backend exits', async () => {
  const h = await boot({busy: true, startBackend: true});
  assert.equal(h.main.progress, 2);
  h.children[0].exitCode = 1; h.children[0].emit('exit', 1, null);
  assert.equal(h.main.progress, -1); assert.equal(h.badges.at(-1), 0);
  const timer = [...h.timers.values()][0]; h.timers.delete(timer); timer.fn(); await settle();
  assert.equal(h.main.progress, -1); assert.equal(h.badges.at(-1), 0);
});

test('busy progress clears before the window is destroyed', async () => {
  const h = await boot({busy: true});
  assert.equal(h.main.progress, 2);
  h.main.once('closed', () => assert.equal(h.main.progress, -1));
  h.main.close();
  assert.equal(h.timers.size, 0); assert.equal(h.badges.at(-1), 0);
});

test('failed enrollment removes the pending route restore listener', async () => {
  const first = await boot(); first.main.url += '?conversation=restored'; first.main.close();
  const h = await boot({home: first.home, noSession: true, failEnrollment: true,
    enrollment: 'http://127.0.0.1:8095/approve-device?nonce=abcdefghijklmnop'});
  h.main.webContents.emit('did-finish-load'); await settle();
  assert.equal(h.main.webContents.listenerCount('did-finish-load'), 0);
  h.main.url = 'http://127.0.0.1:8095/'; h.main.webContents.emit('did-finish-load'); await settle();
  assert.equal(h.main.url, 'http://127.0.0.1:8095/');
});

const listening = (port, uid) => `0: 0100007F:${port.toString(16).toUpperCase().padStart(4,'0')} 00000000:0000 0A 00000000:00000000 00:00000000 00000000 ${uid} 0 1`;
const foreignHarnessTable = `header\n${listening(18194,1000)}\n${listening(8095,1001)}\n`;
const ownerCookie = request => request.headers?.cookie?.includes('keepharness-local=');
test('a harness port owned by another uid never receives the owner cookie on direct boot', async () => {
  const h = await boot({tcp: foreignHarnessTable});
  assert.ok(!h.requestDetails.some(ownerCookie));
  assert.ok(h.dialogs.some(d => /127\.0\.0\.1:8095 is not yours/.test(d.message)));
  assert.ok(h.app.quits); assert.equal(h.windows.filter(w => w.url).length, 0);
});
test('navigating to a harness port owned by another uid never sends the owner cookie', async () => {
  const options = {harnessOffline: true, tcp: foreignHarnessTable}; const h = await boot(options);
  assert.equal(h.main.url, 'http://127.0.0.1:18194/');
  options.harnessOffline = false;
  const e = h.event(); h.main.webContents.emit('will-navigate', e, 'http://127.0.0.1:8095/'); await settle();
  assert.ok(e.prevented); assert.ok(h.app.quits);
  assert.ok(!h.requestDetails.some(ownerCookie));
  assert.equal(h.main.url, 'http://127.0.0.1:18194/');
});
test('a failed sign-in is reported as such, not as a foreign product, and can be retried', async () => {
  const quitOptions = {failOpen: true}; const h = await boot(quitOptions);
  assert.ok(h.dialogs.some(d => /Could not sign in to the KeepHarness service/.test(d.message)));
  assert.ok(!h.dialogs.some(d => /not a KeepHarness service/.test(d.message)));
  assert.ok(h.app.quits); assert.equal(h.windows.filter(w => w.url).length, 0);
  const retry = {failOpen: true, onDialog: d => { retry.failOpen = false; return {response: 0}; }};
  const r = await boot(retry);
  assert.deepEqual(r.dialogs.map(d => d.buttons), [['Retry', 'Quit']]);
  assert.equal(r.app.quits, 0); assert.equal(r.main.url, 'http://127.0.0.1:8095/');
});
test('the enrollment route restore is single-use even when enrollment is refused', async () => {
  const first = await boot(); first.main.url += '?conversation=restored'; first.main.close();
  const h = await boot({home: first.home, noSession: true,
    enrollment: 'http://127.0.0.1:8095/approve-device?nonce=abcdefghijklmnop'});
  h.main.webContents.emit('did-finish-load'); await settle();
  h.main.url = 'http://127.0.0.1:8095/approve-device'; h.main.webContents.emit('did-finish-load'); await settle();
  assert.equal(h.main.webContents.listenerCount('did-finish-load'), 0);
  h.main.url = 'http://127.0.0.1:8095/'; h.main.webContents.emit('did-finish-load'); await settle();
  assert.equal(h.main.url, 'http://127.0.0.1:8095/');
});
