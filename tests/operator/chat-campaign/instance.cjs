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
// Slice 1 seeds: two folders under the harness HOME (so the project dialog can browse them) and one
// file outside both (and outside HOME). Invented values only.
const S1 = {
  main: "campaign-main",
  second: "campaign-annex",
  codeWord: "QUILLFEATHER-3917",
  keeper: "Ondra Welk",
  csvTotal: 4017,
  csvTop: "lamps",
  fn: "reconcile_tide_ledger",
  srcLines: 41,
  secondWord: "OSPREY-5208",
  outsideWord: "KESTREL-6602",
};

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
    if (fs.readlinkSync(link) === path.join(paths.state, "local.key")) return seedAll();
    fs.unlinkSync(link);
  } catch {}
  if (fs.existsSync(path.join(paths.state, "local.key"))) fs.symlinkSync(path.join(paths.state, "local.key"), link);
  seedAll();
}

const S1B = { boat: "Marlin Dusk", departure: "06:40", beaconOld: "BEACON-2210", beaconNew: "BEACON-8843" };

function seedAll() {
  seedProject();
  seedSlice1();
  seedSlice1b();
  seedSlice2b();
}

// Slice 1b seeds: two small attachments (outside every project folder) and a file the run edits between two asks.
function seedSlice1b() {
  const dir = path.join(paths.projects, "attach");
  put(path.join(dir, "harbor-memo.txt"), `Harbor memo: the pilot boat is named ${S1B.boat}. It leaves the quay at ${S1B.departure} with a crew of 5 and carries 12 crates of salted herring.\n`);
  put(path.join(dir, "average.py"), 'def average(values):\n    """Return the mean of a non-empty list."""\n    return sum(values) / (len(values) + 1)\n');
  put(path.join(paths.home, S1.main, "facts/gamma.txt"), `The gamma beacon code is ${S1B.beaconOld}.\n`);
}

// Slice 2b seeds: a red PNG with white "HOLM 73" (5x7 pixel font, no image library), a code file, a CSV and a text file over the excerpt limit (6000 characters).
const S2B = { color: "red", text: "HOLM 73", fee: "417", fn: "settle_dock_fee", peak: "342", start: "START-MARK-QUARTZ-41", end: "END-MARK-HERON-88" };
const GLYPHS = {
  H: "10001 10001 10001 11111 10001 10001 10001", O: "01110 10001 10001 10001 10001 10001 01110", L: "10000 10000 10000 10000 10000 10000 11111",
  M: "10001 11011 10101 10101 10001 10001 10001", 7: "11111 00001 00010 00100 01000 01000 01000", 3: "11110 00001 00001 01110 00001 00001 11110",
};

function holmPng() {
  const zlib = require("node:zlib");
  const [w, h, scale, x0, y0] = [1000, 300, 20, 80, 80];
  const raw = Buffer.alloc(h * (1 + w * 3));
  for (let y = 0; y < h; y++) {
    const row = y * (1 + w * 3);
    for (let x = 0; x < w; x++) raw.set([210, 35, 35], row + 1 + x * 3);
  }
  [...S2B.text].forEach((ch, i) => {
    const rows = GLYPHS[ch]?.split(" ");
    if (!rows) return;
    rows.forEach((bits, gy) => [...bits].forEach((bit, gx) => {
      if (bit !== "1") return;
      for (let dy = 0; dy < scale; dy++) for (let dx = 0; dx < scale; dx++) raw.set([255, 255, 255], (y0 + gy * scale + dy) * (1 + w * 3) + 1 + (x0 + (i * 6 + gx) * scale + dx) * 3);
    }));
  });
  const crcTable = Array.from({ length: 256 }, (_, n) => { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; return c >>> 0; });
  const crc = (buf) => { let c = 0xffffffff; for (const b of buf) c = crcTable[(c ^ b) & 255] ^ (c >>> 8); return (c ^ 0xffffffff) >>> 0; };
  const chunk = (type, data) => {
    const body = Buffer.concat([Buffer.from(type), data]);
    const out = Buffer.alloc(8 + data.length + 4);
    out.writeUInt32BE(data.length, 0);
    body.copy(out, 4);
    out.writeUInt32BE(crc(body), 8 + data.length);
    return out;
  };
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(w, 0);
  ihdr.writeUInt32BE(h, 4);
  ihdr.set([8, 2, 0, 0, 0], 8);
  return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk("IHDR", ihdr), chunk("IDAT", zlib.deflateSync(raw)), chunk("IEND", Buffer.alloc(0))]);
}

function seedSlice2b() {
  const dir = path.join(paths.projects, "attach");
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, "holm.png"), holmPng());
  put(path.join(dir, "tally.py"), `HARBOR_FEE = ${S2B.fee}\n\n\ndef ${S2B.fn}(crates):\n    """Return the dock fee for a number of crates."""\n    return crates * HARBOR_FEE\n`);
  put(path.join(dir, "tides.csv"), `day,high_tide_cm\nMon,310\nTue,295\nWed,${S2B.peak}\n`);
  const filler = Array.from({ length: 125 }, (_, i) => `ledger entry ${String(i + 1).padStart(3, "0")}: crates logged and checked at the quay.`);
  put(path.join(dir, "ledger-long.txt"), [`${S2B.start}: the ledger opens here.`, ...filler, `${S2B.end}: the ledger closes here.`].join("\n") + "\n");
}

function put(file, text) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, text);
}

function seedSlice1() {
  const main = path.join(paths.home, S1.main);
  const second = path.join(paths.home, S1.second);
  put(path.join(main, "facts/alpha.txt"), `The archive code word is ${S1.codeWord}.\nThe archive keeper is ${S1.keeper}.\n`);
  put(path.join(main, "facts/budget.csv"), "item,cost\nlamps,1250\nrope,480\npaint,735\nnets,1190\ntar,362\n");
  const head = `def _fold_offsets(values):\n    """Sum the tide offsets."""\n    return sum(values)\n\n\ndef ${S1.fn}(entries):\n    """Reconcile the tide ledger."""\n    return _fold_offsets(entries)\n`;
  const pad = Array.from({ length: S1.srcLines - head.split("\n").length + 1 }, (_, i) => `# ledger note ${i + 1}`).join("\n");
  put(path.join(main, "src/tide.py"), head + pad + "\n");
  put(path.join(second, "notes/beta.txt"), `The annex code word is ${S1.secondWord}.\n`);
  put(path.join(paths.projects, "outside/vault.txt"), `The outside vault word is ${S1.outsideWord}.\n`);
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
  await provisionDeepseek(settings);
  const saved = await adminApi("POST", "/api/settings", settings);
  if (saved.status !== 200) throw new Error("saving settings failed: " + saved.status + " " + saved.text.slice(0, 300));
}

// DeepSeek is enabled only when the owner already placed deepseek.key in the state folder; the key
// itself is never read here. The admin's own provider check verifies it and lists the models.
async function provisionDeepseek(settings) {
  if (!fs.existsSync(path.join(paths.state, "deepseek.key"))) return;
  const checked = await adminApi("POST", "/api/check", { provider: "deepseek" });
  if (checked.status !== 200 || checked.json?.authenticated !== true) throw new Error("DeepSeek is not ready: " + checked.status + " " + String(checked.json?.error || checked.json?.message || "").slice(0, 200));
  const ids = Object.keys(checked.json.models || {});
  const model = ids.find((id) => /flash/.test(id));
  if (!model) throw new Error("no flash model in the DeepSeek account list: " + ids.join(", "));
  settings.services.deepseek = {
    ...(settings.services.deepseek || {}),
    enabled: true,
    models: [model],
    projects: ["sem-projeto", PROJECT_NAME],
    permissions: { read: true, write: false, upload: true, tests: false, internet: false, shell: false, hooks: false },
  };
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

module.exports = { paths, ROOT, HARNESS_PORT, ADMIN_PORT, MODELS, EFFORT, PROJECT_NAME, FACTS, S1, S1B, S2B, seedSlice1b, seedSlice2b, FACTS_FILE, adminApi, adminLogin, httpRequest, portOpen, until, summary, sleep, readJson };

if (require.main === module) {
  const command = process.argv[2];
  const run = { start, stop, status: async () => console.log(JSON.stringify(await summary(), null, 2)) }[command];
  if (!run) {
    console.error("usage: node instance.cjs start|stop|status");
    process.exit(2);
  }
  run().then(() => process.exit(0), (error) => (console.error("instance " + command + " failed: " + error.message), process.exit(1)));
}
