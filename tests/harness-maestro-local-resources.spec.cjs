// Real HTTP persistence/gates and real browser controls; inference alone is synthetic.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict"),
  fs = require("node:fs/promises"),
  os = require("node:os"),
  path = require("node:path"),
  { spawn } = require("node:child_process");
(async () => {
  const folder = await fs.mkdtemp(path.join(os.tmpdir(), "maestro-entry-"));
  const proc = spawn(
    process.env.PYTHON || path.join(__dirname, "../.venv/bin/python"),
    ["-m", "tests.maestro_browser_fixture", folder, "local"],
    {
      cwd: path.join(__dirname, ".."),
      env: { ...process.env, HOME: folder },
      stdio: ["ignore", "pipe", "pipe"],
    },
  );
  let log = "";
  proc.stderr.on("data", (s) => (log += s));
  let browser;
  try {
    let ready;
    for (let i = 0; i < 100; i++) {
      try {
        ready = JSON.parse(await fs.readFile(path.join(folder, "ready.json")));
        const r = await fetch("http://127.0.0.1:" + ready.port + "/v1/version");
        if (r.ok) break;
      } catch {}
      if (proc.exitCode !== null) throw Error(log);
      await new Promise((r) => setTimeout(r, 50));
    }
    assert(ready, log);
    const origin = "http://127.0.0.1:" + ready.port;
    browser = await chromium.launch();
    const context = await browser.newContext();
    await context.addCookies([
      { name: "harness_session", value: ready.session, url: origin },
    ]);
    const page = await context.newPage();
    page.setDefaultTimeout(8000);
    await page.addInitScript(
      (version) => localStorage.setItem("tail-harness-tour-seen", version),
      (
        await fs.readFile(
          path.join(__dirname, "../agent_service/VERSION"),
          "utf8",
        )
      ).trim(),
    );
    await page.goto(origin);
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    const posts = [];
    page.on("request", (r) => {
      if (r.method() === "POST" && r.url() === origin + "/v1/jobs")
        posts.push(r.postDataJSON());
    });
    await page.locator("#model").selectOption({ label: "Maestro (auto plan)" });
    await page.locator("#prompt").fill("/local-review");
    await page.getByRole("option", { name: /local-review/ }).click();
    await page.locator("#send").click();
    await page.waitForFunction(() => !submitting && !busy && !!parent);
    assert.equal(await page.evaluate(() => executionMode), "scoped");
    assert.equal(await page.evaluate(() => selected().backend), "local");
    const first = await page.evaluate(() => parent);
    await page.locator("#prompt").fill("Continue this synthetic review");
    await page.locator("#send").click();
    await page.waitForFunction(
      (id) => !submitting && !busy && parent !== id,
      first,
    );
    assert.equal(posts.at(-1).backend, "local");
    assert.equal(posts.at(-1).parent_job_id, first);
    assert(
      (await page.locator("#messages").innerText()).includes(
        "Synthetic review complete",
      ),
    );
    await page.reload();
    await page.waitForFunction(() => !loading && !!parent);
    assert.equal(await page.evaluate(() => executionMode), "scoped");
    console.log(
      "PASS Local Maestro resources synchronize execution mode and allow immediate continuation",
    );
  } finally {
    if (browser) await browser.close();
    proc.kill("SIGTERM");
    await new Promise((resolve) => {
      if (proc.exitCode !== null) return resolve();
      proc.once("exit", resolve);
      setTimeout(() => proc.kill("SIGKILL"), 2000).unref();
    });
    await fs.rm(folder, { recursive: true, force: true });
  }
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
