#!/usr/bin/env node
// Instance helper for the real-provider chat campaign: `node instance.cjs start|stop|status`.
// Runs the admin panel (python -m control) from this worktree on spare ports with a throwaway
// HOME and a persistent state folder, provisions Codex (gpt-5.6-sol + gpt-5.6-luna) and starts
// the harness. Never touches the real installation, never reads provider credentials.
"use strict";
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const http = require("node:http");
const net = require("node:net");
const path = require("node:path");
const os = require("node:os");
const policy = require("../../../desktop/policy.cjs");

const REPO = path.resolve(__dirname, "../../..");
const ROOT = process.env.CHAT_ROOT || path.join(os.homedir(), ".cache/kho/chat");
const PY = process.env.CHAT_PYTHON || path.join(os.homedir(), ".cache/keepharness-gauntlet/venv-lock/bin/python");
const HARNESS_PORT = Number(process.env.CHAT_HARNESS_PORT || 18640);
const ADMIN_PORT = Number(process.env.CHAT_ADMIN_PORT || 18641);
const FORBIDDEN = [8094, 8095, 18094, 18095, 18420, 18421, 18430, 18431];
const paths = {
  root: ROOT,
  state: path.join(ROOT, "state"),
  home: path.join(ROOT, "home"),
  projects: path.join(ROOT, "projects"),
  run: path.join(ROOT, "run"),
  runs: path.join(ROOT, "runs"),
};
paths.pidfile = path.join(paths.run, "admin.pid");
paths.log = path.join(paths.run, "admin.log");
const MODELS = ["gpt-5.6-sol", "gpt-5.6-luna"];
const EFFORT = "medium"; // the effort the campaign drives from the composer
const PROJECT_NAME = "chat-facts";
const FACTS_FILE = "FACTS.md";
// Distinctive facts the pilot asks about; the model cannot know them without reading the file.
const FACTS = { harbor: "Port Quillon", lamp: "7 brass lanterns", code: "ZEBRA-4471" };

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const readJson = (file) => {
  try {
    return JSON.parse(fs.readFileSync(file, "utf8"));
  } catch {
    return null;
  }
};

if (FORBIDDEN.includes(HARNESS_PORT) || FORBIDDEN.includes(ADMIN_PORT)) throw new Error("forbidden port requested");

function httpRequest(method, url, { body, cookie, headers = {}, timeout = 8000 } = {}) {
  return new Promise((resolve) => {
    const request = http.request(url, { method, timeout, headers: { ...(cookie ? { cookie } : {}), ...(body ? { "content-type": "application/json" } : {}), ...headers } }, (response) => {
      let text = "";
      response.setEncoding("utf8");
      response.on("data", (chunk) => (text += chunk));
      response.on("end", () => resolve({ status: response.statusCode, headers: response.headers, text }));
    });
    request.on("timeout", () => request.destroy());
    request.on("error", () => resolve({ status: 0, headers: {}, text: "" }));
    if (body) request.write(JSON.stringify(body));
    request.end();
  });
}

async function adminLogin() {
  // local.key is the admin owner secret of the throwaway state, not a provider credential.
  const secret = fs.readFileSync(path.join(paths.state, "local.key"), "utf8");
  const opened = await httpRequest("GET", `http://127.0.0.1:${ADMIN_PORT}/open?ticket=${encodeURIComponent(policy.openTicket(secret))}`);
  const value = policy.cookieValue(opened.headers["set-cookie"], "admin");
  if (!value) throw new Error("the admin did not accept the owner ticket");
  return `admin=${value}`;
}

async function adminApi(method, route, body) {
  const cookie = await adminLogin();
  const response = await httpRequest(method, `http://127.0.0.1:${ADMIN_PORT}${route}`, { body, cookie, headers: { "x-harness-admin": "1" }, timeout: 30000 });
  let json = null;
  try {
    json = JSON.parse(response.text);
  } catch {}
  return { status: response.status, json, text: response.text };
}

const portOpen = (port) =>
  new Promise((resolve) => {
    const socket = net.connect({ port, host: "127.0.0.1" });
    socket.once("connect", () => (socket.destroy(), resolve(true)));
    socket.once("error", () => resolve(false));
  });

async function until(predicate, message, timeout = 30000, step = 250) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (await Promise.resolve(predicate()).catch(() => false)) return;
    await sleep(step);
  }
  throw new Error(message);
}

function instanceEnv() {
  const env = {
    PATH: `${path.dirname(PY)}:${path.join(os.homedir(), ".local/bin")}:/usr/local/bin:/usr/bin:/bin`,
    HOME: paths.home,
    XDG_CONFIG_HOME: path.join(paths.home, ".config"),
    XDG_DATA_HOME: path.join(paths.home, ".local/share"),
    XDG_CACHE_HOME: path.join(paths.home, ".cache"),
    LANG: "C.UTF-8",
  };
  return env;
}

function prepareFolders() {
  for (const dir of [paths.run, paths.runs, paths.projects, paths.home]) fs.mkdirSync(dir, { recursive: true });
  // The desktop signs in with $HOME/.local/share/keepharness/local.key: point it at the state's key.
  const link = path.join(paths.home, ".local/share/keepharness/local.key");
  fs.mkdirSync(path.dirname(link), { recursive: true });
  try {
    if (fs.readlinkSync(link) === path.join(paths.state, "local.key")) return seedProject();
    fs.unlinkSync(link);
  } catch {}
  if (fs.existsSync(path.join(paths.state, "local.key"))) fs.symlinkSync(path.join(paths.state, "local.key"), link);
  seedProject();
}

function seedProject() {
  const dir = path.join(paths.projects, PROJECT_NAME);
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(
    path.join(dir, FACTS_FILE),
    `# Harbor notes\n\nThe harbor of the fictional island is called ${FACTS.harbor}.\nIts lighthouse keeps ${FACTS.lamp} lit every night.\nThe maintenance code for the lighthouse door is ${FACTS.code}.\n`,
  );
  return dir;
}

const readPid = () => Number(fs.readFileSync(paths.pidfile, "utf8").trim()) || 0;
function alive(pid) {
  try {
    process.kill(pid, 0);
    return pid > 0;
  } catch {
    return false;
  }
}
const pidIsAdmin = (pid) => {
  try {
    return fs.readFileSync(`/proc/${pid}/cmdline`, "utf8").includes("control");
  } catch {
    return false;
  }
};

// Settings as the owner would save them: Codex on, sol + luna, one project rooted at the seeded folder.
async function provision() {
  const state = await adminApi("GET", "/api/state");
  if (state.status !== 200) throw new Error("admin state: " + state.status);
  const settings = state.json.settings;
  const folder = seedProject();
  settings.port = HARNESS_PORT;
  settings.tailnet_port = HARNESS_PORT;
  provisionProject(settings, folder);
  const codex = settings.services.codex || {};
  settings.services.codex = {
    ...codex,
    enabled: true,
    models: MODELS,
    mode: codex.mode || "native",
    projects: ["sem-projeto", PROJECT_NAME],
    permissions: { read: true, write: false, upload: true, tests: false, internet: false, shell: false, hooks: false },
  };
  const saved = await adminApi("POST", "/api/settings", settings);
  if (saved.status !== 200) throw new Error("saving settings failed: " + saved.status + " " + saved.text.slice(0, 300));
}

// A registered project is {id, label, root}; the root must be an existing absolute folder.
function provisionProject(settings, folder) {
  const list = (settings.projects = Array.isArray(settings.projects) ? settings.projects : []);
  if (!list.some((p) => p.id === PROJECT_NAME)) list.push({ id: PROJECT_NAME, label: "Chat facts", root: folder });
}

async function summary() {
  const out = { admin: await portOpen(ADMIN_PORT), harness: await portOpen(HARNESS_PORT), pid: fs.existsSync(paths.pidfile) ? readPid() : null };
  if (out.admin) {
    const state = await adminApi("GET", "/api/state").catch(() => ({ status: 0 }));
    const services = state.json?.settings?.services || {};
    out.providers = Object.fromEntries(
      Object.entries(services).map(([name, s]) => [name, { enabled: !!s.enabled, models: s.models || [], projects: s.projects || [] }]),
    );
    out.running = state.json?.running ?? state.json?.harness?.running ?? null;
  }
  return out;
}

async function start() {
  prepareFolders();
  if (await portOpen(ADMIN_PORT)) {
    if (!(fs.existsSync(paths.pidfile) && alive(readPid()) && pidIsAdmin(readPid()))) throw new Error(`port ${ADMIN_PORT} is busy and is not this instance; refusing to touch it`);
    console.log("admin already running (pid " + readPid() + ")");
  } else {
    if (await portOpen(HARNESS_PORT)) throw new Error(`port ${HARNESS_PORT} is busy; refusing to start`);
    const log = fs.openSync(paths.log, "a", 0o600);
    const child = spawn(PY, ["-m", "control", "--port", String(ADMIN_PORT), "--state", paths.state], { cwd: REPO, env: instanceEnv(), detached: true, stdio: ["ignore", log, log] });
    child.unref();
    fs.writeFileSync(paths.pidfile, String(child.pid), { mode: 0o600 });
    await until(() => portOpen(ADMIN_PORT), "the admin did not start (see " + paths.log + ")", 60000);
  }
  // First start creates local.key; link it for the desktop now.
  prepareFolders();
  await until(() => fs.existsSync(path.join(paths.state, "local.key")), "no local.key in the state", 15000);
  await provision();
  if (!(await portOpen(HARNESS_PORT))) {
    const started = await adminApi("POST", "/api/start", {});
    if (started.status !== 200) throw new Error("starting the harness failed: " + started.status + " " + started.text.slice(0, 300));
  }
  await until(() => portOpen(HARNESS_PORT), "the harness did not answer", 60000);
  console.log(`harness http://127.0.0.1:${HARNESS_PORT}\nadmin   http://127.0.0.1:${ADMIN_PORT}\nstate   ${paths.state}\nhome    ${paths.home}`);
}

function descendants(pid) {
  let kids = [];
  try {
    kids = fs.readFileSync(`/proc/${pid}/task/${pid}/children`, "utf8").trim().split(/\s+/).filter(Boolean).map(Number);
  } catch {}
  return kids.flatMap((kid) => [...descendants(kid), kid]);
}

async function stop() {
  if (!fs.existsSync(paths.pidfile)) return console.log("no pidfile; nothing to stop");
  const pid = readPid();
  if (alive(pid) && pidIsAdmin(pid)) {
    const tree = [...descendants(pid), pid];
    // Ask the admin to stop the harness first (it owns that child), then end the admin.
    await adminApi("POST", "/api/stop", {}).catch(() => {});
    for (const target of tree) {
      try {
        process.kill(target, "SIGTERM");
      } catch {}
    }
    await until(() => tree.every((p) => !alive(p)), "processes survived SIGTERM", 20000).catch(() => {
      for (const target of tree) {
        try {
          process.kill(target, "SIGKILL");
        } catch {}
      }
    });
    console.log("stopped pids " + tree.join(","));
  } else console.log("pid " + pid + " is not this instance's admin; leaving it alone");
  fs.rmSync(paths.pidfile, { force: true });
  await until(async () => !(await portOpen(ADMIN_PORT)) && !(await portOpen(HARNESS_PORT)), "ports still busy", 15000);
  console.log(`ports ${HARNESS_PORT}/${ADMIN_PORT} free`);
}

module.exports = { paths, ROOT, HARNESS_PORT, ADMIN_PORT, MODELS, EFFORT, PROJECT_NAME, FACTS, FACTS_FILE, adminApi, adminLogin, httpRequest, portOpen, until, summary, sleep, readJson };

if (require.main === module) {
  const command = process.argv[2];
  const run = { start, stop, status: async () => console.log(JSON.stringify(await summary(), null, 2)) }[command];
  if (!run) {
    console.error("usage: node instance.cjs start|stop|status");
    process.exit(2);
  }
  run().then(() => process.exit(0), (error) => (console.error("instance " + command + " failed: " + error.message), process.exit(1)));
}
