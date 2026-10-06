// D-031: the composer warns before sending an image to a model that cannot read it.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const path = require("node:path");

const MODELS = [
  { id: "deepseek-flash", name: "DeepSeek", backend: "deepseek", efforts: ["configured"], permissions: { upload: true }, capabilities: { images: false } },
  { id: "gpt-6-astra", name: "Astra", backend: "codex", efforts: ["medium"], permissions: { upload: true }, capabilities: { images: true } },
];

async function fixture(browser, uploadReply) {
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  page.setDefaultTimeout(5000);
  page.jobPosts = [];
  await page.route("http://panel.test/**", (route) => {
    const pathname = new URL(route.request().url()).pathname;
    return route.fulfill({
      path: path.join(__dirname, "..", pathname.startsWith("/assets/") ? "harness_ui" : "agent_service", pathname === "/" ? "index.html" : pathname),
    });
  });
  await page.route("**/v1/**", (route) => {
    const p = new URL(route.request().url()).pathname;
    if (p === "/v1/files") return route.fulfill(uploadReply);
    if (p === "/v1/jobs" && route.request().method() === "POST") page.jobPosts.push(p);
    const data = p === "/v1/projects" ? { projects: ["sem-projeto"], details: {} }
      : p === "/v1/models" ? { models: MODELS, uploads_enabled: true }
      : p === "/v1/conversations" ? { conversations: [] }
      : {};
    return route.fulfill({ json: data });
  });
  await page.addInitScript(() => localStorage.setItem("keepharness-tour-seen", "0.16.0"));
  await page.goto("http://panel.test/");
  await page.locator("#startup-gate").waitFor({ state: "hidden" });
  return page;
}
const attachImage = (page) => page.evaluate(() => upload([new File(["x"], "photo.png", { type: "image/png" })]));

(async () => {
  const browser = await chromium.launch();
  try {
    const ok = { json: { file_id: "f1", preview_url: "data:image/gif;base64,R0lGODlhAQABAAAAACw=" } };
    let page = await fixture(browser, ok);
    const warning = page.getByTestId("image-capability-warning");
    assert(await warning.isHidden(), "no warning without an image");
    await page.fill("#prompt", "Describe this");
    await attachImage(page);
    await warning.waitFor({ state: "visible" });
    assert.equal(await warning.getAttribute("role"), "alert");
    assert.match(await warning.innerText(), /DeepSeek.* can't read images\. Choose a model that reads images, or remove the image\./);
    assert(await page.locator("#send").isDisabled(), "send disabled while the warning stands");

    // Enter and Ask again honour the block too: no job is posted while the warning stands.
    await page.focus("#prompt");
    await page.keyboard.press("Enter");
    await page.evaluate(() => {
      $("prompt").value = "";
      const q = Object.assign(document.createElement("article"), { className: "message user", textContent: "Describe this" });
      const a = Object.assign(document.createElement("article"), { className: "message assistant" });
      $("messages").append(q, a);
      askAgainButton(a).onclick();
    });
    await page.waitForTimeout(300);
    assert.equal(page.jobPosts.length, 0, "no POST /v1/jobs while the image warning stands");
    assert.equal(await page.evaluate(() => document.activeElement.id), "image-capability-choose");

    // Switching to a model that reads images clears the warning; switching back brings it back.
    await page.evaluate(() => { $("model").value = "gpt-6-astra"; $("model").dispatchEvent(new Event("change")); });
    await warning.waitFor({ state: "hidden" });
    assert(await page.locator("#send").isEnabled());
    await page.evaluate(() => { $("model").value = "deepseek-flash"; $("model").dispatchEvent(new Event("change")); });
    await warning.waitFor({ state: "visible" });

    // Choose another model opens the picker; Remove image clears the warning.
    await page.getByTestId("image-capability-choose").click();
    await page.waitForFunction(() => document.querySelector("#model-menu").matches(":popover-open"));
    await page.keyboard.press("Escape");
    await page.getByTestId("image-capability-remove").click();
    await warning.waitFor({ state: "hidden" });
    assert.equal(await page.evaluate(() => files.length), 0);
    assert(await page.locator("#send").isEnabled());
    await page.close();

    // A non-image attachment never warns.
    page = await fixture(browser, { json: { file_id: "f2" } });
    await page.fill("#prompt", "Read this");
    await page.evaluate(() => upload([new File(["x"], "n.txt", { type: "text/plain" })]));
    await page.waitForFunction(() => files.length === 1);
    assert(await page.getByTestId("image-capability-warning").isHidden());
    await page.close();

    // Upload refusal: clear copy naming the model, with Choose another model.
    for (const code of ["model_images_unavailable", "images_require_native_service", "local_vision_not_enabled"]) {
      page = await fixture(browser, { status: 422, json: { code } });
      await attachImage(page);
      const w = page.getByTestId("image-capability-warning");
      await w.waitFor({ state: "visible" });
      assert.match(await w.innerText(), /DeepSeek/);
      assert.match(await w.innerText(), /Choose another model/);
      assert.doesNotMatch(await page.locator("#status").innerText(), /does not offer image reading/);
      // The refusal is information only: a text-only message to the same model still sends.
      await page.fill("#prompt", "Just text");
      assert(await page.locator("#send").isEnabled(), "send enabled with no image attached after " + code);
      await page.locator("#send").click();
      await page.waitForTimeout(300);
      assert.equal(page.jobPosts.length, 1, "text-only message sends after " + code);
      await page.close();
    }
    const copy = await (async () => {
      page = await fixture(browser, ok);
      const text = await page.evaluate(() => attachmentError("select_model_for_image"));
      await page.close();
      return text;
    })();
    assert.match(copy, /Choose a model that reads images before attaching one/);
  } finally {
    await browser.close();
  }
})().catch((e) => { console.error(e); process.exit(1); });
