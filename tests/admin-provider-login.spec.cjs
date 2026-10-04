// L53 / PRD-R2-1: the sign-in operation shows a clean link, the one-time code on its own, what
// to enable first, and a title that names the provider (D20).
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");

(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 860 } });
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    const job = {
      id: "3f9a1c0000",
      state: "running",
      accepts_input: false,
      provider: "codex",
      kind: "provider-login",
      // What Operations stores once it has removed the escape codes Codex prints.
      output:
        "Follow these steps to sign in with ChatGPT using device code authorization:\n" +
        "1. Open this link in your browser and sign in to your account\n   https://auth.openai.com/codex/device\n" +
        "2. Enter this one-time code (expires in 15 minutes)\n   ABCD-12345\n",
    };
    await page.route("http://admin.test/**", async (route) => {
      const url = new URL(route.request().url());
      if (url.pathname.startsWith("/api/"))
        return route.fulfill({
          json:
            url.pathname === "/api/state"
              ? {
                  settings: { services: {}, projects: [], logins: [], port: 8095 },
                  inventory: { services: [{ id: "codex", name: "Codex", found: true }], projects: [], network: {} },
                  authentication: {},
                  models: {},
                  integrations: {},
                  operations: [job],
                  credentials: {},
                  status: { running: false },
                }
              : {},
        });
      const file = url.pathname === "/" ? "index.html" : url.pathname.slice(1);
      return route.fulfill({
        body: await fs.readFile(path.join(__dirname, file.startsWith("assets/") ? "../harness_ui" : "../control", file)),
        contentType: file.endsWith(".js") ? "text/javascript" : file.endsWith(".css") ? "text/css" : file.endsWith(".svg") ? "image/svg+xml" : "text/html",
      });
    });
    await page.goto("http://admin.test/#providers");
    await page.evaluate(() => {
      document.getElementById("operation-dialog").showModal();
      return pollOperations();
    });
    const operation = page.locator("#operations details").first();
    await operation.waitFor();
    assert.match(await operation.locator("summary").innerText(), /^Codex sign-in · In progress$/);
    assert.equal(await operation.getByTestId("login-code").innerText(), "ABCD-12345");
    assert.match(await operation.innerText(), /ChatGPT security settings/);
    const link = operation.getByRole("link", { name: /Open authorization/ });
    assert.equal(await link.count(), 1, "one link, not one per mention");
    assert.equal(await link.getAttribute("href"), "https://auth.openai.com/codex/device");
    assert.doesNotMatch(await operation.innerText(), /\u001b|\[9\dm/);

    // A job that is not a Codex sign-in shows neither a code box nor the Codex note.
    job.provider = "claude";
    job.output = "Open https://claude.ai/oauth/authorize?code=true and paste the code";
    await page.evaluate(() => pollOperations());
    await page.getByText("Claude Code sign-in").waitFor();
    assert.equal(await page.getByTestId("login-code").count(), 0);
    assert.doesNotMatch(await page.locator("#operations").innerText(), /ChatGPT security settings/);
    assert.deepEqual(errors, []);
    console.log("PASS: sign-in operation shows a clean link, the device code on its own and the ChatGPT prerequisite");
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
