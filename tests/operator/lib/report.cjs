// Collects areas and steps and writes operator-report/<timestamp>/{index.html,summary.json}.
"use strict";
const fs = require("node:fs");
const path = require("node:path");

class StepRecord {
  constructor(id, caption) {
    Object.assign(this, { id, caption, status: "running", detail: "", ms: 0, shot: null, lint: null, known: null, stale: null });
  }
  finish(status, detail = "") {
    this.status = status;
    this.detail = detail;
    return this;
  }
}

class AreaRecord {
  constructor(area) {
    Object.assign(this, { id: area.id, title: area.title, steps: [], error: null, halted: false });
  }
  step(id, caption) {
    const record = new StepRecord(id, caption);
    this.steps.push(record);
    return record;
  }
}

class Report {
  constructor(dir, meta) {
    this.dir = dir;
    this.meta = meta;
    this.areas = [];
    fs.mkdirSync(path.join(dir, "shots"), { recursive: true });
  }

  area(area) {
    const record = new AreaRecord(area);
    this.areas.push(record);
    return record;
  }

  summary() {
    const counts = { areas: this.areas.length, steps: 0, pass: 0, fail: 0, known: 0, skipped: 0, lint_new_findings: 0, stale_known: [] };
    const distinct = new Set();
    for (const area of this.areas)
      for (const step of area.steps) {
        counts.steps++;
        counts[step.status] = (counts[step.status] || 0) + 1;
        // The same new finding seen on many screens counts once (numbers normalized).
        for (const item of step.lint?.items || []) if (!item.known) distinct.add(item.kind + item.summary.replace(/[\d.]+/g, "#"));
        if (step.stale) counts.stale_known.push(`${step.id} (${step.stale})`);
      }
    counts.lint_new_findings = distinct.size;
    counts.area_errors = this.areas.filter((a) => a.error).map((a) => `${a.id}: ${a.error}`);
    return counts;
  }

  write() {
    const summary = this.summary();
    const data = { meta: { ...this.meta, finished: new Date().toISOString() }, summary, areas: this.areas };
    fs.writeFileSync(path.join(this.dir, "summary.json"), JSON.stringify(data, null, 2));
    fs.writeFileSync(path.join(this.dir, "index.html"), html(data));
    return summary;
  }
}

const esc = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

function badge(step) {
  if (step.status === "known") return `<span class="b known">known (${esc(step.known.id)})</span>`;
  const stale = step.stale ? ` <span class="b stale" title="Passes now: remove it from known-defects.json">allow-list ${esc(step.stale)} can go</span>` : "";
  return `<span class="b ${step.status}">${step.status}</span>${stale}`;
}

function lintCell(lint) {
  if (!lint) return "";
  if (lint.error) return `<span class="muted">lint error: ${esc(lint.error)}</span>`;
  const counts = Object.entries(lint.counts).map(([k, v]) => `${k} ${v}`).join(", ") || "clean";
  if (!lint.items.length) return `<span class="muted">${counts}</span>`;
  const rows = lint.items.map((i) => `<li class="${i.known ? "kn" : "new"}">${i.known ? `[${esc(i.known)}] ` : "[new] "}${esc(i.kind)}: ${esc(i.summary)}</li>`).join("");
  return `<details><summary>${counts}${lint.unexpected ? ` · <b>${lint.unexpected} new</b>` : ""}</summary><ul>${rows}</ul></details>`;
}

function html({ meta, summary, areas }) {
  const toc = areas
    .map((a) => {
      const fails = a.steps.filter((s) => s.status === "fail").length;
      return `<li><a href="#${esc(a.id)}">${esc(a.title)}</a> <span class="muted">${a.steps.length} steps${fails ? `, <b class="f">${fails} failed</b>` : ""}</span></li>`;
    })
    .join("");
  const sections = areas
    .map((a) => {
      const rows = a.steps
        .map(
          (s) => `<tr class="${s.status}"><td class="id">${esc(s.id)}</td><td>${esc(s.caption)}${s.detail ? `<div class="detail">${esc(s.detail)}</div>` : ""}${s.known ? `<div class="detail">${esc(s.known.note || "")}</div>` : ""}</td><td>${badge(s)}<div class="muted">${(s.ms / 1000).toFixed(1)} s</div></td><td>${s.shot ? `<a href="${esc(s.shot)}"><img loading="lazy" src="${esc(s.shot)}" alt="screenshot of ${esc(s.id)}"></a>` : ""}</td><td class="lint">${lintCell(s.lint)}</td></tr>`,
        )
        .join("");
      return `<section id="${esc(a.id)}"><h2>${esc(a.title)}</h2>${a.error ? `<p class="detail">Area error: ${esc(a.error)}</p>` : ""}<table><thead><tr><th>Step</th><th>Caption and outcome</th><th>Result</th><th>Screenshot</th><th>Layout lint</th></tr></thead><tbody>${rows}</tbody></table></section>`;
    })
    .join("");
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>KeepHarness operator report</title>
<style>body{font:14px/1.45 system-ui,sans-serif;margin:24px;color:#1d1d1f;background:#fafafa}h1{margin:0 0 4px}table{border-collapse:collapse;width:100%;background:#fff;margin-bottom:28px}th,td{border:1px solid #e3e3e6;padding:6px 8px;vertical-align:top;text-align:left}td.id{font:12px ui-monospace,monospace;white-space:nowrap}img{width:220px;border:1px solid #ddd}.b{display:inline-block;padding:1px 8px;border-radius:999px;font-weight:600;font-size:12px}.b.pass{background:#d9f5df;color:#145c25}.b.fail{background:#ffd9d6;color:#8a1810}.b.known{background:#fff0c2;color:#6b4e00}.b.skipped{background:#ececf0;color:#555}.b.stale{background:#dbe8ff;color:#123d8a}.detail{color:#8a1810;font-size:12px;margin-top:4px}.muted{color:#777;font-size:12px}td.lint{font-size:12px;max-width:420px}li.new{color:#8a1810}li.kn{color:#6b4e00}.f{color:#8a1810}.sum span{margin-right:14px}</style></head><body>
<h1>KeepHarness operator report</h1><p class="muted">${esc(meta.mode)} mode · ${esc(meta.target)} target · ${meta.visible ? "visible" : "headless"} · ${esc(meta.url)} · version ${esc(meta.version)} · started ${esc(meta.started)}</p>
<p class="sum"><span><b>${summary.areas}</b> areas</span><span><b>${summary.steps}</b> steps</span><span class="b pass">${summary.pass} pass</span><span class="b fail">${summary.fail} fail</span><span class="b known">${summary.known} known</span><span class="b skipped">${summary.skipped} skipped</span><span>${summary.lint_new_findings} distinct new lint findings</span></p>
${summary.stale_known.length ? `<p>Allow-list entries that pass now (remove them): ${summary.stale_known.map(esc).join(", ")}</p>` : ""}
<ol>${toc}</ol>${sections}</body></html>`;
}

module.exports = { Report };
