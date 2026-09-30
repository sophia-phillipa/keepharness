const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({
      viewport: { width: 390, height: 844 },
    });
    await page.route("**/v1/**", (route) => {
      const path = new URL(route.request().url()).pathname;
      const data =
        path === "/v1/projects"
          ? { projects: ["sem-projeto"], details: {} }
          : path === "/v1/models"
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
            : path === "/v1/conversations"
              ? { conversations: [] }
              : path === "/v1/version"
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
    await page.addInitScript(() => localStorage.setItem("tail-harness-tour-seen", "0.10.1"));
    await page.goto(origin);
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    assert.equal(
      await page.locator("#account-menu,#forget-approvals").count(),
      0,
    );
    await page.click("#menu");
    await page.locator("#sidebar").waitFor();
    await page.locator("#settings").click();
    const box = await page.locator("#settings-dialog").boundingBox();
    assert(
      box.x >= 0 &&
        box.x + box.width <= 390 &&
        box.y >= 0 &&
        box.y + box.height <= 844,
      "mobile settings dialog remains in viewport",
    );
    await page.screenshot({ path: "/tmp/tail-account-menu-mobile-final.png" });
    await page.click("#settings-close");
    await page.setViewportSize({ width: 1280, height: 860 });
    await page.locator("#settings").focus();
    await page.keyboard.press("Enter");
    assert(await page.locator("#settings-dialog").isVisible());
    assert.match(
      await page.locator("#version").innerText(),
      /Release: fixture/,
    );
    assert(await page.locator("#settings-dialog #admin-shortcut").count());
    await page.keyboard.press("Escape");
    assert.equal(await page.locator("#settings-dialog").isVisible(), false);
    assert(
      await page
        .locator("#settings")
        .evaluate((el) => el === document.activeElement),
    );
    assert.deepEqual(errors, []);
    const motion = await page.evaluate(() => {
      const previous = active,
        lastId = last,
        el = document.createElement("article"),
        body = document.createElement("div");
      body.className = "text chat-bubble";
      el.append(body);
      document.querySelector("#messages").append(el);
      active = { body, el };
      body.rawAnswer = renderAnswer(body, "Partial answer");
      updateMotion("answer_delta");
      const partial = !!body.querySelector("p .response-motion");
      updateMotion("thinking");
      const thinking = !!body.querySelector("p .response-motion");
      updateMotion("completed");
      const completed = !!body.querySelector(".response-motion");
      status("Completed");
      // F-62: routine progress is announced but kept off screen.
      const routine =
        document.querySelector("#status").className +
        ": " +
        document.querySelector("#status").textContent;
      status("Couldn't finish: test error");
      const error = document.querySelector("#status").textContent;
      active = previous;
      last = lastId;
      el.remove();
      return { partial, thinking, completed, routine, error };
    });
    assert.deepEqual(motion, {
      partial: true,
      thinking: true,
      completed: false,
      routine: "visually-hidden: Completed",
      error: "Couldn't finish: test error",
    });
    console.log(
      "PASS: inline answer motion survives thinking, clears at completion, routine status stays off screen, error stays visible; mobile settings dialog stays within viewport.",
    );
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
