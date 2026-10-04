#!/usr/bin/env node
// Run an installed package without touching the user's profile or display.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { spawn, execFileSync } from 'node:child_process';
import { parseArgs } from 'node:util';
import { pathToFileURL } from 'node:url';

export function verifyBuildReport(log, manifest) {
  const line = log.match(/Build: (\{[^\n]+\})/);
  assert.ok(line, 'Missing runtime build report');
  const reported = JSON.parse(line[1]);
  assert.equal(reported.version, manifest.version, 'Runtime version differs from manifest');
  assert.equal(reported.commit, manifest.commit, 'Runtime commit differs from manifest');
  assert.match(log, /app\.asar/);
  return {version:reported.version, commit:reported.commit, asar:true};
}

async function smoke(executable, scratch) {
  assert.ok(path.isAbsolute(executable), '--executable must be absolute');
  assert.ok(path.isAbsolute(scratch), '--scratch must be absolute');
  const manifest = JSON.parse(fs.readFileSync(path.join(path.dirname(executable), 'build-manifest.json'), 'utf8'));
  assert.equal(manifest.product, 'keepharness');
  assert.match(manifest.commit, /^[a-f0-9]{40}$/);
  const home = fs.mkdtempSync(path.join(scratch, 'smoke-'));
  const runtime = path.join(home, 'runtime');
  fs.mkdirSync(runtime, {mode:0o700});
  const env = {...process.env, HOME:home, TMPDIR:scratch, XDG_RUNTIME_DIR:runtime,
    XDG_CONFIG_HOME:path.join(home, '.config'), XDG_DATA_HOME:path.join(home, '.local/share'),
    XDG_CACHE_HOME:path.join(home, '.cache'), XDG_SESSION_TYPE:'x11',
    KEEPHARNESS_PYTHON:'/nonexistent-python', KEEPHARNESS_ADMIN_PORT:'0', KEEPHARNESS_PORT:'0',
    ELECTRON_RUN_AS_NODE:'1', NODE_OPTIONS:'--require=/nonexistent-node-module'};
  delete env.DISPLAY;
  delete env.WAYLAND_DISPLAY;
  // Disabling GLX avoids host NVIDIA drivers crashing Xvfb before Electron starts.
  const child = spawn('xvfb-run', ['-a', '-s', '-screen 0 1280x1024x24 -extension GLX',
    executable, '--inspect=0', '--ozone-platform=x11'], {env, detached:true, stdio:['ignore','pipe','pipe']});
  let output = '', launchError;
  child.on('error', error => { launchError = error; });
  child.stdout.on('data', data => { output = (output + data).slice(-16000); });
  child.stderr.on('data', data => { output = (output + data).slice(-16000); });
  const logFile = path.join(home, '.config/KeepHarness/logs/main.log');
  const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
  try {
    const deadline = Date.now() + 15000;
    while (Date.now() < deadline) {
      if (launchError) throw launchError;
      const log = fs.existsSync(logFile) ? fs.readFileSync(logFile, 'utf8') : '';
      if (log.includes('Python could not start')) {
        assert.doesNotMatch(output, /Debugger listening|Cannot find module/);
        return verifyBuildReport(log, manifest);
      }
      if (child.exitCode !== null) throw new Error(`Package exited early: ${output}`);
      await pause(50);
    }
    throw new Error(`Timed out waiting for packaged startup: ${output}`);
  } finally {
    // Only the process group created above belongs to this invocation.
    if (child.pid) {
      const signal = value => {
        try { process.kill(-child.pid, value); } catch (error) { if (error.code !== 'ESRCH') throw error; }
      };
      signal('SIGTERM');
      for (let i = 0; i < 20 && child.exitCode === null && child.signalCode === null; i++) await pause(50);
      signal('SIGKILL');
    }
    child.stdout.destroy();
    child.stderr.destroy();
    execFileSync('chmod', ['-R', 'u+w', home]);
    fs.rmSync(home, {recursive:true, force:true});
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  try {
    const {values} = parseArgs({options:{executable:{type:'string'}, scratch:{type:'string'}}});
    assert.ok(values.executable && values.scratch, 'Required: --executable ABS/current/keepharness --scratch ABS');
    console.log(JSON.stringify(await smoke(values.executable, values.scratch)));
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
