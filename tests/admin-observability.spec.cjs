const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");

(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({
      viewport: { width: 1280, height: 900 },
    });
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    const state = {
      settings: {
        services: {},
        projects: [],
        logins: [],
        uploads_enabled: false,
      },
      inventory: {
        platform: "Linux",
        services: [],
        projects: [],
        network: {},
        tools: [
          {
            name: "bwrap",
            present: false,
            package_hints: {
              "Debian / Ubuntu": "sudo apt install bubblewrap",
              Bazzite: "rpm-ostree install bubblewrap; or distrobox",
            },
          },
          {
            name: "ffmpeg",
            present: true,
            package_hints: { Arch: "sudo pacman -S ffmpeg" },
          },
          {
            name: "prlimit",
            present: true,
            package_hints: { Fedora: "sudo dnf install util-linux" },
          },
        ],
      },
      authentication: {},
      models: {},
      integrations: {},
      operations: [],
      credentials: {},
      status: {
        running: false,
        startup_error: "startup failed",
        last_exit: { code: 9, at: 1000, uptime_seconds: 3 },
      },
    };
    await page.route("http://admin.test/**", async (route) => {
      const url = new URL(route.request().url());
      if (url.pathname.startsWith("/api/")) {
        const data =
          url.pathname === "/api/state"
            ? state
            : url.pathname === "/api/logs"
              ? { lines: ["Bearer [redacted]", "<script>unsafe()</script>"] }
              : {};
        return route.fulfill({ json: data });
      }
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
    await page.goto("http://admin.test/");
    await page.locator("#runtime-diagnostics").waitFor({ timeout: 3000 });
    const diagnostics = await page.locator("#runtime-diagnostics").innerText();
    assert.match(diagnostics, /startup failed.*Last exit: 9/s);
    assert.doesNotMatch(diagnostics, /[.!?] ·/); // The two facts are separate lines, not joined with doubled punctuation.
    assert.doesNotMatch(
      await page.locator("#feedback").innerText(),
      /Could not resume/,
    ); // The startup error is not repeated as a notice.
    assert.equal(
      await page
        .locator("#runtime-diagnostics")
        .evaluate((e) => getComputedStyle(e).borderLeftWidth),
      "3px",
    ); // Styled like the other status rows.
    for (const width of [960, 850, 761]) {
      // 960 is the desktop minimum; the compact header rule starts at 761.
      await page.setViewportSize({ width, height: 640 });
      const centers = await page
        .locator(".header-right > *")
        .evaluateAll((items) =>
          items.map((e) => {
            const r = e.getBoundingClientRect();
            return r.top + r.height / 2;
          }),
        );
      assert.ok(
        Math.max(...centers) - Math.min(...centers) < 2,
        `header controls share one row at ${width}px`,
      );
      assert.ok(
        (await page.locator("#main > header").boundingBox()).height < 80,
        `header stays under 80px at ${width}px`,
      );
    }
    await page.setViewportSize({ width: 1280, height: 900 });
    await page.getByText("Environment and logs", { exact: true }).click();
    const inventory = await page.locator("#environment-tools").innerText();
    assert.match(inventory, /bwrap.*Missing/s);
    assert.match(inventory, /apt install bubblewrap/);
    assert.match(inventory, /ffmpeg.*Available/s);
    assert.match(inventory, /prlimit.*Available/s);
    await page.getByRole("button", { name: "Refresh log tail" }).click();
    await page.waitForFunction(() =>
      document.getElementById("log-tail").textContent.includes("[redacted]"),
    );
    assert.match(
      await page.locator("#log-tail").innerText(),
      /<script>unsafe\(\)<\/script>/,
    );
    assert.equal(await page.locator("#log-tail script").count(), 0);
    for (const width of [390, 768, 1280]) {
      await page.setViewportSize({ width, height: 900 });
      assert.equal(
        await page.evaluate(
          () => document.documentElement.scrollWidth > innerWidth,
        ),
        false,
      );
    }
    assert.deepEqual(errors, []);
    console.log(
      "PASS: admin exit diagnostics, dependency hints, read-only log tail, escaping and responsive layout",
    );
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
