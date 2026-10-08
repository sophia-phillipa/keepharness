"use strict";
/* Plugins page of the admin panel (issue #20): the Codex Settings > Plugins layout over the
   integration catalogs. Classic script sharing admin.js globals ($, element, request, state,
   integrationCatalogs, action, say, providerName, connectorIcon, connectorLabel).
   Row switches (issue #21, D-041) read and write the CLI's real state through /api/provider-state;
   the changes made outside KeepHarness that those reads return go to admin.js (issue #43). */
(() => {
  const CLIS = ["codex", "claude"]; // the only providers /api/integration-catalog answers for
  // Like every admin button, each chip leads with its own icon on the label's line.
  const CHIPS = [
    ["plugins", "Plugins", "plugin", "cube"],
    ["apps", "Apps", "account-app", "world"],
    ["mcps", "MCPs", "mcp", "plug"],
    ["skills", "Skills", undefined, "list-check"], // no count until the Skills content lands
  ];
  const NO_PROJECT = "sem-projeto"; // the only scope this page has (D-041 item 3)
  const view = { chip: "plugins", query: "", loading: false, built: false, clis: [] };
  const panel = $("plugins-panel");
  const chipButtons = new Map();
  // Provider id -> { snapshot } from GET /api/provider-state, or { error } when it could not be read.
  const states = new Map();
  // "<provider>|<item id>" -> the last write error, shown on that row until the next read or write.
  const rowErrors = new Map();
  let list, note, status, menu, refreshButton, noteCount = 0;

  const node = (tag, text, cls, testid) => {
    const el = element(tag, text, cls);
    if (testid) el.dataset.testid = testid;
    return el;
  };

  // Installed items of one kind, merged by id across the CLIs, each with the providers that have it.
  // Plugins the CLI loads but the catalog does not list are added from its state snapshot.
  function installed(kind) {
    const merged = new Map();
    const add = (info, item, made) => {
      const group = merged.get(item.id) || { item, made, providers: [] };
      // A catalog item replaces the one made up from another CLI's snapshot: it has the description.
      if (group.made && !made) Object.assign(group, { item, made });
      if (!group.providers.includes(info)) group.providers.push(info);
      merged.set(item.id, group);
    };
    for (const info of view.clis) {
      for (const item of integrationCatalogs.get(info.id)?.items || [])
        if (item.kind === kind && item.status !== "available") add(info, item);
      if (kind === "plugin")
        for (const item of states.get(info.id)?.snapshot?.items || [])
          if (item.kind === "plugin") add(info, { id: item.id, name: item.name, kind, status: "installed" }, true);
    }
    return [...merged.values()].sort((a, b) =>
      connectorLabel(a.item).localeCompare(connectorLabel(b.item)),
    );
  }

  function closeMenu(restoreFocus) {
    if (!menu) return;
    menu.node.remove();
    menu.button.setAttribute("aria-expanded", "false");
    if (restoreFocus) menu.button.focus();
    menu = null;
  }
  function uninstall(group, info) {
    const label = connectorLabel(group.item),
      where = providerName(info);
    if (!confirm("Uninstall " + label + " from " + where + "? The plugin is removed from that CLI only."))
      return;
    return action(async () => {
      await request("integration", {
        provider: info.id,
        action: "plugin_remove",
        name: group.item.id.replace(/^plugin:/, ""),
      });
      // The cached catalog still lists the plugin; the next visit or Refresh reads it again.
      integrationCatalogs.delete(info.id);
      say(label + " is being uninstalled from " + where + ". Refresh the list when the operation finishes.");
    });
  }
  function openMenu(button, group) {
    const reopen = menu?.button !== button;
    closeMenu();
    if (!reopen) return;
    const popup = node("div", undefined, "plugins-menu", "plugin-menu");
    popup.setAttribute("role", "menu");
    for (const info of group.providers) {
      const entry = node(
        "button",
        group.providers.length > 1 ? "Uninstall from " + providerName(info) : "Uninstall",
        "plugins-menu-item",
      );
      entry.type = "button";
      entry.setAttribute("role", "menuitem");
      entry.onclick = () => {
        closeMenu(true);
        uninstall(group, info);
      };
      popup.append(entry);
    }
    popup.addEventListener("keydown", (event) => {
      const step = { ArrowDown: 1, ArrowUp: -1 }[event.key];
      if (!step) return;
      event.preventDefault();
      const items = [...popup.children];
      items[(items.indexOf(document.activeElement) + step + items.length) % items.length].focus();
    });
    button.after(popup);
    button.setAttribute("aria-expanded", "true");
    menu = { node: popup, button };
    popup.firstElementChild.focus();
  }

  // The status line of the switches: #feedback can sit inside the closed provider wizard.
  function report(text) {
    status.textContent = text;
    status.hidden = !text;
  }
  const capitalized = (text) => text.charAt(0).toUpperCase() + text.slice(1);

  // Writes go through action(), which holds the admin's one-operation lock and ignores a click that
  // arrives while a write is in flight. The switch never flips ahead of the server: it is redrawn from
  // the snapshot the server answered with.
  function writeState(group, info, stateItem) {
    const label = connectorLabel(group.item),
      where = providerName(info),
      key = info.id + "|" + stateItem.id;
    return action(async () => {
      report("");
      try {
        const body = await request("provider-state", {
          provider: info.id,
          project_id: NO_PROJECT,
          item_id: stateItem.id,
          scope: stateItem.scope,
          enabled: !stateItem.enabled,
          fingerprint: states.get(info.id).snapshot.fingerprint,
        });
        states.set(info.id, { snapshot: body.snapshot });
        rowErrors.delete(key);
        report(label + " is now " + onOff(!stateItem.enabled) + " in " + where + ".");
      } catch (error) {
        const fresh = error.status === 409 && error.body?.snapshot;
        if (fresh) {
          states.set(info.id, { snapshot: fresh });
          setProviderNotices(info.id, error.body.external_changes);
          showProviderNotices();
          const now = fresh.items.find((x) => x.id === stateItem.id);
          report(
            where + " changed since this page loaded: " + label + (now ? " is now " + onOff(now.enabled) : " was removed") + ". Try again.",
          );
        } else {
          rowErrors.set(key, error.message);
          report(error.message);
        }
      } finally {
        render();
        // The switch is gone or disabled after a 409: focus the row's menu button, else the list.
        const id = CSS.escape(stateItem.id);
        (
          list.querySelector('.plugins-switch[data-item-id="' + id + '"][data-provider="' + info.id + '"]:not(:disabled)') ||
          list.querySelector('[data-testid="plugin-row"][data-item-id="' + id + '"] .plugins-more') ||
          list
        ).focus();
      }
    });
  }

  // One provider on a row: its pill, plus a switch when its snapshot has the item. Returns [cell, note].
  function providerCell(group, info) {
    const label = connectorLabel(group.item),
      where = providerName(info);
    const cell = node("span", undefined, "plugins-provider");
    cell.append(node("span", where, "pill"));
    const read = states.get(info.id);
    const stateItem = read?.snapshot?.items.find((x) => x.id === group.item.id);
    // An item changed outside KeepHarness that nobody dismissed yet carries a marker.
    if (providerNotices.get(info.id)?.some((notice) => notice.item_id === group.item.id))
      cell.append(node("span", "Changed outside KeepHarness", "pill plugins-changed", "plugin-changed"));
    const text = !read?.snapshot
      ? where + ": " + (read?.error || "State is not readable here yet.")
      : !stateItem
        ? "Not installed in " + where
        : [where + ": " + capitalized(stateItem.scope) + " · " + stateItem.source, !stateItem.writable && stateItem.reason, rowErrors.get(info.id + "|" + stateItem.id)]
            .filter(Boolean)
            .join(" · ");
    const hint = node("small", text, "plugins-row-note", "plugin-note");
    hint.id = "plugin-note-" + ++noteCount;
    hint.dataset.provider = info.id;
    if (stateItem) {
      const input = node("button", undefined, "plugins-switch", "plugin-switch");
      input.type = "button";
      input.setAttribute("role", "switch");
      input.setAttribute("aria-label", label + " in " + where);
      input.setAttribute("aria-checked", String(stateItem.enabled));
      input.setAttribute("aria-describedby", hint.id);
      input.dataset.itemId = stateItem.id;
      input.dataset.provider = info.id;
      input.disabled = !stateItem.writable;
      input.onclick = () => writeState(group, info, stateItem);
      cell.append(input);
    }
    return [cell, hint];
  }

  function row(group) {
    const label = connectorLabel(group.item);
    const text = node("div", undefined, "plugins-row-text");
    text.append(node("strong", label, undefined, "plugin-name"));
    if (group.item.description)
      text.append(node("small", group.item.description, undefined, "plugin-description"));
    const badges = node("div", undefined, "plugins-row-providers");
    for (const info of group.providers) {
      const [cell, hint] = providerCell(group, info);
      badges.append(cell);
      text.append(hint);
    }
    const more = node("button", "⋯", "plugins-more", "plugin-menu-button");
    more.type = "button";
    more.setAttribute("aria-label", label + " actions");
    more.setAttribute("aria-haspopup", "menu");
    more.setAttribute("aria-expanded", "false");
    more.onclick = () => openMenu(more, group);
    const menuWrap = node("div", undefined, "plugins-row-menu");
    menuWrap.append(more);
    const article = node("article", undefined, "plugins-row", "plugin-row");
    article.dataset.itemId = group.item.id;
    article.append(connectorIcon(group.item), text, badges, menuWrap);
    return article;
  }

  function renderList() {
    list.setAttribute("aria-busy", String(view.loading));
    if (view.loading) {
      const wait = node("p", "Loading plugins… this can take up to 15 seconds.", "hint", "plugins-loading");
      wait.setAttribute("role", "status");
      return list.replaceChildren(wait);
    }
    const [, label, kind] = CHIPS.find(([id]) => id === view.chip);
    const empty = (message) => list.replaceChildren(node("p", message, "hint", "plugins-empty"));
    if (view.chip !== "plugins") return empty(label + " are not listed here yet.");
    if (!view.clis.length)
      return empty("No Codex or Claude Code CLI was found. Check the environment on the AI Providers page.");
    const query = view.query.trim().toLocaleLowerCase();
    const rows = installed(kind).filter((group) =>
      (connectorLabel(group.item) + " " + (group.item.description || "")).toLocaleLowerCase().includes(query),
    );
    if (!rows.length)
      return empty(query ? "No plugins match “" + view.query.trim() + "”." : "No plugins are installed.");
    list.replaceChildren(...rows.map(row));
  }

  function renderNote() {
    const lines = view.clis.flatMap((info) => {
      const data = integrationCatalogs.get(info.id);
      return [data?.error, ...(data?.warnings || [])].filter(Boolean).map((line) => providerName(info) + ": " + line);
    });
    note.textContent = lines.join("\n");
    note.hidden = !lines.length || view.loading;
  }

  function render() {
    closeMenu();
    for (const [id, label, kind, glyph] of CHIPS) {
      const chip = chipButtons.get(id);
      chip.setAttribute("aria-pressed", String(id === view.chip));
      // The label and its count share one text box so they read "Plugins 3" beside the icon.
      const text = node("span", label);
      if (kind && !view.loading) text.append(" ", node("span", String(installed(kind).length), "plugins-count"));
      chip.replaceChildren(icon(glyph), text);
    }
    renderNote();
    renderList();
  }

  function build() {
    const chips = node("div", undefined, "plugins-chips");
    chips.setAttribute("role", "group");
    chips.setAttribute("aria-label", "Plugin categories");
    for (const [id] of CHIPS) {
      const chip = node("button", undefined, "plugins-chip", "plugins-chip-" + id);
      chip.type = "button";
      chip.onclick = () => {
        view.chip = id;
        render();
      };
      chipButtons.set(id, chip);
      chips.append(chip);
    }
    const search = node("input", undefined, "plugins-search", "plugins-search");
    search.type = "search";
    search.placeholder = "Search plugins";
    search.setAttribute("aria-label", "Search plugins");
    search.oninput = () => {
      view.query = search.value;
      renderList();
    };
    refreshButton = node("button", undefined, "button secondary", "plugins-refresh");
    refreshButton.type = "button";
    refreshButton.replaceChildren(icon("refresh"), document.createTextNode("Refresh"));
    refreshButton.onclick = () => loadCatalogs(true);
    const toolbar = node("div", undefined, "plugins-toolbar");
    toolbar.append(chips, search, refreshButton);
    note = node("p", undefined, "hint plugins-note", "plugins-note");
    note.setAttribute("aria-live", "polite");
    status = node("p", undefined, "hint plugins-status", "plugins-status");
    status.setAttribute("role", "status");
    status.hidden = true;
    list = node("div", undefined, "plugins-list", "plugins-list");
    list.tabIndex = -1; // the focus fallback when a row disappears under a switch
    panel.append(toolbar, note, status, list);
    document.addEventListener("keydown", (event) => {
      if (event.key !== "Escape" || !menu) return;
      event.preventDefault();
      closeMenu(true);
    });
    document.addEventListener("click", (event) => {
      if (menu && !menu.node.parentElement.contains(event.target)) closeMenu();
    });
    view.built = true;
  }

  // The state of each found CLI, read in parallel: GET takes no admin lock. A failure only affects its provider.
  async function loadStates(clis) {
    rowErrors.clear();
    await Promise.all(
      clis.map(async ({ id }) => {
        try {
          const body = await request("provider-state?provider=" + id + "&project_id=" + NO_PROJECT);
          states.set(id, body.snapshot ? { snapshot: body.snapshot } : {});
          setProviderNotices(id, body.external_changes);
        } catch (error) {
          states.set(id, { error: error.message });
        }
      }),
    );
    showProviderNotices();
  }

  async function loadCatalogs(force) {
    if (view.loading) return;
    view.loading = true;
    refreshButton.disabled = true;
    render();
    try {
      const inventory = (state || (await request("state"))).inventory;
      view.clis = inventory.services.filter((info) => info.found && CLIS.includes(info.id));
      const reads = loadStates(view.clis);
      // One CLI at a time: the admin runs one operation at once and answers 429 to a second.
      for (const { id } of view.clis) {
        const cached = integrationCatalogs.get(id);
        // catalogPending is shared with the Providers page so one CLI scan runs at a time.
        if ((!force && cached && !cached.error) || catalogPending.has(id)) continue;
        catalogPending.add(id);
        try {
          integrationCatalogs.set(id, await requestCatalog(id));
        } catch (error) {
          integrationCatalogs.set(id, { ...(cached || { items: [] }), error: error.message });
        } finally {
          catalogPending.delete(id);
        }
      }
      await reads;
    } catch (error) {
      say(error.message, true);
    } finally {
      view.loading = false;
      refreshButton.disabled = false;
      render();
    }
  }

  function enter() {
    if (location.hash !== "#plugins") {
      if (menu) closeMenu();
      return;
    }
    if (!view.built) build();
    loadCatalogs(false);
  }
  window.addEventListener("hashchange", enter);
  // A dismissed notice loses its row marker; coming back to the tab re-reads the states (no timers).
  document.addEventListener("provider-notices", () => view.built && !view.loading && renderList());
  const reread = () => {
    if (location.hash === "#plugins" && view.built && !view.loading && !working && document.visibilityState === "visible")
      loadStates(view.clis).then(render);
  };
  window.addEventListener("focus", reread);
  document.addEventListener("visibilitychange", reread);
  enter();
})();
