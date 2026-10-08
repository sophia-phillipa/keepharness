/* Settings search indexes the displayed controls, including the local embedded admin.
 * Messages carry labels and control IDs only; changing a preference stays with its owner. */
(() => {
  "use strict";
  const byId = (id) => document.getElementById(id);
  const text = (node) => node?.textContent.trim() || "";
  const adminControls = {
    "full-access": "providers",
    "mcp-default-model": "connection",
    "mcp-default-effort": "connection",
  };
  const labelFor = (node) => node.getAttribute("aria-label") ||
    text(node.labels?.[0]?.querySelector("strong") || node.labels?.[0]);
  const descriptionFor = (node) => {
    const described = (node.getAttribute("aria-describedby") || "").split(/\s+/).map(byId).map(text).filter(Boolean);
    return described.join(" ") || text(node.closest("label")?.querySelector("small")) || node.title || "";
  };

  if (document.documentElement.dataset.surface === "admin") {
    if (!document.documentElement.dataset.embedded || !document.referrer) return;
    const parentOrigin = new URL(document.referrer).origin;
    const sections = new Set([...document.querySelectorAll("[data-panel]")].map((node) => node.dataset.panel));
    const preferences = () => Object.entries(adminControls).flatMap(([id, section]) => {
      const control = byId(id);
      if (!control || !sections.has(section)) return [];
      return [{ id, section, label: labelFor(control), description: descriptionFor(control), disabled: control.disabled }];
    });
    let latestRequest, hasReadyIndex = document.documentElement.dataset.settingsSearchReady === "true";
    const publish = () => {
      if (document.documentElement.dataset.settingsSearchReady !== "true" || !Number.isSafeInteger(latestRequest)) return;
      hasReadyIndex = true;
      parent.postMessage({ type: "keepharness:settings-preferences", request: latestRequest, preferences: preferences() }, parentOrigin);
    };
    document.addEventListener("keepharness:settings-index-change", publish);
    addEventListener("message", (event) => {
      if (event.source !== parent || event.origin !== parentOrigin) return;
      const data = event.data;
      if (data?.type === "keepharness:settings-query" && Number.isSafeInteger(data.request)) {
        latestRequest = data.request;
        publish();
      } else if (data?.type === "keepharness:settings-retry" && Number.isSafeInteger(data.request) && hasReadyIndex &&
          document.documentElement.dataset.settingsSearchRecovery === "available") {
        // A failed refresh leaves the last completed form DOM intact. Re-index it without reloading pending edits.
        latestRequest = data.request;
        delete document.documentElement.dataset.settingsSearchRecovery;
        document.documentElement.dataset.settingsSearchReady = "true";
        publish();
      } else if (data?.type === "keepharness:settings-section" && sections.has(data.section)) {
        location.hash = data.section;
      } else if (data?.type === "keepharness:settings-focus" && Object.hasOwn(adminControls, data.id)) {
        const control = byId(data.id);
        if (!control || control.disabled) return;
        location.hash = adminControls[data.id];
        // The admin's hashchange handler owns panel visibility; wait until it runs.
        setTimeout(() => {
          if (control.checkVisibility() && !control.disabled) {
            control.focus();
            control.scrollIntoView({ block: "center" });
          }
        }, 0);
      }
    });
    return;
  }

  const input = byId("settings-search");
  if (!input) return;
  const results = byId("settings-search-results"), status = byId("settings-search-status"),
    clearButton = byId("settings-search-clear"), retry = byId("settings-search-retry");
  let adminPreferences = [], request = 0, timer, adminState = "idle";
  const localAdmin = () => !byId("settings-system-nav").hidden;
  const sectionLabel = (section) => text(document.querySelector(`[data-admin-section="${section}"]`) ||
    document.querySelector(`[data-settings="${section}"]`));
  const nativePreferences = () => {
    const theme = document.querySelector("#settings-appearance .theme-picker"), panel = byId("panel-order-options");
    return [
      theme && { id: "settings-theme-picker", label: theme.getAttribute("aria-label"), description: text(byId("theme-toggle")) },
      { id: "panel-order-options", label: panel.getAttribute("aria-label"), description: text(panel.previousElementSibling) },
      ...["reading-size", "visual-markers-toggle"].map((id) => ({ id, label: labelFor(byId(id)), description: descriptionFor(byId(id)) })),
    ].filter(Boolean).map((item) => ({ ...item, section: "appearance" }));
  };
  const send = (data) => {
    const frame = byId("admin-frame");
    if (localAdmin() && frame?.src) frame.contentWindow?.postMessage(data, new URL(frame.src).origin);
  };
  const clear = () => {
    input.value = "";
    clearTimeout(timer);
    ++request;
    adminState = "idle";
    render();
    input.focus();
  };
  async function choose(item) {
    if (item.disabled || (item.admin && !localAdmin())) return;
    const nav = document.querySelector(item.admin ? `[data-admin-section="${item.section}"]` : `[data-settings="${item.section}"]`);
    if (!nav) return;
    clear();
    await navigate(settingsView(nav.dataset.settings, nav));
    if (item.admin) send({ type: "keepharness:settings-focus", id: item.id });
    else {
      const control = byId(item.id);
      const focus = control.matches("input, select, button") ? control : control.querySelector('button[aria-pressed="true"]') || control.querySelector("button");
      focus?.focus();
      focus?.scrollIntoView({ block: "center" });
    }
  }
  function render() {
    const query = input.value.trim().toLocaleLowerCase();
    const focusedId = document.activeElement?.dataset.preference;
    results.replaceChildren();
    clearButton.hidden = !query;
    retry.hidden = !query || adminState !== "unavailable" || !localAdmin();
    status.hidden = !query;
    if (!query) { status.textContent = ""; return; }
    const items = [...nativePreferences(), ...(localAdmin() ? adminPreferences : [])].filter((item) =>
      [item.label, item.description, sectionLabel(item.section)].join(" ").toLocaleLowerCase().includes(query));
    for (const item of items) {
      const row = document.createElement("li"), button = document.createElement("button");
      button.type = "button";
      button.dataset.preference = item.id;
      const label = document.createElement("strong"), detail = document.createElement("span");
      label.textContent = item.label;
      detail.textContent = [sectionLabel(item.section), item.description, item.disabled ? "Unavailable for the current model." : ""].filter(Boolean).join(" · ");
      button.append(label, detail);
      button.disabled = !!item.disabled;
      button.onclick = () => void choose(item);
      row.append(button);
      results.append(row);
      if (focusedId === item.id && !button.disabled) button.focus({ preventScroll: true });
    }
    status.textContent = items.length ? `${items.length} preferences found.` : "No matching preferences.";
    if (adminState === "loading") status.textContent += " Checking local admin…";
    if (adminState === "unavailable") status.textContent += " Local admin preferences are unavailable. Retry to check again.";
  }
  function queryAdmin(reload = false) {
    clearTimeout(timer);
    const current = ++request;
    if (!input.value.trim() || !localAdmin()) { adminState = "idle"; render(); return; }
    adminState = "loading";
    adminPreferences = [];
    let frame = byId("admin-frame");
    if (!frame) { showAdminSection("providers"); frame = byId("admin-frame"); }
    if (reload && !frame.dataset.settingsSearchReady) {
      // Reassigning the same fragment URL can leave the failed document in place.
      const url = new URL(frame.src);
      url.searchParams.set("settings_retry", String(current));
      frame.src = url.href;
    } else if (reload) send({ type: "keepharness:settings-retry", request: current });
    const ask = () => { if (current === request) send({ type: "keepharness:settings-query", request: current }); };
    // Admin startup fetches its state asynchronously, after the iframe's load event.
    const started = Date.now();
    const poll = () => {
      if (current !== request || adminState !== "loading") return;
      if (Date.now() - started >= 3000) { adminState = "unavailable"; render(); return; }
      ask();
      timer = setTimeout(poll, 150);
    };
    poll();
    render();
  }
  addEventListener("message", (event) => {
    const frame = byId("admin-frame"), data = event.data;
    if (!localAdmin() || !frame?.src || event.source !== frame.contentWindow || event.origin !== new URL(frame.src).origin ||
        data?.type !== "keepharness:settings-preferences" || data.request !== request || !Array.isArray(data.preferences)) return;
    clearTimeout(timer);
    frame.dataset.settingsSearchReady = "true";
    adminState = "ready";
    adminPreferences = data.preferences.filter((item) => Object.hasOwn(adminControls, item?.id) && adminControls[item.id] === item.section &&
      typeof item.label === "string" && typeof item.description === "string" && typeof item.disabled === "boolean")
      .map((item) => ({ ...item, admin: true }));
    render();
  });
  input.addEventListener("input", () => { render(); queryAdmin(); });
  clearButton.onclick = clear;
  retry.onclick = () => queryAdmin(true);
  byId("settings-dialog").addEventListener("close", () => { input.value = ""; clearTimeout(timer); ++request; adminState = "idle"; render(); });
  const searchBox = input.closest(".settings-search");
  searchBox.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && input.value) { event.preventDefault(); event.stopPropagation(); clear(); return; }
    const buttons = [...results.querySelectorAll("button:not(:disabled)")];
    const index = buttons.indexOf(document.activeElement);
    if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key) && buttons.length &&
        (index >= 0 || ["ArrowDown", "ArrowUp"].includes(event.key))) {
      event.preventDefault();
      const next = event.key === "Home" ? 0 : event.key === "End" ? buttons.length - 1 :
        event.key === "ArrowDown" ? (index + 1) % buttons.length : (index < 0 ? buttons.length - 1 : (index - 1 + buttons.length) % buttons.length);
      buttons[next].focus();
    } else if (event.key === "Enter" && event.target === input && buttons.length) { event.preventDefault(); buttons[0].click(); }
  });
})();
