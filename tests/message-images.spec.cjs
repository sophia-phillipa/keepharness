const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const fs = require("node:fs"),
  assert = require("node:assert/strict");
(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage();
    await page.setContent('<div id="messages"></div>');
    await page.addStyleTag({
      content: fs.readFileSync("agent_service/ui.css", "utf8"),
    });
    const source = fs.readFileSync("agent_service/ui.js", "utf8");
    await page.addScriptTag({
      content: fs.readFileSync("tail_ui/assets/file-icons-data.js", "utf8"),
    });
    await page.addScriptTag({
      content: source.slice(
        source.indexOf("function projectFileIcon("),
        source.indexOf("async function loadProjectFileRoots("),
      ),
    });
    await page.addScriptTag({
      content: source.slice(
        source.indexOf("function messageAttachments("),
        source.indexOf("function bubble("),
      ),
    });
    await page.evaluate(() =>
      messageAttachments({ body: document.querySelector("#messages") }, [
        {
          name: "photo.png",
          preview_url:
            'data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" width="640" height="480"/>',
        },
        { name: "note.txt" },
      ]),
    );
    assert.equal(await page.locator(".message-image").count(), 1);
    assert.equal(await page.locator(".message-file").innerText(), "note.txt");
    assert.match(
      await page.locator(".message-file use").getAttribute("href"),
      /^\/assets\/file-icons\.svg#/,
    );
    assert.equal(
      await page
        .locator("#messages")
        .evaluate((el) => el.firstElementChild.className),
      "message-images",
    );
    const thumb = await page.locator(".attachment-preview").boundingBox();
    assert.equal(thumb.width, 128);
    assert.equal(thumb.height, 96);
    await page.getByRole("button", { name: "Enlarge image photo.png" }).click();
    await page.getByRole("dialog").waitFor({ state: "visible" });
    assert.equal(
      await page.locator(".image-modal img").getAttribute("alt"),
      "photo.png",
    );
    await page.keyboard.press("Escape");
    await page.locator("dialog").waitFor({ state: "detached" });
    assert.equal(await page.locator("dialog").count(), 0);
    assert.equal(
      await page
        .locator(".message-image")
        .evaluate((el) => el === document.activeElement),
      true,
    );
    await page.keyboard.press("Enter");
    await page.getByRole("button", { name: "Close image" }).click();
    await page.setViewportSize({ width: 390, height: 844 });
    await page.locator(".message-image").click();
    const modal = await page.getByRole("dialog").boundingBox();
    assert(modal.x >= 0 && modal.x + modal.width <= 390);
    await page.screenshot({ path: "/tmp/tail-message-image-modal.png" });
    await page.getByRole("button", { name: "Close image" }).click();
    await page.setContent(
      '<main style="height:100vh"><div id="messages" tabindex="-1"><div style="height:3000px">Long response</div></div><div class="composer-area"><button id="latest-message" class="latest-message" hidden>↓ Jump to latest message</button><textarea></textarea></div></main>',
    );
    await page.addStyleTag({
      content: fs.readFileSync("agent_service/ui.css", "utf8"),
    });
    await page.addScriptTag({
      content:
        "const $=id=>document.getElementById(id);" +
        source.slice(
          source.indexOf("function updateLatest("),
          source.indexOf("function setQuotaOpen("),
        ) +
        "updateLatest();",
    });
    const jump = page.locator("#latest-message");
    await jump.waitFor({ state: "visible" });
    const bounds = await jump.boundingBox();
    assert(
      bounds.y >= 0 && bounds.y + bounds.height < 844,
      "jump button remains inside viewport above composer",
    );
    await jump.click();
    await jump.waitFor({ state: "hidden" });
    assert(
      await page
        .locator("#messages")
        .evaluate((el) => el.scrollHeight - el.scrollTop - el.clientHeight < 2),
    );
    await page.setContent(
      '<aside id="sidebar"><nav><div class="conversation-row"><button><span class="conversation-title">A conversation with a long title to check the space it takes up in the sidebar</span></button></div></nav></aside>',
    );
    await page.addStyleTag({
      content: fs.readFileSync("agent_service/ui.css", "utf8"),
    });
    assert.equal(
      await page
        .locator(".conversation-title")
        .evaluate((el) => getComputedStyle(el).fontSize),
      "12px",
    );
    assert.equal(
      await page
        .locator(".conversation-title")
        .evaluate((el) => getComputedStyle(el).webkitLineClamp),
      "2",
    );
    console.log(
      "PASS: thumbnails, document cards, modal, keyboard, focus restoration and mobile fit",
    );
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
