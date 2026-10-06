"use strict";
/* Plugins page of the admin panel (issue #20): the Codex Settings > Plugins layout over the
   integration catalogs. Classic script sharing admin.js globals ($, element, request, state,
   integrationCatalogs, action, say, providerName, connectorIcon, connectorLabel). */
(() => {
  const CLIS = ["codex", "claude"]; // the only providers /api/integration-catalog answers for
  // Like every admin button, each chip leads with its own icon on the label's line.
  const CHIPS = [
    ["plugins", "Plugins", "plugin", "cube"],
    ["apps", "Apps", "account-app", "world"],
    ["mcps", "MCPs", "mcp", "plug"],
    ["skills", "Skills", undefined, "list-check"], // no count until the Skills content lands
  ];
  const view = { chip: "plugins", query: "", loading: false, built: false, clis: [] };
  const panel = $("plugins-panel");
  const chipButtons = new Map();
  let list, note, menu, refreshButton;

  const node = (tag, text, cls, testid) => {
    const el = element(tag, text, cls);
    if (testid) el.dataset.testid = testid;
    return el;
  };

  // Installed items of one kind, merged by id across the CLIs, each with the providers that have it.
  function installed(kind) {
    const merged = new Map();
    for (const info of view.clis)
      for (const item of integrationCatalogs.get(info.id)?.items || []) {
        if (item.kind !== kind || item.status === "available") continue;
        const group = merged.get(item.id) || { item, providers: [] };
        group.providers.push(info);
        merged.set(item.id, group);
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

  function row(group) {
    const label = connectorLabel(group.item);
    const text = node("div", undefined, "plugins-row-text");
    text.append(node("strong", label, undefined, "plugin-name"));
    if (group.item.description)
      text.append(node("small", group.item.description, undefined, "plugin-description"));
    const badges = node("div", undefined, "plugins-row-providers");
    badges.append(...group.providers.map((info) => node("span", providerName(info), "pill")));
    const more = node("button", "⋯", "plugins-more", "plugin-menu-button");
    more.type = "button";
    more.setAttribute("aria-label", label + " actions");
    more.setAttribute("aria-haspopup", "menu");
    more.setAttribute("aria-expanded", "false");
    more.onclick = () => openMenu(more, group);
    const menuWrap = node("div", undefined, "plugins-row-menu");
    menuWrap.append(more);
    const article = node("article", undefined, "plugins-row", "plugin-row");
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
    list = node("div", undefined, "plugins-list", "plugins-list");
    panel.append(toolbar, note, list);
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

  async function loadCatalogs(force) {
    if (view.loading) return;
    view.loading = true;
    refreshButton.disabled = true;
    render();
    try {
      const inventory = (state || (await request("state"))).inventory;
      view.clis = inventory.services.filter((info) => info.found && CLIS.includes(info.id));
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
  enter();
})();
