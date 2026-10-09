// Desktop only: the packaged app's splash, window title and icon, the Admin panel and the
// way back, and what closing the window does. Runs its own desktop window in any target.
"use strict";
const { execFileSync } = require("node:child_process");
const fs = require("node:fs");
const { launchDesktop } = require("../lib/targets.cjs");
const { home } = require("../lib/app.cjs");

module.exports = {
  id: "desktop",
  title: "Desktop app (packaged)",
  async run(op) {
    const own = op.session.target !== "desktop";
    const base = op.session.base;
    let desktop = own ? null : op.session;
    const previous = op.session;

    await op.step("launch", "Launch the packaged desktop app attached to this instance", async () => {
      if (!op.options.app || !fs.existsSync(op.options.app)) op.skip("no packaged desktop binary (pass --app)");
      if (!process.env.DISPLAY && !process.env.WAYLAND_DISPLAY) op.skip("no display: run through scripts/operator-suite.sh (xvfb)");
      if (own) {
        desktop = await launchDesktop(op.options, base, op.options.adminUrl);
        op.session = desktop;
      }
      await desktop.page.locator("#startup-gate").waitFor({ state: "hidden", timeout: 40000 });
      // A fresh desktop profile starts the tour a moment after loading; mark it seen first.
      await home(op);
    }, { critical: true, lint: false });

    try {
      await op.step("splash", "The brand splash showed first", async () => {
        op.check(/splash\.html$/.test(desktop.splashUrl || ""), "the first window was " + desktop.splashUrl);
      });

      await op.step("window-title", "The window is titled KeepHarness and is visible", async () => {
        const windows = await desktop.windows();
        const main = windows.find((w) => w.url.startsWith(base));
        op.check(main && main.visible, "no visible main window");
        op.check(/^KeepHarness/.test(main.title), `the window title is "${main.title}"`);
        op.check(!windows.some((w) => w.url.startsWith("file:") && w.visible), "the splash is still visible");
      });

      await op.step("window-icon", "The window is matched to the KeepHarness icon", async () => {
        const main = (await desktop.windows()).find((w) => w.url.startsWith(base));
        let output = "";
        try {
          output = execFileSync("xprop", ["-id", String(main.handle), "_NET_WM_ICON", "WM_CLASS"], { encoding: "utf8", timeout: 5000 });
        } catch {
          op.skip("xprop could not read the window (no X11 display)");
        }
        // An embedded icon ("Icon (W x H):" art or numbers) is best. Without a window manager
        // (Xvfb) Chromium sets none, so the window class that the desktop entry's
        // StartupWMClass=keepharness maps to the KeepHarness icon is the check that remains.
        const embedded = /_NET_WM_ICON\(CARDINAL\) =\s*(Icon \(|\d)/.test(output);
        op.check(embedded || /WM_CLASS\(STRING\) = "keepharness"/.test(output), "no icon and no keepharness window class: " + output.slice(0, 120));
      }, { lint: false });

      await op.step("no-dev-menu", "The window has no developer menu (Reload, DevTools)", async () => {
        const menu = await desktop.app.evaluate(({ Menu }) => {
          const items = [];
          const walk = (m) => m?.items.forEach((i) => (items.push(i.label || i.role || ""), walk(i.submenu)));
          walk(Menu.getApplicationMenu());
          return items;
        });
        op.check(!menu.some((label) => /toggledevtools|Toggle Developer Tools|reload/i.test(label)), "the stock menu offers " + menu.filter((l) => /dev|reload/i.test(l)).join(", "));
      }, { recover: false });

      await op.step("admin-navigate", "Admin opens the admin panel in the window", async () => {
        const page = desktop.page;
        // Admin is Settings > System > Providers: the admin is framed in the Settings dialog of the chat window.
        await op.click(page.locator("#settings"));
        await op.click(page.locator("#settings-menu").getByRole("menuitem", { name: "Providers", exact: true }));
        await op.until(async () => page.frames().some((f) => f.url().startsWith(op.options.adminUrl)), "Settings did not show the admin; the frames are " + page.frames().map((f) => f.url()).join(", "));
        // Framed in Settings, the admin hides its own navigation and shows the Providers page.
        await op.see(page.frameLocator("#admin-frame").getByRole("heading", { name: "AI Providers" }));
      });

      await op.step("admin-back", "Open harness brings the chat back", async () => {
        const page = desktop.page;
        const link = page.locator("#open-harness");
        // The link is disabled while the admin reports its harness stopped (or has no status yet).
        if ((await link.isVisible()) && (await link.getAttribute("aria-disabled")) !== "true") await op.click(link);
        else await page.goBack();
        await op.until(async () => page.url().startsWith(base), "the window did not return to the chat");
        await page.locator("#startup-gate").waitFor({ state: "hidden", timeout: 30000 });
        await op.see(page.locator("#prompt"));
      });

      await op.step("min-size", "The window cannot shrink below a usable size", async () => {
        const [width, height] = await desktop.app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().find((w) => w.isVisible()).getMinimumSize());
        op.check(width >= 720 && height >= 500, `minimum size ${width}x${height}`);
      });

      await op.step("close-keeps-service", "Closing the window leaves the service running", async () => {
        await desktop.close();
        desktop.closed = true;
        const response = await fetch(base + "/v1/version").catch(() => null);
        // /v1/version needs a session on a real instance (401); any HTTP answer means it is still up.
        op.check(response && response.status < 500, "the harness stopped when the window closed");
      }, { lint: false });
    } finally {
      if (desktop && !desktop.closed && own) await desktop.close();
      op.session = previous;
    }
  },
};
