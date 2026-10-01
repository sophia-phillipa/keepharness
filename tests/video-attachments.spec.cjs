// Real browser, synthetic model metadata and upload responses; no inference.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict"),
  path = require("node:path");
(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({
        viewport: { width: 390, height: 850 },
      }),
      requests = [],
      jobs = [];
    let reject = false;
    page.setDefaultTimeout(5000);
    await page.route("http://video.test/**", async (route) => {
      const u = new URL(route.request().url()),
        p = u.pathname;
      if (!p.startsWith("/v1/"))
        return route.fulfill({
          path: path.join(
            __dirname,
            "..",
            p.startsWith("/assets/") ? "tail_ui" : "agent_service",
            p === "/" ? "index.html" : p,
          ),
        });
      let data = {};
      if (p === "/v1/projects") data = { projects: ["p"] };
      if (p === "/v1/models")
        data = {
          uploads_enabled: true,
          models: [
            {
              id: "vision",
              backend: "codex",
              efforts: ["low"],
              permissions: { upload: true },
              execution_modes: ["native", "scoped"],
              capabilities: {
                video: true,
                video_execution_modes: ["native"],
                video_transcription: true,
              },
            },
            {
              id: "text",
              backend: "deepseek",
              efforts: ["low"],
              permissions: { upload: true },
              execution_modes: ["native"],
              capabilities: { video: false },
            },
            {
              id: "local-vision",
              backend: "local",
              efforts: ["low"],
              permissions: { upload: true },
              execution_modes: ["scoped"],
              capabilities: {
                video: true,
                video_execution_modes: ["scoped"],
                video_transcription: false,
              },
            },
          ],
        };
      if (p === "/v1/conversations") data = { conversations: [] };
      if (p === "/v1/jobs" && route.request().method() === "POST")
        jobs.push(route.request().postDataJSON());
      if (p === "/v1/project-files/attach")
        data = {
          attachments: [],
          skipped: [
            { path: "folder/lesson.mp4", reason: "model_video_unavailable" },
          ],
        };
      if (p === "/v1/files") {
        requests.push(Object.fromEntries(u.searchParams));
        if (reject)
          return route.fulfill({
            status: 422,
            json: { code: "invalid_video" },
          });
        data = { file_id: "f" + requests.length };
      }
      return route.fulfill({ json: data });
    });
    await page.addInitScript(() => localStorage.setItem("tail-harness-tour-seen", "0.13.6"));
    await page.goto("http://video.test");
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    const add = () =>
      page.evaluate(() =>
        upload([new File(["fixture"], "lesson.mp4", { type: "video/mp4" })]),
      );
    const select = async (id) => {
      await page.selectOption("#model", id);
      await page.evaluate(() => updateComposer());
    };
    await select("vision");
    assert.match(
      await page.locator("#attach").getAttribute("title"),
      /100 MiB.*MP4/,
    );
    await add();
    assert.equal(requests.length, 1);
    assert.equal(requests[0].model, "vision");
    console.log("P1 discover and attach supported MP4: pass");
    await page.locator("#attachments button").first().focus();
    await page.keyboard.press("Enter");
    assert.equal(await page.locator("#attachments .attachment").count(), 0);
    assert.equal(
      await page.locator("#attach").getAttribute("aria-label"),
      "Attach file",
    );
    console.log("P4 accessible remove and attachment help: pass");
    await page.click("#isolation-toggle");
    await add();
    assert.equal(requests.length, 1);
    assert.match(
      await page.locator("#status").innerText(),
      /MP4.*model.*mode/i,
    );
    await page.click("#isolation-toggle");
    await add();
    assert.equal(requests.length, 2);
    console.log("P2 mode switch rejects and recovers: pass");
    await select("text");
    await add();
    assert.equal(requests.length, 2);
    await page.fill("#prompt", "Interpret the video");
    await page.click("#send");
    assert.equal(
      jobs.length,
      0,
      "pending MP4 must not be sent to an incompatible model",
    );
    assert.match(
      await page.locator("#status").innerText(),
      /remove the videos/,
    );
    await page.evaluate(() =>
      attachSelectedProjectFiles({
        root_id: "home",
        paths: ["folder/lesson.mp4"],
      }),
    );
    assert.match(
      await page.locator("#messages").innerText(),
      /lesson.mp4.*support.*MP4/,
    );
    await select("vision");
    await page.click("#isolation-toggle");
    await select("local-vision");
    await add();
    assert.equal(requests.length, 3);
    assert.equal(requests[2].execution_mode, "scoped");
    console.log("P6 runtime model capability and local mode: pass");
    await page.fill("#prompt", "Preserve my draft");
    reject = true;
    await add();
    assert.equal(await page.inputValue("#prompt"), "Preserve my draft");
    assert.match(await page.locator("#status").innerText(), /video/i);
    console.log("P3 invalid video preserves draft: pass");
    await page.reload();
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    assert.equal(await page.inputValue("#prompt"), "Preserve my draft");
    assert(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    );
    console.log("P5 mobile reload preserves draft and attachments: pass");
    assert.match(await page.locator("#attachment-help").innerText(), /MP4/);
    assert.match(
      await page.locator("#attach").getAttribute("title"),
      /100 MiB/,
    );
    await page.evaluate(() =>
      upload([
        { name: "large.mp4", type: "video/mp4", size: 100 * 1024 * 1024 + 1 },
      ]),
    );
    assert.match(await page.locator("#status").innerText(), /100 MiB/);
    const before = requests.length;
    reject = false;
    await page.evaluate(async () => {
      // Override only browser File.size: verify admission without allocating 100 MiB.
      for (const name of ["exact.txt", "exact.png", "exact.mp3"]) {
        const file = new File(["fixture"], name);
        Object.defineProperty(file, "size", { value: 100 * 1024 * 1024 });
        await upload([file]);
      }
      for (const name of ["large.txt", "large.png", "large.mp3"])
        await upload([{ name, size: 100 * 1024 * 1024 + 1 }]);
    });
    assert.equal(
      requests.length,
      before + 3,
      "all file types share the inclusive limit",
    );
    console.log("P7 consistent format and size feedback: pass");
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
