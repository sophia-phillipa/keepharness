// The packaged launcher and installer, run against a fake package and a throwaway HOME.
const test = require('node:test');
const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const root = fs.mkdtempSync(path.join(os.tmpdir(), 'keepharness-package-'));
test.after(() => { execFileSync('chmod',['-R','u+w',root]); fs.rmSync(root, { recursive: true, force: true }); });

// A package folder as scripts/package-desktop-linux.sh lays it out, with a fake keepharness-bin
// that reports the Python environment, the cache folders and the arguments it was started with.
function fakePackage(name, version) {
  const dir = path.join(root, name);
  const copy = (from, to, mode) => {
    fs.mkdirSync(path.dirname(path.join(dir, to)), { recursive: true });
    fs.copyFileSync(path.join(__dirname, 'linux', from), path.join(dir, to));
    if (mode) fs.chmodSync(path.join(dir, to), mode);
  };
  copy('launcher.sh', 'keepharness', 0o755);
  copy('install-desktop-linux.sh', 'install-desktop-linux.sh', 0o755);
  copy('install_desktop_linux.py', 'install_desktop_linux.py');
  copy('keepharness.desktop', 'share/applications/keepharness.desktop');
  fs.mkdirSync(path.join(dir, 'share/icons/hicolor/256x256/apps'), { recursive: true });
  fs.writeFileSync(path.join(dir, 'share/icons/hicolor/256x256/apps/keepharness.png'), '');
  fs.writeFileSync(path.join(dir, 'VERSION'), version + '\n');
  fs.writeFileSync(
    path.join(dir, 'keepharness-bin'),
    '#!/bin/sh\nprintf "%s|%s|%s|%s\\n" "$KEEPHARNESS_PYTHON" "$XDG_CACHE_HOME" "$KEEPHARNESS_HOST_XDG_CACHE_HOME" "$*"\n',
    { mode: 0o755 },
  );
  fs.writeFileSync(path.join(dir, 'build-manifest.json'), JSON.stringify({version, product:'keepharness', dirty:false, commit:'a'.repeat(40)}));
  writeSums(dir);
  return dir;
}
function writeSums(dir) {
  const sums = [];
  function walk(folder) {
    for (const entry of fs.readdirSync(folder, {withFileTypes:true})) {
      const file = path.join(folder, entry.name);
      if (entry.isDirectory()) walk(file);
      else if (entry.name !== 'SHA256SUMS') sums.push(require('node:crypto').createHash('sha256').update(fs.readFileSync(file)).digest('hex') + '  ' + path.relative(dir,file));
    }
  }
  walk(dir); fs.writeFileSync(path.join(dir,'SHA256SUMS'), sums.sort().join('\n')+'\n');
}
function newHome(name) {
  const dir = path.join(root, name);
  fs.mkdirSync(dir);
  return dir;
}
function run(file, args, env) {
  return execFileSync(file, args, { cwd: root, env: { PATH: process.env.PATH, ...env }, encoding: 'utf8' }).trim();
}
function installedVenv(home) {
  const python = path.join(home, '.local/share/keepharness/venv/bin/python');
  fs.mkdirSync(path.dirname(python), { recursive: true });
  fs.writeFileSync(python, '#!/bin/sh\n', { mode: 0o755 });
  return python;
}

test('the launcher uses the environment install.sh made, a private cache and forwards the arguments', () => {
  const home = newHome('launch-home');
  const python = installedVenv(home);
  const launcher = path.join(fakePackage('launch', '1.0.0'), 'keepharness');
  const out = run(launcher, ['--flag', 'x'], { HOME: home, XDG_CACHE_HOME: '/host/cache' });
  const cache = path.join(home, '.config/KeepHarness/xdg-cache');
  assert.equal(out, `${python}|${cache}|/host/cache|--flag x`);
  assert.equal(fs.statSync(cache).mode & 0o777, 0o700);
});

test('KEEPHARNESS_PYTHON wins, KEEPHARNESS_VENV moves the search, and no environment leaves it unset', () => {
  const home = newHome('python-home');
  const launcher = path.join(fakePackage('python', '1.0.0'), 'keepharness');
  const first = (out) => out.split('|')[0];
  assert.equal(first(run(launcher, [], { HOME: home })), '');
  installedVenv(home);
  assert.equal(first(run(launcher, [], { HOME: home, KEEPHARNESS_PYTHON: '/custom/python' })), '/custom/python');
  const elsewhere = path.join(root, 'elsewhere');
  fs.mkdirSync(path.join(elsewhere, 'bin'), { recursive: true });
  fs.writeFileSync(path.join(elsewhere, 'bin/python'), '#!/bin/sh\n', { mode: 0o755 });
  assert.equal(first(run(launcher, [], { HOME: home, KEEPHARNESS_VENV: elsewhere })), path.join(elsewhere, 'bin/python'));
});

test('the installer copies the package, writes the menu entry and keeps the previous version', () => {
  const home = newHome('install-home');
  const entry = path.join(home, '.local/share/applications/keepharness.desktop');
  const first = path.join(home, '.local/opt/keepharness-1.0.0');
  run(path.join(fakePackage('first', '1.0.0'), 'install-desktop-linux.sh'), [], { HOME: home });
  assert.ok(fs.existsSync(path.join(first, 'keepharness-bin')));
  const text = fs.readFileSync(entry, 'utf8');
  assert.match(text, new RegExp(`^Exec="${home}/.local/opt/keepharness/current/keepharness"$`, 'm'));
  assert.match(text, new RegExp(`^Icon=${home}/.local/opt/keepharness/current/share/icons/hicolor/256x256/apps/keepharness\\.png$`, 'm'));
  assert.match(text, /^Name=KeepHarness$/m);
  assert.ok(!fs.existsSync(path.join(first, 'previous.desktop')));

  const second = path.join(home, '.local/opt/keepharness-1.1.0');
  run(path.join(fakePackage('second', '1.1.0'), 'install-desktop-linux.sh'), [], { HOME: home });
  assert.equal(fs.realpathSync(path.join(home,'.local/opt/keepharness/previous')), first);
  assert.match(fs.readFileSync(entry, 'utf8'), new RegExp(`^Exec="${home}/.local/opt/keepharness/current/keepharness"$`, 'm'));
  assert.ok(fs.existsSync(first), 'the older version stays installed');
});

test('the installer never overwrites a folder of the same version', () => {
  const home = newHome('twice-home');
  const installer = path.join(fakePackage('twice', '2.0.0'), 'install-desktop-linux.sh');
  const target = path.join(home, '.local/opt/keepharness-2.0.0');
  run(installer, [], { HOME: home });
  fs.writeFileSync(path.join(target, 'marker'), 'mine');
  assert.throws(() => run(installer, [], { HOME: home }), (error) => /mismatch|already installed/.test(error.stderr));
  assert.equal(fs.readFileSync(path.join(target, 'marker'), 'utf8'), 'mine');
  assert.deepEqual(fs.readdirSync(path.join(home, '.local/opt')), ['.keepharness.lock', 'keepharness', 'keepharness-2.0.0'], 'no half-installed leftovers');
});

function fixture(version = '0.15.0') {
  const home = fs.mkdtempSync(path.join(root, 'home-'));
  const pkg = fakePackage(path.basename(home) + '-pkg', version);
  const installer = path.join(pkg, 'install-desktop-linux.sh');
  const invoke = (...args) => run(installer, args, { HOME: home });
  const opt = path.join(home, '.local/opt');
  const entry = path.join(home, '.local/share/applications/keepharness.desktop');
  return {home, pkg, installer, invoke, opt, entry};
}
function put(file, content = 'sentinel') {
  fs.mkdirSync(path.dirname(file), {recursive:true}); fs.writeFileSync(file, content);
}
function installNext(f, v) {
  const pkg = fakePackage(path.basename(f.home) + '-' + v, v);
  run(path.join(pkg, 'install-desktop-linux.sh'), [], {HOME:f.home});
}
function current(f, name = 'current') { return fs.realpathSync(path.join(f.opt, 'keepharness', name)); }

test('uninstall_refuses_empty_root_or_relative_home', () => {
  const f = fixture();
  for (const home of ['', '/', 'relative']) assert.throws(() => run(f.installer, ['--uninstall','--yes'], {HOME:home}), /HOME|Invalid HOME/);
});
test('uninstall_works_with_home_behind_symlink', () => {
  const f = fixture(); f.invoke(); const alias = f.home + '-alias'; fs.symlinkSync(f.home, alias);
  run(f.installer, ['--uninstall','--yes'], {HOME:alias}); assert.ok(!fs.existsSync(f.entry));
});
test('uninstall_refuses_symlinked_config_keepharness_target', () => {
  const f = fixture(); f.invoke(); const outside = f.home + '-outside'; fs.mkdirSync(outside); put(path.join(outside,'sentinel'));
  fs.mkdirSync(path.join(f.home,'.config'), {recursive:true}); fs.symlinkSync(outside,path.join(f.home,'.config/KeepHarness'));
  assert.throws(() => f.invoke('--uninstall','--yes'), /symlink/i); assert.ok(fs.existsSync(f.entry)); assert.ok(fs.existsSync(path.join(outside,'sentinel')));
});
test('uninstall_refuses_symlinked_version_dir', () => {
  const f = fixture(); f.invoke(); fs.symlinkSync(f.pkg,path.join(f.opt,'keepharness-9.0.0'));
  assert.throws(() => f.invoke('--uninstall','--yes'), /symlink/i); assert.ok(fs.existsSync(f.entry));
});
test('current_unlinked_target_untouched', () => {
  const f = fixture(); f.invoke(); const link = path.join(f.opt,'keepharness/current'); fs.unlinkSync(link); fs.symlinkSync(f.pkg,link);
  f.invoke('--uninstall','--yes'); assert.ok(fs.existsSync(f.pkg)); assert.ok(!fs.existsSync(link));
});
test('uninstall_skips_unmarked_keepharness_dir', () => {
  const f = fixture(); f.invoke(); const extra = path.join(f.opt,'keepharness-9.0.0/precious'); put(extra);
  f.invoke('--uninstall','--yes'); assert.equal(fs.readFileSync(extra,'utf8'),'sentinel');
});
for (const [name, exec] of [['foreign','/foreign'],['prefix_lookalike', '/keepharness/current/keepharness-foreign']]) {
  test(`uninstall_skips_desktop_entry_with_${name}_exec`, () => {
    const f=fixture(); f.invoke(); const content=`Exec="${f.opt}${exec}"\n`; put(f.entry,content);
    f.invoke('--uninstall','--yes'); assert.equal(fs.readFileSync(f.entry,'utf8'),content);
  });
}
test('uninstall_case_insensitive_alias_refused', () => {
  const f=fixture(); f.invoke(); fs.mkdirSync(path.join(f.home,'.config/keepharness'),{recursive:true});
  fs.symlinkSync('keepharness',path.join(f.home,'.config/KeepHarness'));
  assert.throws(() => f.invoke('--uninstall','--yes'), /share an inode/); assert.ok(fs.existsSync(f.entry));
});
test('uninstall_refused_while_running', () => {
  const f=fixture(); f.invoke(); fs.mkdirSync(path.join(f.home,'.config/KeepHarness'),{recursive:true});
  fs.symlinkSync(`${os.hostname()}-${process.pid}`,path.join(f.home,'.config/KeepHarness/SingletonLock'));
  assert.throws(() => f.invoke('--uninstall','--yes'), /Desktop is running/); assert.ok(fs.existsSync(f.entry));
});
test('uninstall_python_product_byte_identical', () => {
  const f=fixture(); f.invoke(); const keep=['.config/keepharness/settings.json','.local/share/keepharness/local.key','.config/systemd/user/keepharness.service'];
  for (const p of keep) put(path.join(f.home,p),'\0private\xff');
  put(path.join(f.home,'.config/KeepHarness/cache')); f.invoke('--uninstall','--yes');
  for (const p of keep) assert.equal(fs.readFileSync(path.join(f.home,p),'utf8'),'\0private\xff');
  assert.ok(!fs.existsSync(f.entry)); assert.ok(!fs.existsSync(path.join(f.opt,'keepharness'))); assert.ok(!fs.existsSync(path.join(f.home,'.config/KeepHarness')));
});
test('prune_after_downgrade_keeps_current', () => {
  const f=fixture('0.17.0'); f.invoke(); installNext(f,'0.18.0'); installNext(f,'0.15.0');
  assert.equal(path.basename(current(f)),'keepharness-0.15.0'); assert.equal(path.basename(current(f,'previous')),'keepharness-0.18.0');
  assert.ok(!fs.existsSync(path.join(f.opt,'keepharness-0.17.0')));
});
test('prune_ignores_installing_and_failed_copy', () => {
  const f=fixture(); f.invoke(); for (const suffix of ['.installing','.failed-copy-123']) put(path.join(f.opt,'keepharness-1.0.0'+suffix,'precious'));
  installNext(f,'0.15.1'); installNext(f,'0.15.2');
  for (const suffix of ['.installing','.failed-copy-123']) assert.ok(fs.existsSync(path.join(f.opt,'keepharness-1.0.0'+suffix,'precious')));
});
test('rollback_single_version_refused', () => {const f=fixture(); f.invoke(); assert.throws(()=>f.invoke('--rollback'), /Missing link/);});
test('rollback_twice_toggles', () => {
  const f=fixture(); f.invoke(); installNext(f,'0.15.1'); f.invoke('--rollback'); assert.equal(path.basename(current(f)),'keepharness-0.15.0');
  f.invoke('--rollback'); assert.equal(path.basename(current(f)),'keepharness-0.15.1');
});
test('legacy_manifest_without_product_prunable', () => {
  const f=fixture(); f.invoke(); put(path.join(f.opt,'keepharness-0.15.0/build-manifest.json'),'{"version":"0.15.0"}');
  installNext(f,'0.15.1'); installNext(f,'0.15.2'); assert.ok(!fs.existsSync(path.join(f.opt,'keepharness-0.15.0')));
});
test('desktop_install_removes_exact_browser_entry_only', () => {
  for (const exact of [true,false]) {
    const f=fixture(); const browser=path.join(f.home,'.local/share/applications/keepharness-browser.desktop');
    put(browser,`Exec="${f.home}/.local/bin/keepharness-open"${exact?'':' --foreign'}\nIcon=utilities-terminal\n`);
    f.invoke(); assert.equal(fs.existsSync(browser),!exact);
  }
});
test('tampered_file_refused_and_identical_reinstall_noop', () => {
  const f=fixture(); f.invoke(); const before=fs.statSync(path.join(f.opt,'keepharness/current')).mtimeMs; f.invoke();
  assert.equal(fs.statSync(path.join(f.opt,'keepharness/current')).mtimeMs,before);
  put(path.join(f.pkg,'keepharness-bin'),'tampered'); assert.throws(()=>f.invoke(), /SHA256SUMS mismatch/);
});

const {spawn} = require('node:child_process');
const delay = (ms) => new Promise(resolve => setTimeout(resolve,ms));
async function waitFor(predicate) {
  for(let i=0;i<500;i++){if(predicate())return;await delay(20);} assert.fail('Timed out waiting for child');
}
async function stopChild(child) {
  if(child.exitCode!==null || child.signalCode!==null)return;
  const exited = new Promise(resolve=>child.once('exit',resolve)); child.kill('SIGTERM'); await exited;
}
test('prune_skips_running_version', async () => {
  const f=fixture(); f.invoke(); const binary=path.join(f.opt,'keepharness-0.15.0/keepharness-bin');
  const child=spawn('/usr/bin/sleep',['30'],{argv0:binary,stdio:'ignore'});
  try {await delay(60); installNext(f,'0.15.1');installNext(f,'0.15.2');assert.ok(fs.existsSync(binary));assert.throws(()=>f.invoke('--uninstall','--yes'), /Desktop is running/);}
  finally {await stopChild(child);}
});
test('concurrent_install_second_refused', async () => {
  const f=fixture();f.invoke();const ready=path.join(f.home,'locked');
  const child=spawn('python3',['-c', 'import fcntl,sys,time; f=open(sys.argv[1],"w"); fcntl.flock(f,fcntl.LOCK_EX); open(sys.argv[2],"w").close(); time.sleep(30)',path.join(f.opt,'.keepharness.lock'),ready],{stdio:'ignore'});
  try {await waitFor(()=>fs.existsSync(ready));assert.throws(()=>f.invoke(),/lock/);assert.throws(()=>f.invoke('--uninstall','--yes'),/lock/);assert.ok(fs.existsSync(f.entry));}
  finally {await stopChild(child);}
});
test('interrupted_install_stage_cleaned_under_lock', async () => {
  const f=fixture();f.invoke();const stage=path.join(f.opt,'.keepharness-Ab123Z');put(path.join(stage,'partial'));
  const ready=path.join(f.home,'locked');
  const child=spawn('python3',['-c','import fcntl,sys,time; f=open(sys.argv[1],"w"); fcntl.flock(f,fcntl.LOCK_EX); open(sys.argv[2],"w").close(); time.sleep(30)',path.join(f.opt,'.keepharness.lock'),ready],{stdio:'ignore'});
  try {await waitFor(()=>fs.existsSync(ready)); assert.throws(()=>f.invoke(),/lock/); assert.ok(fs.existsSync(stage));}
  finally {await stopChild(child);}
  f.invoke(); assert.ok(!fs.existsSync(stage));
});
test('uninstall_dry_run_and_noninteractive_confirmation', () => {
  const f=fixture(); f.invoke(); assert.match(f.invoke('--uninstall','--dry-run'),/Remove:/);assert.ok(fs.existsSync(f.entry));
  assert.throws(()=>f.invoke('--uninstall'),/--yes/);assert.ok(fs.existsSync(f.entry));
});

test('dirty_tree_refused', () => {
  const f=fixture(); const checkout=path.join(f.home,'dirty'); fs.mkdirSync(path.join(checkout,'scripts'),{recursive:true});
  fs.copyFileSync(path.join(__dirname,'../scripts/package-desktop-linux.sh'),path.join(checkout,'scripts/package-desktop-linux.sh'));
  execFileSync('git',['init','-q',checkout]);
  assert.throws(()=>execFileSync('bash',[path.join(checkout,'scripts/package-desktop-linux.sh')],{
    env:{...process.env,HOME:f.home,KEEPHARNESS_ELECTRON_DIST:'/missing'},encoding:'utf8',stdio:'pipe'
  }), error=>/dirty/i.test(error.stderr));
});

test('packaged_smoke_launches_under_xvfb', async (t) => {
  const f=fixture(); const checkout=path.join(f.home,'build'); fs.mkdirSync(checkout);
  // Export HEAD, then overlay the actual working tree sources being validated. The
  // snapshot gets its own git metadata; the real checkout must remain dirty/refused.
  execFileSync('bash',['-c','git archive HEAD | tar -x -C "$1"','export',checkout],{cwd:path.join(__dirname,'..')});
  const files=execFileSync('git',['ls-files','--cached','--others','--exclude-standard','desktop','scripts','control','docs'],{cwd:path.join(__dirname,'..'),encoding:'utf8'}).trim().split('\n');
  for(const file of new Set(files)) {
    const source=path.join(__dirname,'..',file); if(!fs.statSync(source).isFile())continue;
    const dest=path.join(checkout,file);fs.mkdirSync(path.dirname(dest),{recursive:true});fs.copyFileSync(source,dest);
  }
  fs.symlinkSync(path.join(__dirname,'node_modules'),path.join(checkout,'desktop/node_modules'));
  execFileSync('git',['init','-q',checkout]);
  execFileSync('git',['-C',checkout,'add','.']);
  execFileSync('git',['-C',checkout,'-c','user.name=Package test','-c','user.email=package@test.invalid','commit','-qm','test: snapshot S3 package sources']);
  const dist=process.env.KEEPHARNESS_ELECTRON_DIST || path.join(__dirname,'node_modules/electron/dist');
  const archiveName = `electron-v${require('./package.json').devDependencies.electron}-linux-x64.zip`;
  const cache = path.join(os.homedir(), '.cache/electron');
  const archive = process.env.KEEPHARNESS_ELECTRON_ZIP || fs.readdirSync(cache).map(dir => path.join(cache, dir, archiveName)).find(file => fs.existsSync(file));
  execFileSync('bash',[path.join(checkout,'scripts/package-desktop-linux.sh')],{cwd:checkout,env:{...process.env,HOME:f.home,KEEPHARNESS_ELECTRON_ZIP:archive,KEEPHARNESS_ELECTRON_DIST:dist},encoding:'utf8',stdio:'pipe'});
  const pkg=path.join(checkout,'dist',fs.readdirSync(path.join(checkout,'dist'))[0]);
  assert.ok(fs.existsSync(path.join(pkg,'resources/app.asar'))); assert.ok(!fs.existsSync(path.join(pkg,'resources/app'))); assert.ok(!fs.existsSync(path.join(pkg,'resources/default_app.asar')));
  const {getCurrentFuseWire,FuseV1Options,FuseState}=await import('@electron/fuses');
  const wire=await getCurrentFuseWire(path.join(pkg,'keepharness-bin'));
  await t.test('fuse_run_as_node_disabled',()=>assert.equal(wire[FuseV1Options.RunAsNode],FuseState.DISABLE));
  await t.test('node_options_and_inspect_ignored',()=>{
    assert.equal(wire[FuseV1Options.EnableNodeOptionsEnvironmentVariable],FuseState.DISABLE);
    assert.equal(wire[FuseV1Options.EnableNodeCliInspectArguments],FuseState.DISABLE);
  });
  assert.equal(wire[FuseV1Options.EnableEmbeddedAsarIntegrityValidation],FuseState.ENABLE);
  assert.equal(wire[FuseV1Options.OnlyLoadAppFromAsar],FuseState.ENABLE);
  run(path.join(pkg,'install-desktop-linux.sh'),[],{HOME:f.home});
  const result = execFileSync(process.execPath, [path.join(__dirname, '../scripts/smoke-desktop-package.mjs'),
    '--executable', path.join(f.opt, 'keepharness/current/keepharness'), '--scratch', root],
    {env:process.env, encoding:'utf8', timeout:30000});
  const report = JSON.parse(result);
  const manifest = JSON.parse(fs.readFileSync(path.join(pkg, 'build-manifest.json'), 'utf8'));
  assert.equal(report.version, manifest.version);
  assert.equal(report.commit, manifest.commit);
  assert.match(manifest.built_at, /^\d{4}-\d{2}-\d{2}T/);
  assert.equal(report.asar, true);

});

test('prune_preserves_semver_installing_suffix_and_reinstall_is_noop', () => {
 const f=fixture(); f.invoke(); const staged=path.join(f.opt,'keepharness-1.2.3+build.installing');
 fs.cpSync(path.join(f.opt,'keepharness-0.15.0'),staged,{recursive:true});put(path.join(staged,'VERSION'),'1.2.3+build.installing\n');put(path.join(staged,'build-manifest.json'),'{"version":"1.2.3+build.installing"}');
 const before=fs.lstatSync(f.entry).ino;const link=fs.lstatSync(path.join(f.opt,'keepharness/current')).ino;
 f.invoke();assert.equal(fs.lstatSync(f.entry).ino,before);assert.equal(fs.lstatSync(path.join(f.opt,'keepharness/current')).ino,link);
 installNext(f,'0.15.1');installNext(f,'0.15.2');assert.ok(fs.existsSync(staged));
 assert.match(fs.readFileSync(f.entry,'utf8'),/^StartupWMClass=keepharness$/m);
});
test('uninstall_unreadable_subtree_refuses_before_any_delete', () => {
 const f=fixture();f.invoke();const data=path.join(f.home,'.config/KeepHarness/private');put(path.join(data,'secret'));fs.chmodSync(data,0);
 try {assert.throws(()=>f.invoke('--uninstall','--yes'), /Permission denied/);assert.ok(fs.existsSync(f.entry));assert.ok(fs.existsSync(path.join(f.opt,'keepharness-0.15.0')));}
 finally {fs.chmodSync(data,0o700);}
});

test('uninstall_mount_root_refused_before_any_delete', () => {
 const f=fixture();f.invoke();const mount=path.join(f.home,'.config/KeepHarness');put(path.join(mount,'precious'));
 const script=`import pathlib,sys,runpy\noriginal=pathlib.Path.read_text;mount=sys.argv[2]\npathlib.Path.read_text=lambda p,*a,**kw: ('1 0 0:1 / '+mount+' rw - tmpfs tmpfs rw\\n') if str(p)=='/proc/self/mountinfo' else original(p,*a,**kw)\nhelper=sys.argv[1];sys.argv=[helper,'--source',sys.argv[3],'--uninstall','--yes'];runpy.run_path(helper,run_name='__main__')`;
 assert.throws(()=>run('python3',['-c',script,path.join(f.pkg,'install_desktop_linux.py'),mount,f.pkg],{HOME:f.home}),/Mounted deletion root/);
 assert.ok(fs.existsSync(f.entry));assert.ok(fs.existsSync(path.join(f.opt,'keepharness-0.15.0')));
});
test('uninstall_reports_rm_failure_and_refuses_symlinked_parent', () => {
 const f=fixture();f.invoke();const commands=path.join(f.home,'commands');put(path.join(commands,'rm'),'#!/bin/sh\nexit 42\n');fs.chmodSync(path.join(commands,'rm'),0o755);
 assert.throws(()=>run(f.installer,['--uninstall','--yes'],{HOME:f.home,PATH:commands+':'+process.env.PATH}),/42/);
 assert.ok(fs.existsSync(f.entry));
 fs.renameSync(path.join(f.home,'.config'),path.join(f.home,'other-config'));fs.symlinkSync(path.join(f.home,'other-config'),path.join(f.home,'.config'));
 assert.throws(()=>f.invoke('--uninstall','--yes'),/symlink/);assert.ok(fs.existsSync(f.entry));
});

test('install_refuses_protected_stage_version_suffix', () => {
 const f=fixture('1.2.3+build.installing');assert.throws(()=>f.invoke(),/Invalid package version/);
 assert.ok(!fs.existsSync(path.join(f.opt,'keepharness-1.2.3+build.installing')));
});


test('legacy_upgrade_keeps_previous_and_rollback_works', () => {
  for (const aliased of [false, true]) {
    const f = fixture();
    const old = path.join(f.opt, 'keepharness-0.14.0');
    fs.cpSync(fakePackage(path.basename(f.home) + '-legacy', '0.14.0'), old, {recursive:true});
    put(path.join(old, 'build-manifest.json'), '{"version":"0.14.0"}');
    const home = aliased ? f.home + '-alias' : f.home;
    if (aliased) fs.symlinkSync(f.home, home);
    put(f.entry, `Exec="${home}/.local/opt/keepharness-0.14.0/keepharness"\n`);
    run(f.installer, [], {HOME:home});
    assert.equal(current(f, 'previous'), old);
    assert.ok(fs.existsSync(old));
    f.invoke('--rollback'); assert.equal(current(f), old);
    f.invoke('--rollback'); assert.equal(current(f), path.join(f.opt, 'keepharness-0.15.0'));
  }
});
test('package_outside_git_refused', () => {
  const f = fixture(); const checkout = path.join(f.home, 'export');
  const script = path.join(checkout, 'scripts/package-desktop-linux.sh');
  put(script, fs.readFileSync(path.join(__dirname, '../scripts/package-desktop-linux.sh')));
  // Fail visibly if packaging proceeds past its git preflight.
  put(path.join(checkout, 'scripts/verify_electron.py'), 'raise RuntimeError("verification reached")');
  assert.throws(() => run('bash', [script], {HOME:f.home}), error => {
    assert.match(error.stderr, /not the top of its own Git work tree/);
    assert.doesNotMatch(error.stderr, /verification reached/);
    return true;
  });
  assert.ok(!fs.existsSync(path.join(checkout, 'dist')));
});
test('installer_refuses_bad_commit', () => {
  for (const commit of ['', 'a'.repeat(39), 'g'.repeat(40), 'a'.repeat(41), null, 123]) {
    const f = fixture();
    put(path.join(f.pkg, 'build-manifest.json'), JSON.stringify({version:'0.15.0',product:'keepharness',dirty:false,commit}));
    writeSums(f.pkg);
    assert.throws(() => f.invoke(), /Invalid package provenance/);
    assert.ok(!fs.existsSync(path.join(f.opt, 'keepharness-0.15.0')));
  }
});
test('install_via_symlinked_home_ancestor', () => {
  const f = fixture(); const layout = path.join(f.home, 'atomic');
  const actual = path.join(layout, 'var/home'); fs.mkdirSync(actual, {recursive:true});
  fs.symlinkSync('var/home', path.join(layout, 'home'));
  fs.renameSync(f.pkg, path.join(actual, 'package'));
  run(path.join(layout, 'home/package/install-desktop-linux.sh'), [], {HOME:f.home});
  assert.equal(current(f), path.join(f.opt, 'keepharness-0.15.0'));
});
test('launcher_apparmor_hint', () => {
  const f = fixture(); const launcher = path.join(f.pkg, 'keepharness');
  put(path.join(f.pkg, 'keepharness-bin'), '#!/bin/sh\nprintf "EXEC:%s\\n" "$*" >&2\n');
  const script = `function [ { if [[ "$1" == -r && "$2" == /proc/sys/kernel/apparmor_restrict_unprivileged_userns ]]; then return 0; fi; builtin [ "$@"; }
function cat { if [[ "$1" == /proc/sys/kernel/apparmor_restrict_unprivileged_userns ]]; then echo 1; else command cat "$@"; fi; }
source "$0" --flag`;
  const result = require('node:child_process').spawnSync('bash', ['-c', script, launcher], {env:{PATH:process.env.PATH, HOME:f.home},encoding:'utf8'});
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stderr, /^Ubuntu AppArmor[^\n]+\nEXEC:--flag\n$/);
  assert.doesNotMatch(result.stdout + result.stderr + fs.readFileSync(launcher, 'utf8'), /--no-sandbox/);
});
test('desktop_install_skips_symlinked_browser_entry', () => {
  const f = fixture(); const target = path.join(f.home, 'browser-target');
  const content = `Exec="${f.home}/.local/bin/keepharness-open"\nIcon=utilities-terminal\n`;
  put(target, content); fs.mkdirSync(path.dirname(f.entry), {recursive:true});
  const browser = path.join(path.dirname(f.entry), 'keepharness-browser.desktop'); fs.symlinkSync(target, browser);
  assert.match(f.invoke(), /skip.*symlink/i);
  assert.equal(fs.readlinkSync(browser), target); assert.equal(fs.readFileSync(target, 'utf8'), content);
});
test('uninstall_leaves_browser_entry', () => {
  for (const symlink of [false, true]) {
    const f = fixture(); f.invoke();
    const browser = path.join(path.dirname(f.entry), 'keepharness-browser.desktop');
    const content = `Exec="${f.home}/.local/bin/keepharness-open"\nIcon=utilities-terminal\n`;
    if (symlink) { const target = path.join(f.home, 'browser-target'); put(target, content); fs.symlinkSync(target, browser); }
    else put(browser, content);
    assert.match(f.invoke('--uninstall', '--yes'), /The KeepHarness server and keepharness-open launcher are unchanged/);
    assert.equal(fs.lstatSync(browser).isSymbolicLink(), symlink);
    assert.equal(fs.readFileSync(browser, 'utf8'), content);
  }
});
test('tree_refuses_changed_parent_root', () => {
  const f = fixture(); const original = path.join(f.home, 'original');
  const moved = path.join(f.home, 'moved'); put(path.join(original, 'candidate/precious'));
  fs.renameSync(original, moved); fs.symlinkSync(moved, original);
  const script = `import runpy,sys,pathlib\nm=runpy.run_path(sys.argv[1]);m['tree'](pathlib.Path(sys.argv[2]))`;
  assert.throws(() => run('python3', ['-c',script,path.join(f.pkg,'install_desktop_linux.py'),path.join(original,'candidate')], {HOME:f.home}), /Outside expected parent/);
  assert.equal(fs.readFileSync(path.join(moved, 'candidate/precious'), 'utf8'), 'sentinel');
});
test('refusal_assertions_require_diagnostics', () => {
  const source = fs.readFileSync(__filename, 'utf8');
  assert.doesNotMatch(source, /assert\.throws\([^\n]*?\{HOME:home\}\)\);/);
  assert.doesNotMatch(source, /assert\.throws\(\s*\(\)\s*=>\s*f\.invoke\([^()]*\)\);/);
});


test('legacy_same_version_install_creates_current', () => {
  const f = fixture(); const target = path.join(f.opt, 'keepharness-0.15.0');
  fs.cpSync(f.pkg, target, {recursive:true});
  put(f.entry, `Exec="${target}/keepharness"\n`);
  f.invoke();
  assert.equal(current(f), target);
});

test('packaged_smoke_rejects_mismatched_runtime_identity', async () => {
  const { verifyBuildReport } = await import('../scripts/smoke-desktop-package.mjs');
  const manifest = {version:'0.16.0', commit:'a'.repeat(40)};
  const log = (build) => 'start app.asar\nBuild: ' + JSON.stringify(build) + '\nPython could not start';
  assert.deepEqual(verifyBuildReport(log(manifest), manifest), {...manifest, asar:true});
  assert.throws(() => verifyBuildReport(log({...manifest, version:'0.15.0'}), manifest), /version/);
  assert.throws(() => verifyBuildReport(log({...manifest, commit:'b'.repeat(40)}), manifest), /commit/);
  assert.throws(() => verifyBuildReport('start app.asar', manifest), /runtime build/);
});
