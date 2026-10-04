const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");

(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({
      viewport: { width: 1440, height: 1000 },
    });
    const fs = require("node:fs/promises"),
      path = require("node:path");
    await page.route("http://admin.test/**", async (route) => {
      const url = new URL(route.request().url());
      if (url.pathname.startsWith("/api/")) return route.fallback();
      const file = url.pathname === "/" ? "index.html" : url.pathname.slice(1);
      return route.fulfill({
        body: await fs.readFile(
          path.join(
            __dirname,
            file.startsWith("assets/") ? "../harness_ui" : "../control",
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
    page.on("pageerror", (e) => errors.push(e.message));
    let failSave = false,
      failCatalog = false,
      releaseCatalog = null,
      catalogCalls = 0;
    const service = () => ({
      added: false,
      enabled: false,
      models: [],
      projects: ["sem-projeto"],
      permissions: {
        read: false,
        write: false,
        upload: false,
        shell: false,
        internet: false,
        hooks: false,
      },
      mode: "scoped",
      integrations: [],
    });
    const state = {
      settings: {
        services: { codex: service(), claude: service() },
        projects: [],
        logins: [],
        port: 8095,
        tailnet_port: 8095,
        uploads_enabled: false,
      },
      inventory: {
        platform: "Linux",
        services: [
          { id: "codex", name: "Codex CLI", found: true },
          { id: "claude", name: "Claude Code", found: true },
        ],
        projects: [{ name: "Demo", path: "/workspace/demo" }],
        network: { online: true },
      },
      authentication: {},
      models: {},
      integrations: {
        codex: [
          { id: "mcp:drive", name: "Drive", kind: "mcp" },
          { id: "plugin:github@openai", name: "github@openai", kind: "plugin" },
        ],
        claude: [{ id: "mcp:linear", name: "Linear", kind: "mcp" }],
      },
      operations: [],
      credentials: {},
      status: { running: false },
    };
    await page.route("**/api/**", async (route) => {
      const url = new URL(route.request().url());
      if (url.pathname.endsWith("/check")) {
        state.authentication.claude = true;
        return route.fulfill({
          json: { authenticated: true, models: { sonnet: ["configured"] } },
        });
      }
      if (url.pathname.endsWith("/integration-catalog")) {
        catalogCalls++;
        if (releaseCatalog) await releaseCatalog;
        if (failCatalog)
          return route.fulfill({
            status: 503,
            json: { error: "Catalog temporarily unavailable" },
          });
      }
      if (url.pathname.endsWith("/settings")) {
        if (failSave)
          return route.fulfill({
            status: 400,
            json: { error: "Simulated failure" },
          });
        state.settings = route.request().postDataJSON();
      }
      const result = url.pathname.endsWith("/integration-catalog")
        ? {
            items: [
              {
                id: "plugin:sentry@official",
                name: "Sentry",
                kind: "plugin",
                status: "available",
              },
              {
                id: "plugin:github@openai",
                name: "GitHub",
                kind: "plugin",
                status: "installed",
              },
            ],
            warnings: [],
          }
        : url.pathname.endsWith("/state")
          ? state
          : url.pathname.endsWith("/scan")
            ? state.inventory
            : {};
      await route.fulfill({ json: result });
    });

    const results = [],
      failures = [];
    const check = (id, ok, detail) => {
      results.push({ id, ok, detail });
      if (!ok) failures.push(id);
    };
    await page.goto("http://admin.test/");
    await page.locator("[data-panel=providers]").click();
    await page.click("#add-provider");
    await page
      .locator("#provider-options")
      .getByText("Codex CLI", { exact: false })
      .click();
    const tabs = page.locator("#inspector-tabs");
    const connectors = tabs.getByRole("button", {
      name: "Plugins",
      exact: true,
    });
    await connectors.focus();
    await page.keyboard.press("Enter");
    await page
      .getByRole("button", { name: "Install Sentry", exact: true })
      .waitFor();
    check(
      "P4-S1",
      (await connectors.getAttribute("aria-pressed")) === "true",
      "Keyboard Enter activates section and exposes selected state",
    );
    for (const width of [320, 390, 768, 1440]) {
      await page.setViewportSize({ width, height: 1000 });
      for (const section of ["Plugins", "Connectors"]) {
        await tabs.getByRole("button", { name: section, exact: true }).click();
        await page.waitForTimeout(250);
        const bounds = await page.evaluate(() => ({
          width: innerWidth,
          doc: document.documentElement.scrollWidth,
          clipped: [
            ...document.querySelectorAll(
              "#provider-dialog button,#provider-dialog input,#provider-dialog select",
            ),
          ]
            .filter((e) => e.getClientRects().length)
            .filter((e) => {
              const r = e.getBoundingClientRect();
              return r.left < 0 || r.right > innerWidth + 1;
            })
            .map((e) => e.id || e.textContent),
        }));
        check(
          `P5-layout-${width}-${section}`,
          bounds.doc <= width && bounds.clipped.length === 0,
          bounds,
        );
        await page.screenshot({
          path: `/tmp/tester-a11y-${width}-${section === "Permissions" ? "permissions" : "integrations"}.png`,
          fullPage: true,
        });
      }
    }

    for (const width of [320, 390, 768, 1440]) {
      await page.setViewportSize({ width, height: 1000 });
      const operation = await page
        .locator("#integration-action")
        .evaluate((e) => {
          const s = getComputedStyle(e),
            c = document.createElement("canvas").getContext("2d");
          c.font = s.font;
          return {
            text: e.selectedOptions[0].text,
            width: e.clientWidth,
            available:
              e.clientWidth -
              parseFloat(s.paddingLeft) -
              parseFloat(s.paddingRight),
            textWidth: c.measureText(e.selectedOptions[0].text).width,
          };
        });

      check(
        `P5-operation-label-${width}`,
        operation.available >= operation.textWidth,
        operation,
      );
      await page.locator("#integration-transport").selectOption("stdio");
      const transport = await page
        .locator("#integration-transport")
        .evaluate((e) => {
          const s = getComputedStyle(e),
            c = document.createElement("canvas").getContext("2d");
          c.font = s.font;
          return {
            text: e.selectedOptions[0].text,
            available:
              e.clientWidth -
              parseFloat(s.paddingLeft) -
              parseFloat(s.paddingRight),
            textWidth: c.measureText(e.selectedOptions[0].text).width,
          };
        });
      check(
        `P5-transport-label-${width}`,
        transport.available >= transport.textWidth,
        transport,
      );
    }

    await page.locator("#catalog-search").focus();
    await page.evaluate(() => scrollTo(0, 0));
    const skip = await page.locator(".skip").evaluate((e) => ({
      top: e.getBoundingClientRect().top,
      bottom: e.getBoundingClientRect().bottom,
      position: getComputedStyle(e).position,
      focused: document.activeElement === e,
    }));
    check("P4-skip-unfocused", skip.bottom <= 0 && !skip.focused, skip);
    await page.screenshot({ path: "/tmp/tester-a11y-skip.png" });
    const tabStates = await tabs.locator("button").evaluateAll((es) =>
      es.map((e) => ({
        text: e.textContent,
        pressed: e.getAttribute("aria-pressed"),
        background: getComputedStyle(e).backgroundColor,
        color: getComputedStyle(e).color,
        class: e.className,
      })),
    );
    check(
      "P4-tab-state",
      tabStates.filter((x) => x.pressed === "true").length === 1 &&
        tabStates[2].pressed === "true",
      tabStates,
    );
    await page.setViewportSize({ width: 390, height: 844 });
    await tabs.getByRole("button", { name: "Plugins", exact: true }).click();
    check(
      "P5-catalog-kind",
      await page.locator("#catalog-items .subtle").first().isVisible(),
      "Catalog provider/kind metadata visible on mobile",
    );
    await page.evaluate(() => HarnessTheme.apply("arizona", false));
    await page.waitForTimeout(250);
    await page.screenshot({
      path: "/tmp/tester-a11y-arizona-mobile.png",
      fullPage: true,
    });

    let release;
    releaseCatalog = new Promise((resolve) => (release = resolve));
    await page.locator("#catalog-refresh").focus();
    await page.keyboard.press("Enter");
    await page
      .locator("#catalog-status")
      .filter({ hasText: "Searching" })
      .waitFor();
    check(
      "P4-S2",
      (await page.locator("#catalog-status").getAttribute("role")) ===
        "status" &&
        (await page.locator("#catalog-items").getAttribute("aria-busy")) ===
          "true",
      "Loading status live region and busy contents",
    );
    release();
    releaseCatalog = null;
    await page.waitForFunction(
      () =>
        document.querySelector("#catalog-items").getAttribute("aria-busy") ===
        "false",
    );
    failCatalog = true;
    await page.locator("#catalog-refresh").click();
    await page
      .locator("#catalog-status")
      .filter({ hasText: "Catalog temporarily unavailable" })
      .waitFor();
    await page.waitForTimeout(250);
    await page.screenshot({
      path: "/tmp/tester-a11y-mobile-error.png",
      fullPage: true,
    });
    const skipError = await page.locator(".skip").evaluate((e) => ({
      top: e.getBoundingClientRect().top,
      bottom: e.getBoundingClientRect().bottom,
      focused: document.activeElement === e,
      scroll: scrollY,
    }));
    check(
      "P4-skip-error",
      skipError.bottom <= 0 && !skipError.focused,
      skipError,
    );
    await page.screenshot({ path: "/tmp/tester-a11y-error-viewport.png" });
    check(
      "P5-error-layout",
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
      "Long catalogue failure/status remains within viewport",
    );
    check(
      "P5-S2",
      !(await page.locator("#catalog-refresh").isDisabled()),
      "Retry remains enabled after network failure",
    );
    await tabs.getByRole("button", { name: "Plugins", exact: true }).click();
    failCatalog = false;
    await page.locator("#catalog-refresh").focus();
    await page.keyboard.press("Enter");
    await page
      .getByRole("button", { name: "Install Sentry", exact: true })
      .waitFor();
    await page.locator("#catalog-search").focus();
    await page.keyboard.type("Sentry");
    check(
      "P4-S3",
      (await page.locator("#catalog-items article").count()) === 1,
      "Keyboard search after recovery",
    );
    await page.keyboard.press("Tab");
    const focus = await page.evaluate(() => {
      const e = document.activeElement,
        s = getComputedStyle(e);
      return {
        id: e.id,
        name: e.textContent,
        outline: s.outline,
        shadow: s.boxShadow,
      };
    });
    check(
      "P4-focus",
      focus.outline !== "none" && !focus.outline.includes("0px"),
      focus,
    );
    for (const theme of ["violet-bordeaux", "arizona"]) {
      await page.evaluate((theme) => HarnessTheme.apply(theme, false), theme);
      const contrast = await page.locator("#catalog-status").evaluate((e) => {
        let b = e;
        while (b && getComputedStyle(b).backgroundColor === "rgba(0, 0, 0, 0)")
          b = b.parentElement;
        return {
          foreground: getComputedStyle(e).color,
          background: getComputedStyle(b || document.body).backgroundColor,
        };
      });
      function lum(rgb) {
        return rgb
          .match(/[\d.]+/g)
          .slice(0, 3)
          .map(Number)
          .map((v) => {
            v /= 255;
            return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
          })
          .reduce((a, v, i) => a + v * [0.2126, 0.7152, 0.0722][i], 0);
      }
      const fg = lum(contrast.foreground),
        bg = lum(contrast.background),
        ratio = (Math.max(fg, bg) + 0.05) / (Math.min(fg, bg) + 0.05);
      check("P4-contrast-" + theme, ratio >= 4.5, { ...contrast, ratio });
    }
    check("runtime", errors.length === 0, errors);
    await fs.writeFile(
      "/tmp/tester-accessibility.json",
      JSON.stringify(results, null, 2),
    );
    console.log(JSON.stringify(results, null, 2));
    assert.deepEqual(failures, []);
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
