const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");

// P5-20/P5-21/P5-22: keyboard access to the three top-level dialogs, forced-colors
// contrast for prompt resource highlights, and reduced-motion gates. Reuses the
// harness-motion.spec.cjs route-mock recipe (static files + minimal /v1/* fixtures).
(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({
      viewport: { width: 1280, height: 860 },
    });
    await page.route("**/v1/**", (route) => {
      const pathname = new URL(route.request().url()).pathname;
      const data =
        pathname === "/v1/projects"
          ? { projects: ["sem-projeto"], details: {} }
          : pathname === "/v1/models"
            ? {
                models: [
                  {
                    id: "fixture",
                    name: "Fixture",
                    backend: "local",
                    efforts: ["low"],
                  },
                ],
                providers: { local: true },
                uploads_enabled: false,
              }
            : pathname === "/v1/conversations"
              ? { conversations: [] }
              : pathname === "/v1/version"
                ? { version: "fixture", build: "fixture" }
                : {};
      return route.fulfill({ json: data });
    });
    const origin = process.env.HARNESS_URL || "http://panel.test";
    if (!process.env.HARNESS_URL)
      await page.route(origin + "/**", async (route) => {
        const pathname = new URL(route.request().url()).pathname;
        if (pathname.startsWith("/v1/")) return route.fallback();
        const file = pathname === "/" ? "index.html" : pathname.slice(1);
        return route.fulfill({
          body: await fs.readFile(
            path.join(
              __dirname,
              file.startsWith("assets/") ? "../tail_ui" : "../agent_service",
              file,
            ),
          ),
          contentType: file.endsWith(".js")
            ? "text/javascript"
            : file.endsWith(".css")
              ? "text/css"
              : file.endsWith(".svg")
                ? "image/svg+xml"
                : "text/html",
        });
      });
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.addInitScript(() => localStorage.setItem("tail-harness-tour-seen", "0.11.0"));
    await page.goto(origin);
    await page.locator("#startup-gate").waitFor({ state: "hidden" });

    const results = [];
    const check = (id, ok, detail) => results.push({ id, ok, detail });

    // P5-20: each dialog opens from the keyboard, Escape closes it, and focus returns
    // to the button that opened it.
    async function openWithKeyboardAndEscape(openerId, dialogId) {
      await page.locator("#" + openerId).focus();
      await page.keyboard.press("Enter");
      const opened = await page.locator("#" + dialogId).isVisible();
      await page.keyboard.press("Escape");
      const closed = !(await page.locator("#" + dialogId).isVisible());
      const focusReturned = await page
        .locator("#" + openerId)
        .evaluate((el) => el === document.activeElement);
      return { opened, closed, focusReturned };
    }

    const settings = await openWithKeyboardAndEscape(
      "settings",
      "settings-dialog",
    );
    check(
      "settings-dialog keyboard open/escape/focus-return",
      settings.opened && settings.closed && settings.focusReturned,
      settings,
    );

    const search = await openWithKeyboardAndEscape(
      "search-conversations",
      "conversation-search-dialog",
    );
    check(
      "conversation-search-dialog keyboard open/escape/focus-return",
      search.opened && search.closed && search.focusReturned,
      search,
    );

    // setup-dialog is reached from inside settings-dialog.
    await page.locator("#settings").focus();
    await page.keyboard.press("Enter");
    await page.locator("#setup").focus();
    await page.keyboard.press("Enter");
    const setupOpened = await page.locator("#setup-dialog").isVisible();
    await page.keyboard.press("Escape");
    const setupClosed = !(await page.locator("#setup-dialog").isVisible());
    const settingsStillOpen = await page
      .locator("#settings-dialog")
      .isVisible();
    const setupFocusReturned = await page
      .locator("#setup")
      .evaluate((el) => el === document.activeElement);
    await page.keyboard.press("Escape");
    const settingsClosedToo = !(await page
      .locator("#settings-dialog")
      .isVisible());
    check(
      "setup-dialog keyboard open/escape/focus-return, nested under settings-dialog",
      setupOpened &&
        setupClosed &&
        settingsStillOpen &&
        setupFocusReturned &&
        settingsClosedToo,
      {
        setupOpened,
        setupClosed,
        settingsStillOpen,
        setupFocusReturned,
        settingsClosedToo,
      },
    );

    // Tab x20 inside the open settings dialog never leaves it (F-32): Tab on the last
    // focusable control wraps to the first, and Shift+Tab on the first wraps to the last.
    await page.locator("#settings").focus();
    await page.keyboard.press("Enter");
    let escapes = 0;
    let escapee = null;
    for (let i = 0; i < 20; i++) {
      await page.keyboard.press("Tab");
      const active = await page.evaluate(() => {
        const dialog = document.querySelector("dialog[open]");
        const el = document.activeElement;
        return {
          inside: !!dialog && (dialog === el || dialog.contains(el)),
          tag: el ? el.tagName : null,
          id: el ? el.id : null,
        };
      });
      if (!active.inside) {
        escapes++;
        if (!escapee) escapee = active;
      }
    }
    check("Tab x20 stays inside the open settings dialog", escapes === 0, {
      escapes,
      escapee,
    });
    await page.locator("#settings-close").focus();
    await page.keyboard.press("Shift+Tab");
    const shiftTabTarget = await page.evaluate(
      () => document.activeElement?.id,
    );
    check(
      "Shift+Tab on the first settings control wraps to the last one",
      shiftTabTarget === "catalog-refresh",
      shiftTabTarget,
    );
    await page.keyboard.press("Escape");

    // P5-21: forced-colors contrast for prompt-resource highlights, and a visible
    // focus outline on the focused element.
    await page.emulateMedia({ forcedColors: "active" });
    await page.evaluate(() => {
      document.querySelector("#prompt-highlights").innerHTML =
        '<span class="prompt-resource">@resource</span>';
    });
    const { resourceColor, linkTextColor } = await page.evaluate(() => {
      const probe = document.createElement("span");
      probe.style.color = "LinkText";
      document.body.append(probe);
      const linkText = getComputedStyle(probe).color;
      probe.remove();
      const resource = getComputedStyle(
        document.querySelector("#prompt-highlights .prompt-resource"),
      ).color;
      return { resourceColor: resource, linkTextColor: linkText };
    });
    check(
      "prompt-resource color resolves to the system LinkText color under forced-colors",
      resourceColor === linkTextColor,
      { resourceColor, linkTextColor },
    );

    await page.locator("#settings").focus();
    const focusOutline = await page
      .locator("#settings")
      .evaluate((el) => getComputedStyle(el).outlineStyle);
    check(
      "the focused element keeps a non-none outline under forced-colors",
      focusOutline !== "none",
      focusOutline,
    );
    await page.emulateMedia({ forcedColors: "none" });

    // P5-22: reduced motion turns off the scroll animation, the "working" indicator
    // animation and the startup-gate blur.
    await page.emulateMedia({ reducedMotion: "reduce" });
    const messagesScroll = await page
      .locator("#messages")
      .evaluate((el) => getComputedStyle(el).scrollBehavior);
    check(
      "reduced motion sets #messages scroll-behavior:auto",
      messagesScroll === "auto",
      messagesScroll,
    );

    const indicatorAnimation = await page.evaluate(() => {
      const probe = document.createElement("span");
      probe.className = "response-motion";
      probe.dataset.motion = "answer";
      document.body.append(probe);
      const name = getComputedStyle(probe, "::after").animationName;
      probe.remove();
      return name;
    });
    check(
      "reduced motion sets the answer indicator's ::after animation-name:none",
      indicatorAnimation === "none",
      indicatorAnimation,
    );

    const gateBackdrop = await page.evaluate(() => {
      const probe = document.createElement("div");
      probe.className = "startup-gate";
      document.body.append(probe);
      const value = getComputedStyle(probe).backdropFilter;
      probe.remove();
      return value;
    });
    check(
      "reduced motion sets .startup-gate backdrop-filter:none",
      gateBackdrop === "none",
      gateBackdrop,
    );

    const failures = results.filter((r) => !r.ok);
    for (const r of results)
      console.log((r.ok ? "PASS " : "FAIL ") + r.id, r.detail ?? "");
    assert.deepEqual(errors, []);
    assert.equal(failures.length, 0, JSON.stringify(failures));
    console.log(
      "PASS: harness dialogs are keyboard-operable with focus return and tab containment; forced-colors and reduced-motion gates hold.",
    );
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
