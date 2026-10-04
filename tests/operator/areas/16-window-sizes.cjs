// Window sizes 1440, 1024, 800 and 390 px: the layout lint runs on every step, plus named
// checks for the composer, the status strip, the sidebar share and small-screen reach.
"use strict";
const { home } = require("../lib/app.cjs");

const SIZES = [
  [1440, 900],
  [1024, 768],
  [800, 600],
  [390, 844],
];

async function composerClear(op) {
  const boxes = await op.page.evaluate(() =>
    ["attach", "access-trigger", "model-trigger", "effort-trigger", "send", "dropzone"].map((id) => {
      const el = document.getElementById(id);
      const r = el && el.checkVisibility() ? el.getBoundingClientRect() : null;
      return [id, r && { l: r.left, r: r.right, t: r.top, b: r.bottom }];
    }),
  );
  const map = Object.fromEntries(boxes);
  const ids = Object.keys(map).filter((id) => id !== "dropzone" && map[id]);
  for (let i = 0; i < ids.length; i++)
    for (let j = i + 1; j < ids.length; j++) {
      const a = map[ids[i]], b = map[ids[j]];
      const w = Math.min(a.r, b.r) - Math.max(a.l, b.l), h = Math.min(a.b, b.b) - Math.max(a.t, b.t);
      op.check(!(w > 1 && h > 1), `#${ids[i]} overlaps #${ids[j]} by ${w.toFixed(0)}x${h.toFixed(0)} px`);
    }
}

module.exports = {
  id: "window-sizes",
  title: "Window sizes 1440 / 1024 / 800 / 390",
  async run(op) {
    const page = op.page;
    try {
      for (const [width, height] of SIZES) {
        const tag = String(width);
        let applied = true;
        await op.step(tag + "-resize", `Resize the window to ${width}×${height}`, async () => {
          applied = await op.session.resize(width, height);
          if (!applied) op.skip(`below the ${op.session.target} window's minimum size`);
          await home(op);
        });
        if (!applied) continue;

        await op.step(tag + "-no-page-scroll", `${width}px: the page never scrolls sideways`, async () => {
          const extra = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
          op.check(extra <= 1, `the page scrolls ${extra}px sideways`);
        });

        await op.step(tag + "-composer", `${width}px: new-chat composer controls do not overlap`, async () => {
          await composerClear(op);
        });

        await op.step(tag + "-conversation", `${width}px: an existing conversation, composer clear`, async () => {
          const last = page.locator("#sidebar").getByRole("button").filter({ hasText: /Completed/ }).filter({ visible: true }).first();
          if (width <= 620 && !(await last.isVisible())) await op.click(page.locator("#menu"));
          if (!(await last.count())) op.skip("no finished conversation to open");
          await op.click(last);
          await op.see(page.locator("#messages").getByRole("article").first());
          await composerClear(op);
        });

        await op.step(tag + "-strip", `${width}px: the status strip fits on one line`, async () => {
          const fits = await page.evaluate(() => {
            const strip = document.getElementById("run-status-strip") || document.getElementById("run-status-toggle")?.closest("[aria-label='Run status']");
            if (!strip) return true;
            const r = strip.getBoundingClientRect();
            return strip.scrollHeight <= strip.clientHeight + 1 && [...strip.querySelectorAll("button")].every((b) => {
              const c = b.getBoundingClientRect();
              return !c.width || (c.top >= r.top - 1 && c.bottom <= r.bottom + 1);
            });
          });
          op.check(fits, "the status strip content overflows its bar");
        });

        if (width === 800)
          await op.step("800-sidebar-share", "800px: the sidebar takes at most 30% of the window", async () => {
            const share = await page.locator("#sidebar").evaluate((el) => (el.checkVisibility() ? el.getBoundingClientRect().width : 0) / innerWidth);
            op.check(share <= 0.3, `the sidebar takes ${(share * 100).toFixed(0)}% of the window`);
          });

        if (width === 390) {
          await op.step("390-drawer", "390px: the conversations drawer opens from the menu and Escape closes it", async () => {
            await op.click(page.locator("#menu"));
            await op.until(async () => (await page.locator("#menu").getAttribute("aria-expanded")) === "true", "the drawer did not open");
            await op.press("Escape");
            await op.until(async () => (await page.locator("#menu").getAttribute("aria-expanded")) === "false", "Escape did not close the drawer");
          });

          await op.step("390-space-scheduled", "390px: Space and Scheduled are still reachable", async () => {
            const reachable = async (id) => (await page.locator("#" + id).isVisible()) || (await page.locator("#menu").click().then(() => page.locator("#" + id).isVisible()));
            op.check((await reachable("rail-space")) && (await reachable("rail-scheduled")), "Space or Scheduled cannot be reached at 390px");
          });

          await op.step("390-agent-card", "390px: an agent conversation's card stays readable", async () => {
            const control = page.locator("#persona-control");
            if (!(await control.isVisible())) op.skip("no agent conversation is open");
            const box = await control.boundingBox();
            op.check(box.height <= 0.25 * 844, `the agent card is ${box.height}px tall`);
          });
        }
      }
    } finally {
      await op.session.resize(op.options.viewport.width, op.options.viewport.height).catch(() => {});
    }
  },
};
