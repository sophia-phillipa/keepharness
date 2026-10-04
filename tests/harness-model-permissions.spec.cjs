const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
(async () => {
  const browser = await chromium.launch();
  try {
    const p = await browser.newPage();
    let uploads = 0,
      jobs = 0,
      audioRoute;
    const errors = [];
    p.on("pageerror", (e) => errors.push(e.message));
    await p.route("**/v1/**", async (r) => {
      const path = new URL(r.request().url()).pathname;
      let data = {};
      if (path === "/v1/projects")
        data = { projects: ["sem-projeto", "demo"], details: {} };
      if (path === "/v1/models")
        data = {
          uploads_enabled: true,
          providers: { local: true },
          models: [
            {
              id: "qwen-local",
              backend: "local",
              efforts: ["configured"],
              permissions: { upload: true, internet: true, shell: true },
            },
            {
              id: "gemma-local",
              backend: "local",
              efforts: ["configured"],
              permissions: { upload: false, internet: false, shell: false },
            },
          ],
        };
      if (path === "/v1/conversations") data = { conversations: [] };
      if (path === "/v1/files") {
        uploads++;
        if (r.request().headers()["x-filename"] === "speech.wav") {
          audioRoute = r;
          return;
        }
        data = { file_id: "fixture-file" };
      }
      if (path === "/v1/jobs") {
        jobs++;
        throw Error("Must not execute inference in permission test");
      }
      if (
        path === "/v1/models" &&
        new URL(r.request().url()).searchParams.get("project_id") === "demo"
      )
        data.models = data.models.map((m) => ({
          ...m,
          permissions: { ...m.permissions, upload: true, internet: true },
        }));
      await r.fulfill({ json: data });
    });
    await p.addInitScript(() => localStorage.setItem("keepharness-tour-seen", "0.15.0"));
    await p.goto(process.env.HARNESS_URL || "http://127.0.0.1:18196/");
    await p.waitForFunction(
      () =>
        document.querySelector("#models-retry").textContent === "Check again",
    );
    assert(await p.locator("#attach").isEnabled());
    assert.match(
      await p.locator("#model-permissions").innerText(),
      /Internet allowed/,
    );
    await p.locator("#file").setInputFiles({
      name: "test.txt",
      mimeType: "text/plain",
      buffer: Buffer.from("Test file without personal data"),
    });
    await p.waitForFunction(() =>
      document.querySelector("#attachments").textContent.includes("test.txt"),
    );
    assert.equal(uploads, 1);
    await p.clock.install();
    await p.locator("#file").setInputFiles({
      name: "speech.wav",
      mimeType: "audio/wav",
      buffer: Buffer.from("fixture audio"),
    });
    await p.waitForFunction(() =>
      document.querySelector("#status").textContent.includes("speech.wav"),
    );
    for (let attempt = 0; !audioRoute && attempt < 100; attempt++)
      await new Promise((resolve) => setTimeout(resolve, 10));
    assert(audioRoute, "audio request must reach the fixture");
    await p.clock.fastForward(31000);
    assert.match(await p.locator("#status").innerText(), /speech.wav/);
    await audioRoute.fulfill({ json: { file_id: "audio-fixture" } });
    await p.waitForFunction(() =>
      document.querySelector("#attachments").textContent.includes("speech.wav"),
    );
    assert.equal(uploads, 2);

    await p.selectOption("#model", "gemma-local");
    assert(await p.locator("#attach").isDisabled());
    assert.match(
      await p.locator("#model-permissions").innerText(),
      /Internet disabled/,
    );
    await p.fill("#prompt", "Analyze attachment");
    await p.click("#send");
    assert.match(
      await p.locator("#status").innerText(),
      /doesn't allow attachments/,
    );
    assert.equal(jobs, 0);
    assert.equal(await p.locator("#prompt").inputValue(), "Analyze attachment");
    await p.selectOption("#model", "qwen-local");
    assert(await p.locator("#attach").isEnabled());
    assert.match(await p.locator("#attachments").innerText(), /test.txt/);
    await p.selectOption("#model", "gemma-local");
    await p.selectOption("#project", "demo", { force: true });
    await p.waitForFunction(() => !document.querySelector("#attach").disabled);
    assert.match(
      await p.locator("#model-permissions").innerText(),
      /Internet allowed/,
    );
    await p.selectOption("#project", "sem-projeto", { force: true });
    await p.waitForFunction(() => document.querySelector("#attach").disabled);
    assert.match(
      await p.locator("#model-permissions").innerText(),
      /Internet disabled/,
    );
    assert.deepEqual(errors, []);
    console.log(
      "PASS: Qwen attachments and internet permissions, isolated Gemma restrictions, draft and attachment retained on model switch",
    );
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
