// 15 simulated attachment profiles, varied per round; real browser, synthetic API.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict"),
  fs = require("node:fs/promises"),
  path = require("node:path");
(async () => {
  const browser = await chromium.launch(),
    results = [];
  const round = Number(process.env.GAUNTLET_ROUND || 1);
  try {
    for (let slot = 0; slot < 15; slot++) {
      const profile = ((slot + round - 1) % 15) + 1;
      const page = await browser.newPage({
        viewport: { width: round % 2 ? 1280 : 390, height: 900 },
      });
      page.setDefaultTimeout(4000);
      const requests = [],
        jobs = [],
        errors = [];
      let reject = false,
        hold = false,
        release;
      page.on("pageerror", (e) => errors.push(e.message));
      await page.route("http://attachments.test/**", async (route) => {
        const u = new URL(route.request().url()),
          p = u.pathname;
        let data = {};
        if (p.startsWith("/v1/")) {
          if (p === "/v1/projects") data = { projects: ["p", "sem-projeto"] };
          if (p === "/v1/models")
            data = {
              uploads_enabled: true,
              models: [
                {
                  id: "fixture",
                  backend: "codex",
                  efforts: ["low"],
                  permissions: { upload: true },
                  execution_modes: ["native", "scoped"],
                },
                {
                  id: "other",
                  backend: "claude",
                  efforts: ["low"],
                  permissions: { upload: true },
                  execution_modes: ["native", "scoped"],
                },
              ],
            };
          if (p === "/v1/conversations") data = { conversations: [] };
          if (p === "/v1/files" || p === "/v1/project-files/attach") {
            requests.push({
              path: p,
              params: Object.fromEntries(u.searchParams),
              body: p.endsWith("/attach")
                ? route.request().postDataJSON()
                : null,
            });
            if (hold) await new Promise((resolve) => (release = resolve));
            if (reject)
              return route.fulfill({
                status: 422,
                json: { code: "unsupported_binary_format" },
              });
            data =
              p === "/v1/files"
                ? { file_id: "f" + requests.length }
                : {
                    attachments: [
                      { file_id: "folder-file", name: "folder/note.txt" },
                    ],
                    skipped: [
                      { path: "bad.exe", reason: "unsupported_binary_format" },
                      {
                        path: "changed.txt",
                        reason: "attachment_source_unavailable",
                      },
                      { path: "limit.txt", reason: "file_limit" },
                    ],
                  };
          }
          if (p === "/v1/jobs" && route.request().method() === "POST") {
            jobs.push(route.request().postDataJSON());
            data = { job_id: "j" };
          }
          if (p.endsWith("/events")) return;
          return route.fulfill({ json: data });
        }
        const file = p === "/" ? "index.html" : p.slice(1);
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
      const add = async (name = "note.txt", body = "round " + round) => {
        await page.locator("#file").setInputFiles({
          name,
          mimeType: "text/plain",
          buffer: Buffer.from(body),
        });
        await page.waitForFunction(() => !uploads);
      };
      try {
        await page.addInitScript(() => localStorage.setItem("tail-harness-tour-seen", "0.13.13"));
        await page.goto("http://attachments.test");
        await page.locator("#startup-gate").waitFor({ state: "hidden" });
        if (profile === 1) {
          await add();
          assert.equal(
            await page.locator("#attachments .attachment").count(),
            1,
          );
        }
        if (profile === 2) {
          await page.evaluate(() =>
            upload(
              Array.from(
                { length: 21 },
                (_, i) => new File(["x"], "f" + i + ".txt"),
              ),
            ),
          );
          assert.equal(requests.length, 20);
          assert.equal(
            await page.locator("#attachments .attachment").count(),
            20,
          );
        }
        if (profile === 3) {
          await page.fill("#prompt", "Read unattached-file-" + round + ".txt");
          await page.click("#send");
          await page.waitForFunction(() => !submitting);
          assert.deepEqual(jobs[0].file_ids, []);
        }
        if (profile === 4) {
          await add("first.txt");
          await add("second.txt");
          await page.locator("#attachments button").first().focus();
          await page.keyboard.press("Enter");
          assert.equal(
            await page.locator("#attachments .attachment").count(),
            1,
          );
          assert(
            await page.evaluate(
              () =>
                document.activeElement.closest("#attachments") ||
                document.activeElement.id === "attach",
            ),
            "removing attachment must keep useful keyboard focus",
          );
        }
        if (profile === 5) {
          await add("very-long-file-" + "x".repeat(100) + ".txt");
          assert(
            await page.evaluate(
              () => document.documentElement.scrollWidth <= innerWidth,
            ),
          );
          await page.reload();
          await page.locator("#startup-gate").waitFor({ state: "hidden" });
          assert.equal(
            await page.locator("#attachments .attachment").count(),
            1,
          );
        }
        if (profile === 6) {
          await page.evaluate(() =>
            upload([
              {
                name: "large.png",
                type: "image/png",
                size: 100 * 1024 * 1024 + 1,
              },
            ]),
          );
          assert.equal(
            requests.length,
            0,
            "oversized known image should be rejected before network transfer",
          );
          assert.match(await page.locator("#status").innerText(), /100 MiB/);
        }
        if (profile === 7) {
          hold = true;
          await page.locator("#file").setInputFiles({
            name: "slow.txt",
            mimeType: "text/plain",
            buffer: Buffer.from("x"),
          });
          await page.waitForFunction(() => uploads === 1);
          assert(
            await page.locator("#model-trigger").isDisabled(),
            "model change during upload must be locked",
          );
          assert(await page.locator("#isolation-toggle").isDisabled());
          release();
          await page.waitForFunction(() => !uploads);
          assert(await page.locator("#model-trigger").isEnabled());
        }
        if (profile === 8) {
          reject = true;
          await page.fill("#prompt", "Preserve draft");
          await add("bad.exe");
          assert.equal(await page.inputValue("#prompt"), "Preserve draft");
          assert.equal(
            await page.locator("#attachments .attachment").count(),
            0,
          );
          assert.match(
            await page.locator("#messages").innerText(),
            /was skipped/,
          );
        }
        if (profile === 9) {
          await page.evaluate(() =>
            attachSelectedProjectFiles({ root_id: "home", paths: ["mixed"] }),
          );
          assert.equal(
            await page.locator("#attachments .attachment").count(),
            1,
          );
          assert.match(await page.locator("#messages").innerText(), /bad.exe/);
          assert.match(
            await page.locator("#messages").innerText(),
            /changed.txt/,
          );
          assert.match(
            await page.locator("#messages").innerText(),
            /limit.txt/,
          );
        }
        if (profile === 10) {
          await add("zero.txt", "");
          assert.equal(
            await page.locator("#attachments .attachment").count(),
            1,
          );
          await page.locator("#attachments button").click();
          assert.equal(
            await page.locator("#attachments .attachment").count(),
            0,
          );
        }
        if (profile === 11) {
          await page.click("#isolation-toggle");
          await add();
          assert.equal(
            requests[0].params.execution_mode,
            "scoped",
            "raw upload must carry selected execution mode",
          );
        }
        if (profile === 12) {
          await page.evaluate(() => {
            window.attachmentTimeouts = [];
            const timeout = AbortSignal.timeout.bind(AbortSignal);
            AbortSignal.timeout = (ms) => {
              window.attachmentTimeouts.push(ms);
              return timeout(ms);
            };
          });
          await page.click("#isolation-toggle");
          await page.evaluate(() =>
            attachSelectedProjectFiles({
              root_id: "home",
              paths: ["note.txt"],
            }),
          );
          assert.equal(
            requests[0].body.execution_mode,
            "scoped",
            "folder import must carry selected execution mode",
          );
          assert(
            (await page.evaluate(() => window.attachmentTimeouts)).some(
              (ms) => ms >= 8200000,
            ),
            "folder audio must wait for transcription beyond the 30-second API default",
          );
        }
        if (profile === 13) {
          await page.evaluate(() => {
            const dt = new DataTransfer();
            dt.items.add(new File(["dragged"], "drag.txt"));
            document
              .querySelector("#dropzone")
              .dispatchEvent(
                new DragEvent("drop", { bubbles: true, dataTransfer: dt }),
              );
          });
          await page.waitForFunction(() => files.length === 1 && !uploads);
          assert.equal(requests.length, 1);
        }
        if (profile === 14) {
          await add("<img onerror=alert(1)>.txt");
          assert.equal(await page.locator("#attachments img").count(), 0);
          assert.match(await page.locator("#attachments").innerText(), /<img/);
        }
        if (profile === 15) {
          await page.evaluate(() => {
            const dt = new DataTransfer();
            dt.setData("application/x-tail-authorized-project-files", "null");
            document
              .querySelector("#dropzone")
              .dispatchEvent(
                new DragEvent("drop", { bubbles: true, dataTransfer: dt }),
              );
          });
          assert.equal(requests.length, 0);
          assert.match(
            await page.locator("#status").innerText(),
            /isn't valid/,
          );
        }
        assert.deepEqual(errors, []);
        results.push({ profile, round, status: "pass" });
      } catch (e) {
        results.push({ profile, round, status: "fail", error: e.message });
      }
      console.log(
        "P" +
          profile +
          " R" +
          round +
          " " +
          results.at(-1).status +
          (results.at(-1).error
            ? " " + results.at(-1).error.split("\n")[0]
            : ""),
      );
      await page.close();
    }
  } finally {
    await browser.close();
    if (process.env.GAUNTLET_OUTPUT)
      await fs.writeFile(
        process.env.GAUNTLET_OUTPUT,
        JSON.stringify(results, null, 2),
      );
  }
  if (results.some((r) => r.status === "fail")) process.exitCode = 1;
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
