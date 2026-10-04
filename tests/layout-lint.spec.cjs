// Layout lint gate (gauntlet WP-05, ledger L26-L29, UX-R5 section 3).
//
// Renders the composer and the status strip in the states where they broke (new chat, a Claude
// model, an agent conversation, an attachment, a long table/code answer, a long last-run text)
// at 1440/1024/768/390/360 (and the small desktop windows) in Paper and Graphite, against route
// fixtures only (no server, no provider). It fails on:
//   1. the named invariants V1-V5 (agent card, controls, status strip, mode label, clipped text);
//   2. any finding of the generic lint (tests/support/layout-lint.js): interactive elements that
//      overlap by more than 2 px (confirmed with elementFromPoint, so controls behind a modal or an
//      inert region do not count), text that escapes its box, elements beyond their container,
//      field text that is clipped or runs under an icon, and horizontal page scroll.
// A failing screen is saved as a screenshot in the temp directory (path printed with the failures).
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const origin = "http://layout-lint.test";
const ROOT = path.join(__dirname, "..");
const LINT = fs.readFileSync(path.join(__dirname, "support", "layout-lint.js"), "utf8");
const SHOTS = path.join(os.tmpdir(), "keepharness-layout-lint");
// Lint findings that are not defects.
const KNOWN_NOISE = new Set([
  "spill|#send", // the visually hidden "Send" label is wider than the 32 px button
  "spill|#access-trigger-icon", // the "?" glyph line is 5 px taller than its 16 px icon box
  "clipped|#search-conversations > kbd", // the Ctrl K hint inside the search box
]);
const THEMES = ["paper", "graphite"];
const MAIN_VIEWPORTS = [
  { width: 1440, height: 900 },
  { width: 1024, height: 768 },
  { width: 768, height: 1024 },
  { width: 390, height: 844 },
  { width: 360, height: 800 }, // the most common Android phone width
];
// Electron's minimum window is 720x500: the status strip must also hold there (V3).
const SMALL_WINDOWS = [
  { width: 800, height: 600 },
  { width: 720, height: 500 },
];
const PIXEL_PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
  "base64",
);
const LONG_RUN_TEXT =
  "Review the release notes against the migration checklist and report every step that still lacks a verified owner, a rollback note or a measured result before the cut-over window opens";
// The strip no longer echoes the prompt (OP-R1-3); a long work item reference is what can still make it long.
const LONG_WORK_ITEM = "RELEASE-CUT-OVER-" + "0123456789".repeat(8);
const LONG_ANSWER = [
  "Here is the matrix you asked for.",
  "",
  "| Configuration | Availability | Infrastructure | Observability | Ownership | Escalation | Rollback |",
  "| --- | --- | --- | --- | --- | --- | --- |",
  "| Primary region | 99.95% | Managed cluster | Traces and metrics | Platform team | Pager rotation | Automatic |",
  "| Secondary region | 99.90% | Managed cluster | Metrics only | Platform team | Email | Manual |",
  "",
  "```python",
  'print("this single line is intentionally long so the code block has to scroll sideways: " + ", ".join(str(n) for n in range(40)) + " end")',
  "```",
  "",
  "Source: https://example.test/runbooks/release/cut-over/checklist/with/a/very/long/path/that/never/breaks/on/its/own/segment-0123456789",
].join("\n");

const models = [
  { id: "gpt-6-astra", name: "GPT-6 Astra", backend: "codex", efforts: ["low", "medium", "high"], execution_modes: ["native", "scoped"], permissions: { upload: true } },
  {
    id: "claude-opus-5",
    name: "Claude Opus 5",
    backend: "claude",
    efforts: ["configured", "low", "medium", "high", "xhigh", "max"],
    execution_modes: ["native", "scoped"],
    permissions: { upload: true },
  },
];
const turn = (id, request, result = {}, state = "completed", extra = {}) => ({
  id,
  project: "sem-projeto",
  state,
  request: { backend: "codex", model: "gpt-6-astra", effort: "medium", access_mode: "ask", prompt: "Summarize the release.", ...request },
  result: { answer: "Done.", model: request.model || "gpt-6-astra", total_seconds: 4.2, ...result },
  gates: [],
  ...extra,
});
const conversationDetails = {
  "c-agent": {
    title: "Release checker",
    execution_mode: "native",
    turns: [
      turn("j-agent", {
        backend: "claude",
        model: "claude-opus-5",
        effort: "configured",
        prompt: "@@release-checker check the changelog",
        invocations: [{ mode: "conversational", resource_id: "release-checker" }],
        resource_selections: [{ token: "@@release-checker" }],
      }),
    ],
  },
  "c-files": {
    title: "Photo question",
    execution_mode: "native",
    turns: [
      turn(
        "j-files",
        { prompt: "What does the label say?" },
        {},
        "completed",
        { attachments: [{ name: "photo.png", preview_url: origin + "/v1/previews/photo.png" }, { name: "a-file-name-that-is-long-enough-to-need-truncating-in-the-chip.pdf" }] },
      ),
    ],
  },
  "c-long": {
    title: "Release matrix",
    execution_mode: "native",
    turns: [turn("j-long", { prompt: "Make the matrix." }, { answer: LONG_ANSWER })],
  },
  "c-run": {
    title: "Release review",
    execution_mode: "native",
    turns: [turn("j-run", { prompt: LONG_RUN_TEXT }, {}, "running")],
  },
};
const conversationList = [
  { id: "c-agent", title: "Release checker", project: "sem-projeto", state: "completed", last_job_id: "j-agent", execution: { backend: "claude", model: "claude-opus-5" } },
  { id: "c-files", title: "Photo question", project: "sem-projeto", state: "completed", last_job_id: "j-files", execution: { backend: "codex", model: "gpt-6-astra" } },
  { id: "c-long", title: "Release matrix", project: "sem-projeto", state: "completed", last_job_id: "j-long", execution: { backend: "codex", model: "gpt-6-astra" } },
  { id: "c-run", title: "Release review", project: "sem-projeto", state: "running", last_job_id: "j-run", execution: { backend: "codex", model: "gpt-6-astra" } },
];
const activity = {
  counts: { running: 1, queued: 0, needs_you: 0 },
  jobs: [{ job_id: "j-run", conversation_id: "c-run", project_id: "sem-projeto", state: "running", backend: "codex", model: "gpt-6-astra", title: LONG_RUN_TEXT, work_item: LONG_WORK_ITEM }],
  providers: [{ backend: "codex", model: "gpt-6-astra", state: "ready", running: 1, queued: 0 }],
  needs_you: [],
};

async function serve(route) {
  const url = new URL(route.request().url());
  const pathname = url.pathname;
  if (!pathname.startsWith("/v1/")) {
    const file = pathname === "/" ? "index.html" : pathname.slice(1);
    return route.fulfill({ path: path.join(ROOT, file.startsWith("assets/") ? "harness_ui" : "agent_service", file) });
  }
  const detail = pathname.match(/^\/v1\/conversations\/([^/]+)$/);
  let data = {};
  if (pathname === "/v1/projects") data = { projects: ["sem-projeto"], details: {} };
  else if (pathname === "/v1/models") data = { models, providers: { codex: true, claude: true }, uploads_enabled: true };
  else if (pathname === "/v1/conversations") data = { conversations: conversationList };
  else if (detail) data = conversationDetails[detail[1]] || {};
  else if (pathname === "/v1/version") data = { version: "0.15.0", build: "layout-lint" };
  else if (pathname === "/v1/activity") data = activity;
  else if (pathname === "/v1/catalog") data = { agents: [], skills: [], warnings: [], scope: "test" };
  else if (pathname === "/v1/files") data = { file_id: "file-fixture", name: "photo.png" };
  else if (pathname === "/v1/previews/photo.png") return route.fulfill({ body: PIXEL_PNG, contentType: "image/png" });
  else if (/^\/v1\/jobs\/[^/]+\/spans$/.test(pathname)) data = { spans: [] };
  else if (pathname === "/v1/project-files") data = { roots: [], entries: [] };
  else if (pathname === "/v1/usage") data = { available: false };
  return route.fulfill({ json: data });
}
async function openConversation(page, id) {
  const title = conversationDetails[id].title;
  await page.evaluate((conversationId) => load(conversationId), id);
  await page.waitForFunction((expected) => document.getElementById("conversation-title")?.textContent === expected, title);
}

// Each screen leaves the page in the state it names; screens run in this order on one page per theme and viewport.
const SCREENS = [
  { id: "new-chat", open: async () => {} },
  { id: "new-chat-claude", open: (page) => page.selectOption("#model", "claude-opus-5") },
  { id: "agent-conversation", open: (page) => openConversation(page, "c-agent"), extra: SMALL_WINDOWS },
  {
    id: "conversation-attachment",
    open: async (page) => {
      await openConversation(page, "c-files");
      await page.setInputFiles("#file", { name: "photo.png", mimeType: "image/png", buffer: PIXEL_PNG });
      await page.locator("#attachments .attachment").first().waitFor();
    },
  },
  { id: "long-answer", open: (page) => openConversation(page, "c-long") },
  {
    id: "status-bar-long",
    open: async (page) => {
      await openConversation(page, "c-run");
      await page.waitForFunction(() => document.getElementById("run-status-toggle")?.textContent.includes("RELEASE-CUT-OVER"));
    },
    extra: SMALL_WINDOWS,
  },
  {
    // The multiple-choice question card (operator suite, approvals area): its legend is the question.
    id: "question-gate",
    open: async (page) => {
      await openConversation(page, "c-long");
      await page.evaluate(() =>
        showGate({
          gate_id: "g-lint",
          question: "Which fixture option should run, and should it also publish the result to every connected project afterwards?",
          options: [{ id: "a", label: "Alpha", description: "First option" }, { id: "b", label: "Beta", description: "Second option" }],
        }),
      );
      await page.locator("#gate-g-lint legend").waitFor();
    },
  },
  // L64: resize handles (V6), dialog close buttons (V12) and search fields (generic field lint) must not sit
  // on text or controls. These screens are checked for those rules only: the generic overlap lint of an open side
  // panel at tablet widths is a separate layout concern.
  {
    id: "side-panel-and-console",
    lint: [],
    open: async (page) => {
      await openConversation(page, "c-run");
      await page.keyboard.press("Control+j");
      await page.locator("#run-console").waitFor({ state: "visible" });
      if (await page.locator("#activity-panel").isHidden()) await page.click("#panel-toggle");
      await page.locator("#activity-panel").waitFor({ state: "visible" });
    },
    close: async (page) => {
      await page.keyboard.press("Control+j");
      await page.locator("#run-console").waitFor({ state: "hidden" });
      await page.click("#panel-toggle");
    },
  },
  {
    id: "search-dialog",
    lint: ["fields"],
    open: async (page) => {
      await page.click("#search-conversations");
      await page.fill("#conversation-search", "release migration");
    },
    close: (page) => page.keyboard.press("Escape"),
  },
  {
    id: "setup-dialog",
    lint: [],
    open: (page) => page.evaluate(() => document.getElementById("setup-dialog").showModal()),
    close: (page) => page.keyboard.press("Escape"),
  },
];

// ---------------------------------------------------------------------------- named invariants
async function namedInvariants(page, viewport) {
  const problems = [];
  const measured = await page.evaluate(() => {
    const box = (el) => {
      if (!el || !el.checkVisibility()) return null;
      const r = el.getBoundingClientRect();
      return { x: r.x, y: r.y, right: r.right, bottom: r.bottom, width: r.width, height: r.height };
    };
    const $ = (selector) => document.querySelector(selector);
    // A label that is visually hidden on purpose (1 px wide in the conversation pill) is not clipped text.
    const overflows = (el) => !!el && el.checkVisibility() && el.clientWidth > 2 && el.scrollWidth > el.clientWidth + 1;
    const prompt = $("#prompt");
    const style = getComputedStyle(prompt);
    const canvas = document.createElement("canvas").getContext("2d");
    canvas.font = `${style.fontStyle} ${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
    const strip = $("#run-status-strip");
    return {
      controls: Object.fromEntries(["#attach", "#access-trigger", "#model-trigger", "#effort-trigger", "#send", "#prompt"].map((s) => [s, box($(s))])),
      dropzone: box($("#dropzone")),
      persona: box($("#persona-control")),
      personaLabelWidth: $(".persona-label")?.checkVisibility() ? $(".persona-label").clientWidth : null,
      personaLabelOverflow: overflows($(".persona-label")),
      strip: { box: box(strip), overflow: strip.scrollHeight - strip.clientHeight },
      stripChildren: [...strip.children].map((child) => ({ id: child.id || child.className, box: box(child) })),
      modeHeadingOverflow: [".execution-mode-heading", "#access-mode-notice"].filter((s) => overflows($(s))),
      placeholder: { shown: !prompt.value, width: canvas.measureText(prompt.placeholder).width, content: prompt.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight), text: prompt.placeholder },
      clippedLabels: ["#effort-label", "#access-label"].filter((s) => overflows($(s))),
    };
  });
  // V1/V2: controls never intersect by more than 2 px and never leave the composer card.
  const ids = Object.keys(measured.controls).filter((id) => measured.controls[id]);
  for (const id of ids) {
    const control = measured.controls[id];
    if (control.x < measured.dropzone.x - 1 || control.right > measured.dropzone.right + 1)
      problems.push(`V1 ${id} leaves the composer card (${Math.round(control.x)}-${Math.round(control.right)} vs ${Math.round(measured.dropzone.x)}-${Math.round(measured.dropzone.right)})`);
  }
  for (let i = 0; i < ids.length; i++)
    for (let j = i + 1; j < ids.length; j++) {
      const a = measured.controls[ids[i]], b = measured.controls[ids[j]];
      const w = Math.min(a.right, b.right) - Math.max(a.x, b.x), h = Math.min(a.bottom, b.bottom) - Math.max(a.y, b.y);
      if (w > 2 && h > 2) problems.push(`V1 ${ids[i]} and ${ids[j]} overlap by ${Math.round(w)}x${Math.round(h)} px`);
    }
  if (measured.persona) {
    if (measured.persona.height > viewport.height * 0.25) problems.push(`V2 #persona-control is ${Math.round(measured.persona.height)} px tall (limit ${viewport.height * 0.25})`);
    if (measured.personaLabelWidth < 80 || measured.personaLabelOverflow) problems.push(`V2 .persona-label is ${measured.personaLabelWidth} px wide or overflows`);
    if (measured.controls["#prompt"] && measured.persona.bottom > measured.controls["#prompt"].y + 1) problems.push("V1 the agent card is not above the input");
  }
  // V3: the 28 px strip holds its own content on one line.
  if (measured.strip.overflow > 1) problems.push(`V3 #run-status-strip content is ${measured.strip.overflow} px taller than the strip`);
  for (const child of measured.stripChildren) {
    if (!child.box) continue;
    if (child.box.y < measured.strip.box.y - 1 || child.box.bottom > measured.strip.box.bottom + 1 || child.box.right > measured.strip.box.right + 1)
      problems.push(`V3 strip child ${child.id} leaves the strip`);
  }
  // V4/V5: nothing paints outside the "Native conversation" pill; labels and the placeholder fit.
  for (const selector of measured.modeHeadingOverflow) problems.push(`V4 ${selector} paints outside its box`);
  for (const selector of measured.clippedLabels) problems.push(`V5 ${selector} is clipped`);
  if (measured.placeholder.shown && measured.placeholder.width > measured.placeholder.content + 1)
    problems.push(`V5 the placeholder needs ${Math.round(measured.placeholder.width)} px but #prompt offers ${Math.round(measured.placeholder.content)} ("${measured.placeholder.text}")`);
  if (viewport.width >= 1024 && measured.controls["#prompt"] && measured.controls["#prompt"].width < 240)
    problems.push(`V1 #prompt is only ${Math.round(measured.controls["#prompt"].width)} px wide`);
  return problems;
}

// V6: a resize handle shares at most 2 px with any control; V12: a dialog's close button never sits on its text.
// Only overlaps a user can reach count: the handle must be what a click hits there, and the control what lies under it.
async function handleAndDialogInvariants(page) {
  return page.evaluate(() => {
    const problems = [];
    const shown = (el) => el.checkVisibility() && el.getBoundingClientRect().width > 0;
    const overlap = (a, b) => {
      const w = Math.min(a.right, b.right) - Math.max(a.left, b.left), h = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
      return w > 2 && h > 2 ? { w, h, x: Math.max(a.left, b.left) + w / 2, y: Math.max(a.top, b.top) + h / 2 } : null;
    };
    const name = (el) => el.getAttribute("aria-label") || el.id || el.className;
    const handles = [...document.querySelectorAll('[role="separator"]')].filter(shown);
    const controls = [...document.querySelectorAll("button, a[href], input, select, textarea, summary, [role=tab]")].filter(shown);
    const reachable = (handle, control, point) => {
      const top = document.elementFromPoint(point.x, point.y);
      if (!top || !handle.contains(top)) return false;
      const saved = handle.style.pointerEvents;
      handle.style.pointerEvents = "none";
      const below = document.elementFromPoint(point.x, point.y);
      handle.style.pointerEvents = saved;
      return !!below && control.contains(below);
    };
    for (const handle of handles)
      for (const control of controls) {
        if (handle.contains(control) || control.contains(handle)) continue;
        const hit = overlap(handle.getBoundingClientRect(), control.getBoundingClientRect());
        if (hit && reachable(handle, control, hit))
          problems.push(`V6 handle "${name(handle)}" overlaps "${name(control)}" by ${Math.round(hit.w)}x${Math.round(hit.h)} px`);
      }
    // Text lines, not the whole block: a heading's padding is not text.
    const lines = (el) => {
      const range = document.createRange();
      range.selectNodeContents(el);
      return [...range.getClientRects()].filter((r) => r.width > 0);
    };
    for (const dialog of document.querySelectorAll("dialog[open]"))
      for (const close of dialog.querySelectorAll(".dialog-close, [id$='-close']")) {
        if (!shown(close)) continue;
        for (const text of dialog.querySelectorAll("h2, h3, p, label")) {
          if (!shown(text) || close.contains(text) || text.contains(close)) continue;
          for (const line of lines(text)) {
            const hit = overlap(close.getBoundingClientRect(), line);
            if (hit) problems.push(`V12 "${name(close)}" sits on text "${text.textContent.trim().slice(0, 30)}" by ${Math.round(hit.w)}x${Math.round(hit.h)} px`);
          }
        }
      }
    return problems;
  });
}

// ---------------------------------------------------------------------------- generic lint
function lintProblems(report, keep = ["overlaps", "spills", "beyond", "fields"]) {
  const problems = [];
  for (const key of ["overlaps", "spills", "beyond", "fields"]) if (!keep.includes(key)) report = { ...report, [key]: [] };
  for (const o of report.overlaps) problems.push(`${o.aLabel} ${JSON.stringify(o.aBox)} overlaps ${o.bLabel} ${JSON.stringify(o.bBox)} by ${o.overlap.w}x${o.overlap.h} px`);
  for (const sp of report.spills) if (!KNOWN_NOISE.has(`${sp.kind}|${sp.sel}`)) problems.push(`${sp.sel} "${sp.text}" ${sp.kind}: scroll ${JSON.stringify(sp.scroll)} in client ${JSON.stringify(sp.client)}`);
  for (const b of report.beyond) problems.push(`${b.sel} "${b.text}" ${b.kind} by ${b.excess} px`);
  for (const f of report.fields) problems.push(`${f.sel} text "${f.text}" needs ${f.textWidth} px of ${f.availWidth} px (clipped ${f.textClipped}, under an icon ${f.textRunsUnderIcon}, padding deficit ${f.deficit})`);
  return problems;
}

(async () => {
  const browser = await chromium.launch();
  const failures = [];
  let measurements = 0;
  try {
    for (const theme of THEMES) {
      for (const viewport of MAIN_VIEWPORTS) {
        const context = await browser.newContext({ viewport });
        const page = await context.newPage();
        const pageErrors = [];
        page.on("pageerror", (error) => pageErrors.push(error.message));
        await page.route(origin + "/**", serve);
        await page.addInitScript((value) => {
          localStorage.setItem("keepharness-tour-seen", "0.15.0");
          localStorage.setItem("keepharness:theme:harness", value);
        }, theme);
        await page.goto(origin);
        await page.locator("#startup-gate").waitFor({ state: "hidden" });
        await page.waitForFunction(() => document.getElementById("model-label")?.textContent !== "Loading models…");
        for (const screen of SCREENS) {
          await screen.open(page);
          for (const size of [viewport, ...(viewport === MAIN_VIEWPORTS[1] ? screen.extra || [] : [])]) {
            await page.setViewportSize(size);
            const where = `${theme} ${screen.id}@${size.width}x${size.height}`;
            const problems = [
              ...(screen.lint ? [] : await namedInvariants(page, size)),
              ...(await handleAndDialogInvariants(page)),
              ...lintProblems(await page.evaluate(LINT), screen.lint),
            ];
            if (problems.length) {
              const shot = path.join(SHOTS, where.replace(/\W+/g, "-") + ".png");
              fs.mkdirSync(SHOTS, { recursive: true });
              await page.screenshot({ path: shot });
              failures.push(`${where} (screenshot ${shot})\n    ` + problems.join("\n    "));
            }
            measurements++;
          }
          await page.setViewportSize(viewport);
          await screen.close?.(page);
        }
        for (const message of pageErrors) failures.push(`${theme} ${viewport.width}x${viewport.height}: page error ${message}`);
        await context.close();
      }
    }
  } finally {
    await browser.close();
  }
  if (failures.length) {
    console.error(failures.length + " layout lint failure(s):\n- " + failures.join("\n- "));
    process.exit(1);
  }
  console.log(`PASS: layout lint, ${measurements} measurements (${THEMES.length} themes, ${SCREENS.length} screens)`);
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
