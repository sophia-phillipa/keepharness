#!/usr/bin/env node
// KeepHarness operator suite: scripted walkthroughs that operate the real app the way a
// person does, area by area, with captions, highlights, assertions, screenshots and a
// layout lint per step. See docs/operator-suite.md.
"use strict";
const fs = require("node:fs");
const path = require("node:path");
const { Operator } = require("./lib/operator.cjs");
const { Report } = require("./lib/report.cjs");
const { startFixture, openSession, REPO } = require("./lib/targets.cjs");

const USAGE = `usage: node tests/operator/run-operator.cjs [options]
  --mode fixture|real      fixture (default): isolated servers + fake provider CLIs
                           real: attach to --url/--admin-url, at most --budget prompts
  --url URL --admin-url URL   instance to operate in real mode
  --budget N               real-mode prompt budget (default 0: no prompt is sent)
  --visible | --headless   watch it on screen (paced) or run unattended (default)
  --target browser|desktop Chromium page (default) or the packaged desktop window
  --app PATH               packaged desktop binary (default $KEEPHARNESS_DESKTOP_BIN or
                           dist/keepharness-<version>-linux-x64/keepharness-bin)
  --areas a,b              only these areas (ids or numeric prefixes); --list shows them
  --pace MS                pause between actions (default 600 visible, 0 headless)
  --out DIR                report folder (default operator-report/<timestamp>)
  --python PATH            Python for the fixture servers (default $PYTHON or .venv/bin/python)
  --fixture-ports A,H      fixture admin and harness ports (default 18510,18511)
  --allow-writes           real mode: also run steps that create, edit or delete things
  --allow-default-ports    permit 8094/8095 (refused by default: a person's live instance)`;

function parse(argv) {
  const version = fs.readFileSync(path.join(REPO, "agent_service/VERSION"), "utf8").trim();
  const options = {
    mode: "fixture", budget: 0, visible: false, target: "browser", areas: null, pace: null,
    python: process.env.PYTHON || path.join(REPO, ".venv/bin/python"), fixturePorts: ["18510", "18511"],
    app: process.env.KEEPHARNESS_DESKTOP_BIN || path.join(REPO, `dist/keepharness-${version}-linux-x64/keepharness-bin`),
    allowWrites: false, allowDefaultPorts: false, list: false, version, viewport: { width: 1440, height: 900 },
  };
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i], next = () => argv[++i];
    if (arg === "--mode") options.mode = next();
    else if (arg === "--url") options.url = next().replace(/\/$/, "");
    else if (arg === "--admin-url") options.adminUrl = next().replace(/\/$/, "");
    else if (arg === "--budget") options.budget = Number(next());
    else if (arg === "--visible") options.visible = true;
    else if (arg === "--headless") options.visible = false;
    else if (arg === "--target") options.target = next();
    else if (arg === "--app") options.app = next();
    else if (arg === "--areas") options.areas = next().split(",").map((s) => s.trim()).filter(Boolean);
    else if (arg === "--pace") options.pace = Number(next());
    else if (arg === "--out") options.out = next();
    else if (arg === "--python") options.python = next();
    else if (arg === "--fixture-ports") options.fixturePorts = next().split(",");
    else if (arg === "--allow-writes") options.allowWrites = true;
    else if (arg === "--allow-default-ports") options.allowDefaultPorts = true;
    else if (arg === "--list") options.list = true;
    else if (arg === "--help" || arg === "-h") (console.log(USAGE), process.exit(0));
    else throw new Error("unknown option " + arg + "\n" + USAGE);
  }
  if (!["fixture", "real"].includes(options.mode)) throw new Error("--mode must be fixture or real");
  if (!["browser", "desktop"].includes(options.target)) throw new Error("--target must be browser or desktop");
  if (!Number.isInteger(options.budget) || options.budget < 0) throw new Error("--budget must be a whole number");
  if (options.mode === "real" && !(options.url && options.adminUrl)) throw new Error("real mode needs --url and --admin-url");
  const ports = [options.url, options.adminUrl].filter(Boolean).map((u) => new URL(u).port);
  if (!options.allowDefaultPorts && ports.some((p) => p === "8094" || p === "8095"))
    throw new Error("refusing 8094/8095 (a live instance); pass --allow-default-ports only if that is intended");
  options.pace ??= options.visible ? 600 : 0;
  return options;
}

function loadAreas(selected) {
  const dir = path.join(__dirname, "areas");
  const areas = fs.readdirSync(dir).filter((f) => f.endsWith(".cjs")).sort().map((f) => ({ file: f, ...require(path.join(dir, f)) }));
  if (!selected) return areas;
  return areas.filter((a) => selected.some((s) => s === a.id || a.file.startsWith(s + "-") || a.file.startsWith(s)));
}

async function main() {
  const options = parse(process.argv.slice(2));
  const areas = loadAreas(options.areas);
  if (options.list) {
    for (const a of areas) console.log(`${a.file.padEnd(28)} ${a.id.padEnd(16)} ${a.title}`);
    return 0;
  }
  if (!areas.length) throw new Error("no area matches --areas " + options.areas);
  const known = JSON.parse(fs.readFileSync(path.join(__dirname, "known-defects.json"), "utf8"));
  const stamp = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
  const dir = path.resolve(options.out || path.join(REPO, "operator-report", stamp));
  let fixture = null, session = null, report = null;
  const stop = async () => {
    await session?.close();
    await fixture?.stop();
  };
  process.once("SIGINT", () => stop().finally(() => process.exit(130)));
  process.once("SIGTERM", () => stop().finally(() => process.exit(143)));
  try {
    if (options.mode === "fixture") {
      fixture = await startFixture(options);
      options.url = fixture.harness_url;
      options.adminUrl = fixture.admin_url;
      console.log(`fixture servers: harness ${fixture.harness_url} admin ${fixture.admin_url} (pids ${fixture.pids.join(", ")})`);
    }
    session = await openSession(options, options.url, options.adminUrl);
    report = new Report(dir, {
      mode: options.mode, target: options.target, visible: options.visible, url: options.url,
      admin: options.adminUrl, version: options.version, started: new Date().toISOString(), budget: options.budget,
    });
    const op = new Operator({ session, options, known, report, fixture });
    for (const area of areas) {
      console.log(`\n## ${area.title}`);
      await op.runArea(area);
      if (op.session.page?.isClosed?.() && op.session.target === "desktop") break;
    }
  } finally {
    const summary = report?.write();
    await stop();
    if (summary) {
      console.log(`\nOPERATOR SUMMARY ${JSON.stringify(summary)}`);
      console.log(`report: ${path.join(dir, "index.html")}`);
      process.exitCode = summary.fail || summary.area_errors.length ? 1 : 0;
    }
  }
  return process.exitCode;
}

main().catch((error) => {
  console.error("operator suite failed:", error.message);
  process.exitCode = 2;
});
