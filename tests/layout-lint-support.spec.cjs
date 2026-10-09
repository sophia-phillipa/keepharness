// Self-test of tests/support/layout-lint.js on small synthetic pages (no server, no app).
// It pins what the lint must report (a real overlap, text under a real icon) and what it must
// ignore: controls that sit under a modal dialog or a full-page view are not reachable, so
// they neither overlap what is on top of them nor cover a field of the dialog.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");

const LINT = fs.readFileSync(
  path.join(__dirname, "support", "layout-lint.js"),
  "utf8",
);
const BASE = `<style>
  body { margin: 0; font: 14px sans-serif }
  .abs { position: absolute }
  dialog { margin: 0 }
  input { box-sizing: border-box; width: 300px; height: 32px; padding: 0 8px }
</style>`;

const CASES = [
  {
    name: "two real controls that overlap are reported",
    html: `<button class="abs" style="left:20px;top:20px;width:120px;height:40px">First</button>
           <button class="abs" style="left:100px;top:30px;width:120px;height:40px">Second</button>`,
    expect: (r) =>
      assert.equal(r.overlaps.length, 1, JSON.stringify(r.overlaps)),
  },
  {
    name: "text that runs under a real icon button is reported",
    html: `<div class="abs" style="left:20px;top:20px">
             <input id="f" value="a long value that needs the whole field to be shown in full" style="width:200px">
             <button class="abs" style="left:150px;top:0;width:50px;height:32px">x</button></div>`,
    expect: (r) => assert.equal(r.fields.length, 1, JSON.stringify(r.fields)),
  },
  {
    name: "controls behind a modal dialog do not overlap its contents",
    html: `<button class="abs" style="left:20px;top:20px;width:200px;height:40px">Behind A</button>
           <dialog id="d" style="left:10px;top:10px;width:300px;height:120px"><button style="width:150px;height:40px">Inside</button></dialog>
           <script>document.getElementById("d").showModal()</script>`,
    expect: (r) => assert.deepEqual(r.overlaps, []),
  },
  {
    name: "a field in a modal dialog is not covered by the page behind it",
    html: `<button class="abs" style="left:20px;top:20px;width:260px;height:40px">Behind</button>
           <dialog id="d" style="left:10px;top:10px;width:340px;height:120px"><input id="f" value="x"></dialog>
           <script>document.getElementById("d").showModal()</script>`,
    expect: (r) => assert.deepEqual(r.fields, []),
  },
  {
    name: "a control under a full-page view is not an overlap even where the view's control sits",
    html: `<div id="page" tabindex="0" class="abs" style="left:0;top:0;width:480px;height:300px">Messages</div>
           <div class="abs" style="left:0;top:0;width:480px;height:300px;background:#fff">
             <button class="abs" style="left:350px;top:20px;width:150px;height:40px">On top</button></div>`,
    expect: (r) => assert.deepEqual(r.overlaps, []),
  },
  {
    name: "a control floating over a focusable region (the message log) is not an overlap",
    html: `<div role="region" aria-label="Messages" tabindex="0" class="abs" style="left:0;top:0;width:300px;height:300px">Messages</div>
           <button class="abs" style="left:250px;top:20px;width:150px;height:40px">Popover</button>`,
    expect: (r) => assert.deepEqual(r.overlaps, []),
  },
  {
    name: "a count painted transparent (the notification dot) does not spill",
    html: `<b class="abs" style="left:20px;top:20px;width:8px;height:8px;overflow:hidden;color:transparent;background:#38f">12</b>`,
    expect: (r) => assert.deepEqual(r.spills, []),
  },
  {
    name: "a visible count clipped by its box is reported",
    html: `<b class="abs" style="left:20px;top:20px;width:8px;height:8px;overflow:hidden;background:#38f">12345</b>`,
    expect: (r) => assert.equal(r.spills.length, 1, JSON.stringify(r.spills)),
  },
  {
    name: "a long textarea value scrolls while a clipped placeholder is reported",
    html: `<textarea id="v" class="abs" style="left:20px;top:20px;width:150px;height:40px;white-space:pre">a long line of text that runs past the right edge of this box</textarea>
           <textarea id="p" class="abs" style="left:20px;top:100px;width:150px;height:40px;white-space:pre" placeholder="a long placeholder that runs past the right edge of this box"></textarea>`,
    expect: (r) =>
      assert.deepEqual(
        r.fields.map((f) => f.sel),
        ["#p"],
      ),
  },
];

(async () => {
  const browser = await chromium.launch();
  const failures = [];
  try {
    const page = await browser.newPage({
      viewport: { width: 500, height: 400 },
    });
    for (const c of CASES) {
      await page.setContent(BASE + c.html);
      const report = await page.evaluate(LINT);
      try {
        c.expect(report);
      } catch (error) {
        failures.push(`${c.name}: ${error.message}`);
      }
    }
  } finally {
    await browser.close();
  }
  if (failures.length) {
    console.error(
      failures.length +
        " layout lint self-test failure(s):\n- " +
        failures.join("\n- "),
    );
    process.exit(1);
  }
  console.log(`PASS: layout lint self-test, ${CASES.length} cases`);
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
