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
                  {
                    id: "codex-fixture",
                    name: "Codex fixture",
                    backend: "codex",
                    efforts: ["low"],
                  },
                ],
                providers: { local: true, codex: true },
                uploads_enabled: false,
              }
            : pathname === "/v1/conversations"
              ? {
                  conversations: ["completed", "running"].map((state, i) => ({
                    id: "c" + i,
                    title: "Conversation " + i,
                    project: "sem-projeto",
                    state,
                    execution: { backend: "local", model: "fixture" },
                  })),
                }
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
              file.startsWith("assets/") ? "../harness_ui" : "../agent_service",
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
    await page.addInitScript(() => localStorage.setItem("keepharness-tour-seen", "0.16.0"));
    await page.goto(origin);
    await page.locator("#startup-gate").waitFor({ state: "hidden" });

    const results = [];
    const check = (id, ok, detail) => results.push({ id, ok, detail });

    // Exercise the operator's actual resize step, including mutations that omit
    // each key press. A Home/End-only step must not pass as arrow-key coverage.
    const keyboardArea = require("./operator/areas/15-keyboard.cjs");
    const resizeStep = async (omitKey) => keyboardArea.run({
      page,
      async step(id, _description, run) {
        if (id === "sidebar-resize-keys") await run();
      },
      async press(key) {
        if (key !== omitKey) await page.keyboard.press(key);
      },
      async until(predicate, message) {
        for (let i = 0; i < 20; i++) {
          if (await predicate()) return;
          await page.waitForTimeout(25);
        }
        assert.fail(message);
      },
    });
    await assert.rejects(resizeStep("ArrowRight"), /ArrowRight/, "removing ArrowRight must fail the operator's directional assertion");
    await assert.rejects(resizeStep("ArrowLeft"), /ArrowLeft/, "removing ArrowLeft must fail the operator's directional assertion");
    await resizeStep();
    check("P5-W15 operator real arrows resize directionally; omitted presses fail", true);

    // P5-20: each dialog opens from the keyboard, Escape closes it, and focus returns
    // to the button that opened it.
    async function openWithKeyboardAndEscape(openerId, dialogId) {
      await page.locator("#" + openerId).focus();
      await page.keyboard.press("Enter");
      if (openerId === "settings") {
        // The Settings button opens a submenu; its first item opens the dialog.
        await page.waitForFunction(() => document.activeElement?.getAttribute("role") === "menuitem");
        await page.keyboard.press("Enter");
        await page.locator("#" + dialogId).waitFor({ state: "visible" });
      }
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
    await page.waitForFunction(() => document.activeElement?.getAttribute("role") === "menuitem");
    await page.keyboard.press("Enter"); // the Settings submenu opens first; its first item opens the dialog
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
    await page.waitForFunction(() => document.activeElement?.getAttribute("role") === "menuitem");
    await page.keyboard.press("Enter"); // the Settings submenu opens first; its first item opens the dialog
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

    // UX-R1-7 / L58: every visible text clears WCAG AA (4.5:1, 3:1 for large text) in every
    // palette, light and dark, measured from computed colors; status text is at least 11 px.
    const measureText = () =>
      page.evaluate(() => {
        const parse = (c) => {
          const m = c.match(/rgba?\(([^)]+)\)|color\(srgb ([^)]+)\)/);
          if (!m) return null;
          let v = (m[1] || m[2]).split(/[ ,/]+/).map(Number);
          if (m[2]) v = [v[0] * 255, v[1] * 255, v[2] * 255, v[3] ?? 1];
          return [v[0], v[1], v[2], v[3] ?? 1];
        };
        const over = (top, bottom) =>
          [0, 1, 2].map((i) => top[i] * top[3] + bottom[i] * (1 - top[3])).concat(1);
        const lum = (c) => {
          const f = (x) => ((x /= 255) <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4);
          return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]);
        };
        const backdrop = (el) => {
          let bg = over(parse(getComputedStyle(document.documentElement).backgroundColor), [255, 255, 255, 1]);
          const chain = [];
          for (let e = el; e; e = e.parentElement) chain.unshift(e);
          for (const e of chain) {
            const c = parse(getComputedStyle(e).backgroundColor);
            if (c && c[3] > 0) bg = over(c, bg);
          }
          return bg;
        };
        const rows = [];
        for (const el of document.querySelectorAll("body *")) {
          if (![...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim())) continue;
          const box = el.getBoundingClientRect(),
            style = getComputedStyle(el);
          if (box.width <= 1 || box.height <= 1 || style.visibility === "hidden" || +style.opacity === 0) continue;
          if (box.right < 0 || box.bottom < 0 || box.left > innerWidth || box.top > innerHeight) continue;
          if (el.closest("[hidden],[aria-hidden=true],[inert]")) continue;
          const background = backdrop(el);
          let fg = parse(style.color);
          if (fg[3] < 1) fg = over(fg, background);
          const [hi, lo] = [lum(fg), lum(background)].sort((a, b) => b - a);
          const size = parseFloat(style.fontSize);
          rows.push({
            name: el.id ? "#" + el.id : el.tagName.toLowerCase() + "." + String(el.className).split(" ")[0],
            text: el.textContent.trim().slice(0, 24),
            size,
            ratio: +((hi + 0.05) / (lo + 0.05)).toFixed(2),
            need: size >= 24 || (size >= 18.66 && +style.fontWeight >= 700) ? 3 : 4.5,
          });
        }
        return rows;
      });
    const palettes = await page.evaluate(() => HarnessTheme.themes.map((t) => t.id));
    assert.equal(palettes.length, 8);
    const contrastStates = [
      ["page", async () => {}],
      ["quota panel", async () => page.evaluate(() => setQuotaOpen(true))],
      ["search", async () => {
        await page.evaluate(() => setQuotaOpen(false));
        await page.keyboard.press("Control+k");
      }],
      ["settings", async () => {
        await page.keyboard.press("Escape");
        await page.keyboard.press("Control+,");
      }],
    ];
    const lowContrast = [];
    for (const [stateName, enter] of contrastStates) {
      await enter();
      for (const palette of palettes) {
        await page.evaluate((id) => HarnessTheme.apply(id), palette);
        await page.waitForTimeout(60);
        for (const row of await measureText()) {
          if (row.ratio < row.need) lowContrast.push(`${palette}/${stateName}: ${row.ratio} ${row.name} "${row.text}"`);
          if (row.size < 11) lowContrast.push(`${palette}/${stateName}: ${row.size}px ${row.name} "${row.text}"`);
        }
      }
    }
    check("all palettes: text >= 4.5:1 (3:1 large) and >= 11 px", lowContrast.length === 0, [...new Set(lowContrast)].slice(0, 12));

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
