"use strict";
/* Plugins page of the admin panel (issue #20): the Codex Settings > Plugins layout over the
   integration catalogs. Classic script sharing admin.js globals ($, element, request, state,
   integrationCatalogs, action, say, providerName, connectorIcon, connectorLabel).
   Row switches (issue #21, D-041) read and write the CLI's real state through /api/provider-state;
   the changes made outside KeepHarness that those reads return go to admin.js (issue #43). */
(() => {
  const CLIS = ["codex", "claude", "deepseek"];
  // Like every admin button, each chip leads with its own icon on the label's line.
  const CHIPS = [
    ["plugins", "Plugins", "plugin", "cube"],
    ["apps", "Apps", "app", "world"],
    ["mcps", "MCPs", "mcp", "plug"],
    ["skills", "Skills", "skill", "list-check"],
    ["hooks", "Hooks", "hook", "list-check"],
    ["instructions", "Instructions", "instructions", "list-check"],
  ];
  const NO_PROJECT = "sem-projeto";
  const view = {
    chip: "plugins",
    mode: "directory",
    query: "",
    detailKey: "",
    detailOrigin: "",
    loading: false,
    built: false,
    clis: [],
    project: NO_PROJECT,
  };
  const panel = $("plugins-panel");
  const chipButtons = new Map();
  // Provider id -> { snapshot } from GET /api/provider-state, or { error } when it could not be read.
  const states = new Map();
  // "<provider>|<item id>" -> the last write error, shown on that row until the next read or write.
  const rowErrors = new Map();
  let list,
    note,
    status,
    menu,
    refreshButton,
    modeButton,
    projectSelect,
    trustPanel,
    stateRead = 0,
    noteCount = 0;

  const node = (tag, text, cls, testid) => {
    const el = element(tag, text, cls);
    if (testid) el.dataset.testid = testid;
    return el;
  };

  // Keep this byte-for-byte equivalent to integrations_view.family_key; both consume the shared
  // tests/fixtures/plugin-directory.json cases.
  const familyKey = (item) =>
    String(item.name || item.id || "")
      .replace(/^(?:mcp|plugin):/i, "")
      .split("@")[0]
      .toLowerCase()
      .replace(/[_ ]/g, "-");
  const marketplace = (item) =>
    item.marketplace || String(item.id || "").split("@")[1] || "Local plugins";

  // One card per plugin family. Provider variants stay separate so every operation keeps its exact
  // provider and identifier; ambiguous same-provider variants are explained instead of guessed.
  function pluginGroups() {
    const merged = new Map();
    const add = (info, item, made = false) => {
      const key = familyKey(item);
      if (!key) return;
      const group = merged.get(key) || {
        key,
        item,
        made,
        providers: [],
        variants: new Map(),
      };
      const variants = group.variants.get(info.id) || [];
      if (!variants.some((entry) => entry.id === item.id)) variants.push(item);
      group.variants.set(info.id, variants);
      if (!group.providers.includes(info)) group.providers.push(info);
      // Catalog metadata wins over a snapshot-only stub; otherwise retain the richer description.
      if (
        (group.made && !made) ||
        (!group.item.description && item.description)
      )
        Object.assign(group, { item, made });
      merged.set(key, group);
    };
    for (const info of view.clis) {
      const catalogItems = (
        integrationCatalogs.get(info.id)?.items || []
      ).filter((item) => item.kind === "plugin");
      for (const item of catalogItems)
        if (item.kind === "plugin") add(info, item);
      for (const item of states.get(info.id)?.snapshot?.items || [])
        if (
          item.kind === "plugin" &&
          !catalogItems.some((catalogItem) => catalogItem.id === item.id)
        )
          add(
            info,
            {
              id: item.id,
              name: item.name,
              kind: "plugin",
              status: "installed",
            },
            true,
          );
    }
    return [...merged.values()].sort((a, b) =>
      connectorLabel(a.item).localeCompare(connectorLabel(b.item)),
    );
  }

  // Installed items of one kind, merged by id across the CLIs, each with the providers that have it.
  // Plugins the CLI loads but the catalog does not list are added from its state snapshot.
  function installed(kind) {
    const merged = new Map();
    const add = (info, item, made) => {
      const group = merged.get(item.id) || {
        key: familyKey(item),
        item,
        made,
        providers: [],
        variants: new Map(),
      };
      // A catalog item replaces the one made up from another CLI's snapshot: it has the description.
      if (group.made && !made) Object.assign(group, { item, made });
      if (!group.providers.includes(info)) group.providers.push(info);
      const variants = group.variants.get(info.id) || [];
      if (!variants.some((entry) => entry.id === item.id)) variants.push(item);
      group.variants.set(info.id, variants);
      merged.set(item.id, group);
    };
    for (const info of view.clis) {
      for (const item of integrationCatalogs.get(info.id)?.items || [])
        if (
          (item.kind === kind ||
            (kind === "app" && item.kind === "account-app")) &&
          item.status !== "available"
        )
          add(info, item);
      for (const item of states.get(info.id)?.snapshot?.items || [])
        if (item.kind === kind)
          add(
            info,
            { id: item.id, name: item.name, kind, status: "installed" },
            true,
          );
    }
    return [...merged.values()].sort((a, b) =>
      connectorLabel(a.item).localeCompare(connectorLabel(b.item)),
    );
  }

  const familyStateItems = (group, info) => {
    const ids = new Set(providerVariants(group, info).map((item) => item.id));
    const catalogIds = new Set(
      (integrationCatalogs.get(info.id)?.items || [])
        .filter((item) => item.kind === "plugin")
        .map((item) => item.id),
    );
    return (states.get(info.id)?.snapshot?.items || []).filter(
      (item) =>
        item.kind === "plugin" &&
        (ids.has(item.id) ||
          (!catalogIds.has(item.id) && familyKey(item) === group.key)),
    );
  };
  const exactStateItems = (group, info) =>
    (states.get(info.id)?.snapshot?.items || []).filter(
      (item) => item.kind === group.item.kind && item.id === group.item.id,
    );
  const providerVariants = (group, info) => group.variants.get(info.id) || [];
  const installedTargets = (group, family = false) =>
    group.providers.filter(
      (info) =>
        group.item.kind === "plugin" &&
        info.id !== "deepseek" &&
        (family ? familyStateItems(group, info) : exactStateItems(group, info))
          .length === 1,
    );
  const providerTarget = (group, info, family = false) => {
    const statesHere = family
      ? familyStateItems(group, info)
      : exactStateItems(group, info);
    return statesHere.length === 1 ? statesHere[0] : null;
  };

  function closeMenu(restoreFocus) {
    if (!menu) return;
    menu.node.remove();
    menu.button.setAttribute("aria-expanded", "false");
    if (restoreFocus) menu.button.focus();
    menu = null;
  }
  function uninstall(group, info, family = false) {
    const label = connectorLabel(group.item),
      where = providerName(info);
    if (
      !confirm(
        "Uninstall " +
          label +
          " from " +
          where +
          "? The plugin is removed from that CLI only.",
      )
    )
      return;
    return action(async () => {
      await request("integration", {
        provider: info.id,
        action: "plugin_remove",
        name: providerTarget(group, info, family).id.replace(/^plugin:/, ""),
      });
      // The cached catalog still lists the plugin; the next visit or Refresh reads it again.
      integrationCatalogs.delete(info.id);
      say(
        label +
          " is being uninstalled from " +
          where +
          ". Refresh the list when the operation finishes.",
      );
    });
  }
  function openMenu(button, group, family = false) {
    const reopen = menu?.button !== button;
    closeMenu();
    if (!reopen) return;
    const popup = node("div", undefined, "plugins-menu", "plugin-menu");
    popup.setAttribute("role", "menu");
    for (const info of installedTargets(group, family)) {
      const entry = node(
        "button",
        group.providers.length > 1
          ? "Uninstall from " + providerName(info)
          : "Uninstall",
        "plugins-menu-item",
      );
      entry.type = "button";
      entry.setAttribute("role", "menuitem");
      entry.onclick = () => {
        closeMenu(true);
        uninstall(group, info, family);
      };
      popup.append(entry);
    }
    popup.addEventListener("keydown", (event) => {
      const step = { ArrowDown: 1, ArrowUp: -1 }[event.key];
      if (!step) return;
      event.preventDefault();
      const items = [...popup.children];
      items[
        (items.indexOf(document.activeElement) + step + items.length) %
          items.length
      ].focus();
    });
    button.after(popup);
    button.setAttribute("aria-expanded", "true");
    menu = { node: popup, button };
    popup.firstElementChild.focus();
  }

  function install(group, info, variant) {
    const label = connectorLabel(group.item),
      where = providerName(info);
    return action(async () => {
      await request("integration", {
        provider: info.id,
        action: "plugin_install",
        name: variant.id.replace(/^plugin:/, ""),
      });
      report(
        "Installation of " +
          label +
          " started in " +
          where +
          ". Track Operations, then Refresh.",
      );
      pollOperations();
    });
  }

  // The status line of the switches: #feedback can sit inside the closed provider wizard.
  function report(text) {
    status.textContent = text;
    status.hidden = !text;
  }
  const capitalized = (text) => text.charAt(0).toUpperCase() + text.slice(1);

  const projectLabel = () =>
    projectSelect?.selectedOptions[0]?.textContent || view.project;

  function acceptProviderBody(provider, body) {
    const current = states.get(provider) || {};
    states.set(provider, {
      ...current,
      ...(body.snapshot ? { snapshot: body.snapshot } : {}),
      ...(body.trust ? { trust: body.trust } : {}),
      ...(Array.isArray(body.mcp_approvals)
        ? { mcp_approvals: body.mcp_approvals }
        : {}),
    });
  }

  function trustContext(provider) {
    return {
      provider,
      project_id: view.project,
      expected_project_root: states.get(provider)?.snapshot?.project_root,
      read: stateRead,
    };
  }

  function currentTrustContext(context) {
    return (
      context &&
      context.read === stateRead &&
      context.project_id === view.project &&
      context.expected_project_root &&
      !view.loading &&
      view.clis.some((info) => info.id === context.provider) &&
      states.get(context.provider)?.snapshot?.project_root ===
        context.expected_project_root
    );
  }

  function writeTrust(context, trusted = true) {
    if (!currentTrustContext(context)) return;
    const { provider, project_id: project, expected_project_root } = context;
    let current = true;
    return action(async () => {
      report("");
      try {
        const body = await request("provider-state/trust", {
          provider,
          project_id: project,
          expected_project_root,
          ...(trusted ? {} : { trusted: false }),
        });
        current = currentTrustContext(context);
        if (!current) return;
        for (const info of view.clis) {
          const current = states.get(info.id) || {};
          states.set(info.id, {
            ...current,
            ...(body.trust ? { trust: body.trust } : {}),
          });
        }
        acceptProviderBody(provider, body);
        if (Array.isArray(body.mcp_approvals)) {
          const claude = states.get("claude") || {};
          states.set("claude", {
            ...claude,
            mcp_approvals: body.mcp_approvals,
          });
        }
        report(
          projectLabel() +
            (body.trust?.trusted
              ? " is trusted for Codex and Claude Code in KeepHarness."
              : " is no longer trusted by Codex or Claude Code."),
        );
      } catch (error) {
        current = currentTrustContext(context);
        if (current && error.status === 409)
          current = await loadStates(view.clis);
        if (current) report(error.message);
      } finally {
        if (current) {
          renderTrust();
          trustPanel.querySelector("button:not(:disabled)")?.focus();
        }
      }
    });
  }

  function writeMcpApproval(context, server, approved) {
    if (!currentTrustContext(context)) return;
    const { provider, project_id: project, expected_project_root } = context;
    let current = true;
    return action(async () => {
      report("");
      try {
        const body = await request("provider-state/mcp-approvals", {
          provider,
          project_id: project,
          expected_project_root,
          server,
          approved,
        });
        current = currentTrustContext(context);
        if (!current) return;
        acceptProviderBody("claude", body);
        const actual = body.mcp_approvals?.find(
          (item) => item.server === server,
        )?.approved;
        report(
          actual === approved
            ? server +
                " is now " +
                (approved ? "approved" : "not approved") +
                " for " +
                projectLabel() +
                "."
            : body.trust?.required
              ? "Approval saved for " +
                server +
                ". Trust " +
                projectLabel() +
                " before it can run."
              : server +
                " remains not approved because another CLI settings layer denies it.",
        );
      } catch (error) {
        current = currentTrustContext(context);
        if (current && error.status === 409)
          current = await loadStates(view.clis);
        if (current) report(error.message);
      } finally {
        if (current) {
          renderTrust();
          trustPanel
            .querySelector('[data-server="' + CSS.escape(server) + '"] button')
            ?.focus();
        }
      }
    });
  }

  function renderTrust() {
    if (!trustPanel) return;
    trustPanel.replaceChildren();
    trustPanel.hidden = true;
    if (view.project === NO_PROJECT || view.loading) return;
    const provider = view.clis.find(
        (info) =>
          states.get(info.id)?.trust &&
          states.get(info.id)?.snapshot?.project_root,
      )?.id,
      trust = states.get(provider)?.trust;
    const approvals = states.get("claude")?.mcp_approvals || [];
    if (!trust && !approvals.length) return;
    trustPanel.hidden = false;
    trustPanel.setAttribute("aria-label", "Project trust and MCP approvals");
    if (trust?.inherited_from)
      trustPanel.append(
        node(
          "p",
          "Codex still loads trusted configuration from " +
            trust.inherited_from +
            ". Revoking this project's trust does not revoke its parent.",
          "project-trust-inherited",
        ),
      );
    if (trust?.required) {
      const copy = node("div", undefined, "project-trust-copy");
      copy.append(
        node("strong", "Trust " + projectLabel() + "?"),
        node(
          "p",
          "Trusting this project applies to both Codex and Claude Code in KeepHarness. It enables project instructions, Claude hooks and environment settings, including env entries. Versioned .mcp.json servers approved in project settings can then run; other servers still need approval below.",
        ),
      );
      const accept = node(
        "button",
        "Trust " + projectLabel(),
        "button primary",
      );
      accept.type = "button";
      const context = trustContext(provider);
      accept.disabled = !currentTrustContext(context);
      accept.onclick = () => writeTrust(context);
      trustPanel.append(copy, accept);
    }
    if (trust?.trusted) {
      const context = trustContext(provider),
        revoke = node(
          "button",
          "Revoke trust for " + projectLabel(),
          "button secondary",
        );
      revoke.type = "button";
      revoke.disabled = !currentTrustContext(context);
      revoke.onclick = () => writeTrust(context, false);
      trustPanel.append(
        node(
          "p",
          "Trusted by Codex or Claude Code. Revoking trust applies to both CLIs.",
        ),
        revoke,
      );
    }
    for (const item of approvals) {
      const row = node(
        "div",
        undefined,
        "project-mcp-approval",
        "project-mcp-approval",
      );
      row.dataset.server = item.server;
      const copy = node("div");
      copy.append(
        node("strong", item.server),
        node(
          "small",
          "Project MCP server · " +
            (item.enabled === false
              ? "Disabled by owner"
              : item.approved
                ? "Approved"
                : "Not approved"),
        ),
      );
      const toggle = node(
        "button",
        (item.approved ? "Revoke" : "Approve") + " " + item.server,
        "button secondary",
      );
      toggle.type = "button";
      const context = trustContext("claude");
      toggle.disabled = !currentTrustContext(context);
      toggle.onclick = () =>
        writeMcpApproval(context, item.server, !item.approved);
      row.append(copy);
      if (item.enabled !== false) row.append(toggle);
      trustPanel.append(row);
    }
  }

  // Writes go through action(), which holds the admin's one-operation lock and ignores a click that
  // arrives while a write is in flight. The switch never flips ahead of the server: it is redrawn from
  // the snapshot the server answered with.
  function writeState(group, info, stateItem) {
    const label = connectorLabel(group.item),
      where = providerName(info),
      key = info.id + "|" + stateItem.id,
      project = view.project;
    let current = true;
    return action(async () => {
      report("");
      try {
        const body = await request("provider-state", {
          provider: info.id,
          project_id: project,
          item_id: stateItem.id,
          scope: stateItem.scope,
          enabled: !stateItem.enabled,
          fingerprint: states.get(info.id).snapshot.fingerprint,
        });
        current = project === view.project;
        if (!current) return;
        acceptProviderBody(info.id, body);
        rowErrors.delete(key);
        report(
          label + " is now " + onOff(!stateItem.enabled) + " in " + where + ".",
        );
      } catch (error) {
        current = project === view.project;
        if (!current) return;
        const fresh = error.status === 409 && error.body?.snapshot;
        if (fresh) {
          acceptProviderBody(info.id, error.body);
          setProviderNotices(info.id, error.body.external_changes);
          showProviderNotices();
          const now = fresh.items.find((x) => x.id === stateItem.id);
          report(
            where +
              " changed since this page loaded: " +
              label +
              (now ? " is now " + onOff(now.enabled) : " was removed") +
              ". Try again.",
          );
        } else {
          rowErrors.set(key, error.message);
          report(error.message);
        }
      } finally {
        if (current) {
          render();
          // The switch is gone or disabled after a 409: focus the row's menu button, else the list.
          const id = CSS.escape(stateItem.id);
          (
            list.querySelector(
              '.plugins-switch[data-item-id="' +
                id +
                '"][data-provider="' +
                info.id +
                '"]:not(:disabled)',
            ) ||
            list.querySelector(
              '[data-testid="plugin-row"][data-item-id="' +
                id +
                '"] .plugins-more',
            ) ||
            list
          ).focus();
        }
      }
    });
  }

  // One provider on a row: its pill, plus a switch when its snapshot has the item. Returns [cell, note].
  function providerCell(group, info, detail = false) {
    const label = connectorLabel(group.item),
      where = providerName(info);
    const cell = node("span", undefined, "plugins-provider");
    cell.append(node("span", where, "pill"));
    const read = states.get(info.id);
    const matchingStates = detail
      ? familyStateItems(group, info)
      : exactStateItems(group, info);
    const stateItem = matchingStates.length === 1 ? matchingStates[0] : null;
    const variants = providerVariants(group, info);
    // An item changed outside KeepHarness that nobody dismissed yet carries a marker.
    const ids = new Set(
      detail
        ? [...matchingStates, ...variants].map((item) => item.id)
        : [group.item.id],
    );
    if (providerNotices.get(info.id)?.some((notice) => ids.has(notice.item_id)))
      cell.append(
        node(
          "span",
          "Changed outside KeepHarness",
          "pill plugins-changed",
          "plugin-changed",
        ),
      );
    let text = !read?.snapshot
      ? where + ": " + (read?.error || "State is not readable here yet.")
      : matchingStates.length > 1
        ? where +
          ": Multiple installed entries share this name; manage them in the CLI."
        : !stateItem && variants.some((item) => item.status === "installed")
          ? where +
            ": The catalog reports this plugin as installed, but provider state did not return a matching item."
          : !stateItem && variants.length > 1
            ? where +
              ": Multiple catalog entries share this name; choose one in the CLI."
            : !stateItem &&
                variants.length === 1 &&
                variants[0].status === "available"
              ? "Not installed in " + where + "."
              : !stateItem
                ? where + ": This plugin is not offered by this provider."
                : [
                    where +
                      ": " +
                      capitalized(stateItem.scope) +
                      " · " +
                      stateItem.source,
                    stateItem.reason,
                    stateItem.affects?.length &&
                      "Also affects: " + stateItem.affects.join(", "),
                    rowErrors.get(info.id + "|" + stateItem.id),
                  ]
                    .filter(Boolean)
                    .join(" · ");
    const hint = node("small", text, "plugins-row-note", "plugin-note");
    hint.id = "plugin-note-" + ++noteCount;
    hint.dataset.provider = info.id;
    if (stateItem) {
      const input = node(
        "button",
        undefined,
        "plugins-switch",
        "plugin-switch",
      );
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
    } else if (
      detail &&
      read?.snapshot &&
      variants.length === 1 &&
      variants[0].status === "available"
    ) {
      const variant = variants[0];
      const installButton = node(
        "button",
        "Install",
        "button secondary",
        "plugin-install",
      );
      installButton.type = "button";
      installButton.setAttribute(
        "aria-label",
        "Install " + label + " in " + where,
      );
      installButton.onclick = () => install(group, info, variant);
      hint.textContent = text;
      cell.append(installButton);
    }
    return [cell, hint];
  }

  function openDetail(group, focusProviders = false, origin = "") {
    view.detailKey = group.key;
    view.detailOrigin = origin;
    renderList();
    (focusProviders
      ? list.querySelector('[data-testid="plugin-providers"]')
      : list.querySelector("h2")
    )?.focus();
  }

  function card(group, source) {
    const cardItem =
      group.providers
        .flatMap((info) => providerVariants(group, info))
        .find((item) => marketplace(item) === source) || group.item;
    const label = connectorLabel(cardItem);
    const article = node("article", undefined, "plugin-card", "plugin-card");
    article.dataset.familyKey = group.key;
    article.dataset.marketplace = source;
    article.dataset.itemId = cardItem.id;
    const open = node("button", undefined, "plugin-card-open");
    open.type = "button";
    open.setAttribute("aria-label", "View " + label + " details");
    const text = node("span", undefined, "plugin-card-text");
    text.append(node("strong", label, undefined, "plugin-name"));
    if (cardItem.description)
      text.append(
        node("small", cardItem.description, undefined, "plugin-description"),
      );
    open.append(connectorIcon(cardItem), text);
    open.onclick = () => openDetail(group, false, source);
    const actionButton = node("button", undefined, "plugin-card-action");
    actionButton.type = "button";
    const hasInstallTarget = group.providers.some(
      (info) =>
        states.get(info.id)?.snapshot &&
        providerVariants(group, info).length === 1 &&
        providerVariants(group, info)[0].status === "available",
    );
    if (installedTargets(group, true).length) {
      actionButton.textContent = "⋯";
      actionButton.setAttribute("aria-label", label + " actions");
      actionButton.setAttribute("aria-haspopup", "menu");
      actionButton.setAttribute("aria-expanded", "false");
      actionButton.onclick = () => openMenu(actionButton, group, true);
    } else if (hasInstallTarget) {
      actionButton.textContent = "+";
      actionButton.setAttribute("aria-label", "Install " + label);
      actionButton.onclick = () => openDetail(group, true, source);
    } else {
      actionButton.textContent = "›";
      actionButton.setAttribute(
        "aria-label",
        "View " + label + " provider availability",
      );
      actionButton.onclick = () => openDetail(group, true, source);
    }
    article.append(open, actionButton);
    return article;
  }

  function renderDirectory(groups) {
    const directory = node(
      "div",
      undefined,
      "plugin-directory",
      "plugin-directory",
    );
    const byMarketplace = new Map();
    for (const group of groups) {
      const sources = new Set(
        group.providers
          .flatMap((info) => providerVariants(group, info))
          .map(marketplace),
      );
      if (!sources.size) sources.add("Local plugins");
      for (const source of sources) {
        if (!byMarketplace.has(source)) byMarketplace.set(source, []);
        byMarketplace.get(source).push(group);
      }
    }
    for (const source of [...byMarketplace.keys()].sort((a, b) =>
      a.localeCompare(b),
    )) {
      const section = node(
        "details",
        undefined,
        "plugin-marketplace",
        "plugin-marketplace",
      );
      section.open = true;
      section.append(node("summary", source));
      const grid = node("div", undefined, "plugin-card-grid");
      grid.append(
        ...byMarketplace.get(source).map((group) => card(group, source)),
      );
      section.append(grid);
      directory.append(section);
    }
    list.replaceChildren(directory);
  }

  function renderDetail(group) {
    const label = connectorLabel(group.item);
    const detail = node("article", undefined, "plugin-detail", "plugin-detail");
    const back = node(
      "button",
      undefined,
      "plugin-detail-back",
      "plugin-detail-back",
    );
    back.type = "button";
    back.setAttribute("aria-label", "Back to plugins");
    back.append(icon("chevron-left"), document.createTextNode("Plugins"));
    back.onclick = () => {
      view.detailKey = "";
      renderList();
      list
        .querySelector(
          '[data-family-key="' +
            CSS.escape(group.key) +
            '"][data-marketplace="' +
            CSS.escape(view.detailOrigin) +
            '"] .plugin-card-open',
        )
        ?.focus();
    };
    const heading = node("div", undefined, "plugin-detail-heading");
    const title = node("h2", label, undefined, "plugin-detail-title");
    title.tabIndex = -1;
    heading.append(connectorIcon(group.item), title);
    detail.append(back, heading);
    const variants = group.providers.flatMap((info) =>
      providerVariants(group, info),
    );
    const metadata =
      variants.find((item) => marketplace(item) === view.detailOrigin) ||
      group.item;
    const description = metadata.description;
    if (description)
      detail.append(node("p", description, "plugin-detail-description"));
    const versionValue = metadata.version;
    if (versionValue) {
      const version = node("dl", undefined, "plugin-detail-facts");
      version.append(node("dt", "Version"), node("dd", versionValue));
      detail.append(version);
    }
    for (const [name, entries] of [
      ["Apps", metadata.apps],
      ["Skills", metadata.skills],
    ]) {
      if (!Array.isArray(entries)) continue;
      if (!entries.length) continue;
      detail.append(
        node("h3", name),
        (() => {
          const ul = node("ul", undefined, "plugin-detail-items");
          ul.append(...entries.map((value) => node("li", value)));
          return ul;
        })(),
      );
    }
    const attribution = node("dl", undefined, "plugin-detail-facts");
    for (const [name, value] of [
      ["Developer", metadata.developer],
      ["Source", metadata.source],
    ].filter(([, value]) => value))
      attribution.append(node("dt", name), node("dd", value));
    if (attribution.children.length) detail.append(attribution);
    const providers = node(
      "section",
      undefined,
      "plugin-providers",
      "plugin-providers",
    );
    providers.tabIndex = -1;
    providers.append(node("h3", "Providers"));
    for (const info of view.clis) {
      const providerRow = node(
        "div",
        undefined,
        "plugin-provider-row",
        "plugin-provider-row",
      );
      const [cell, hint] = providerCell(group, info, true);
      providerRow.append(cell, hint);
      providers.append(providerRow);
    }
    detail.append(providers);
    list.replaceChildren(detail);
  }

  function row(group) {
    const label = connectorLabel(group.item);
    const text = node("div", undefined, "plugins-row-text");
    text.append(node("strong", label, undefined, "plugin-name"));
    if (group.item.description)
      text.append(
        node("small", group.item.description, undefined, "plugin-description"),
      );
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
    more.disabled = !installedTargets(group).length;
    if (more.disabled)
      more.title = "Provider state did not report an installed item.";
    else more.onclick = () => openMenu(more, group);
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
      const wait = node(
        "p",
        "Loading plugins… this can take up to 15 seconds.",
        "hint",
        "plugins-loading",
      );
      wait.setAttribute("role", "status");
      return list.replaceChildren(wait);
    }
    const [, label] = CHIPS.find(([id]) => id === view.chip);
    const empty = (message) =>
      list.replaceChildren(node("p", message, "hint", "plugins-empty"));
    if (view.chip !== "plugins") {
      const kind = CHIPS.find(([id]) => id === view.chip)[2];
      const query = view.query.trim().toLocaleLowerCase();
      const rows = installed(kind).filter((group) =>
        connectorLabel(group.item).toLocaleLowerCase().includes(query),
      );
      return rows.length
        ? list.replaceChildren(...rows.map(row))
        : empty("No " + label.toLowerCase() + " are listed.");
    }
    if (!view.clis.length)
      return empty(
        "No Codex or Claude Code CLI was found. Check the environment on the AI Providers page.",
      );
    const query = view.query.trim().toLocaleLowerCase();
    if (view.mode === "manage") {
      const rows = installed("plugin").filter((group) =>
        (connectorLabel(group.item) + " " + (group.item.description || ""))
          .toLocaleLowerCase()
          .includes(query),
      );
      if (!rows.length)
        return empty(
          query
            ? "No plugins match “" + view.query.trim() + "”."
            : "No plugins are installed.",
        );
      return list.replaceChildren(...rows.map(row));
    }
    const groups = pluginGroups();
    const selected = groups.find((group) => group.key === view.detailKey);
    if (selected) return renderDetail(selected);
    const rows = groups.filter((group) =>
      group.providers
        .flatMap((info) => providerVariants(group, info))
        .map((item) =>
          [
            connectorLabel(item),
            item.description,
            item.developer,
            item.marketplace,
            item.version,
            ...(item.apps || []),
            ...(item.skills || []),
          ]
            .filter(Boolean)
            .join(" "),
        )
        .join(" ")
        .toLocaleLowerCase()
        .includes(query),
    );
    if (!rows.length)
      return empty(
        query
          ? "No plugins match “" + view.query.trim() + "”."
          : "No plugins are available.",
      );
    renderDirectory(rows);
  }

  function renderNote() {
    const lines = view.clis.flatMap((info) => {
      const data = integrationCatalogs.get(info.id);
      return [
        states.get(info.id)?.error,
        data?.error,
        ...(data?.warnings || []),
      ]
        .filter(Boolean)
        .map((line) => providerName(info) + ": " + line);
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
      if (kind && !view.loading)
        text.append(
          " ",
          node(
            "span",
            String(
              kind === "plugin" && view.mode === "directory"
                ? pluginGroups().length
                : installed(kind).length,
            ),
            "plugins-count",
          ),
        );
      chip.replaceChildren(icon(glyph), text);
    }
    renderNote();
    renderTrust();
    modeButton.replaceChildren(
      icon(view.mode === "directory" ? "settings" : "cube"),
      document.createTextNode(
        view.mode === "directory" ? "Manage" : "Browse directory",
      ),
    );
    renderList();
  }

  function build() {
    const chips = node("div", undefined, "plugins-chips");
    chips.setAttribute("role", "group");
    chips.setAttribute("aria-label", "Plugin categories");
    for (const [id] of CHIPS) {
      const chip = node(
        "button",
        undefined,
        "plugins-chip",
        "plugins-chip-" + id,
      );
      chip.type = "button";
      chip.onclick = () => {
        view.chip = id;
        view.detailKey = "";
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
    refreshButton = node(
      "button",
      undefined,
      "button secondary",
      "plugins-refresh",
    );
    refreshButton.type = "button";
    refreshButton.replaceChildren(
      icon("refresh"),
      document.createTextNode("Refresh"),
    );
    refreshButton.onclick = () => loadCatalogs(true);
    modeButton = node("button", undefined, "button secondary", "plugins-mode");
    modeButton.type = "button";
    modeButton.onclick = () => {
      view.mode = view.mode === "directory" ? "manage" : "directory";
      view.detailKey = "";
      render();
    };
    const toolbar = node("div", undefined, "plugins-toolbar");
    const projectLabelNode = node("label", "Project", "plugins-project-label");
    projectSelect = node(
      "select",
      undefined,
      "plugins-project",
      "plugins-project",
    );
    projectSelect.setAttribute("aria-label", "Project for plugins");
    projectSelect.onchange = () => {
      view.project = projectSelect.value;
      rowErrors.clear();
      states.clear();
      renderTrust();
      renderList();
      void loadStates(view.clis).then((applied) => applied && render());
    };
    projectLabelNode.append(projectSelect);
    toolbar.append(chips, search, projectLabelNode, refreshButton, modeButton);
    note = node("p", undefined, "hint plugins-note", "plugins-note");
    note.setAttribute("aria-live", "polite");
    status = node("p", undefined, "hint plugins-status", "plugins-status");
    status.setAttribute("role", "status");
    status.hidden = true;
    trustPanel = node("section", undefined, "project-trust", "project-trust");
    trustPanel.setAttribute("role", "region");
    trustPanel.hidden = true;
    list = node("div", undefined, "plugins-list", "plugins-list");
    list.tabIndex = -1; // the focus fallback when a row disappears under a switch
    panel.append(toolbar, note, status, trustPanel, list);
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
    const readId = ++stateRead,
      project = view.project,
      next = new Map();
    await Promise.all(
      clis.map(async ({ id }) => {
        try {
          const body = await request(
            "provider-state?provider=" +
              id +
              "&project_id=" +
              encodeURIComponent(project),
          );
          next.set(id, { body });
        } catch (error) {
          next.set(id, { error });
        }
      }),
    );
    if (readId !== stateRead || project !== view.project) return false;
    states.clear();
    for (const { id } of clis) {
      const result = next.get(id);
      if (result?.body) {
        states.set(id, {});
        acceptProviderBody(id, result.body);
        setProviderNotices(id, result.body.external_changes);
      } else {
        states.set(id, {
          error: result?.error?.message || "State is not readable here yet.",
        });
        setProviderNotices(id, []);
      }
    }
    showProviderNotices();
    return true;
  }

  async function loadCatalogs(force) {
    if (view.loading) return;
    view.loading = true;
    refreshButton.disabled = true;
    render();
    try {
      const inventory = (state || (await request("state"))).inventory;
      view.clis = inventory.services.filter(
        (info) => info.found && CLIS.includes(info.id),
      );
      const projects = [
        [NO_PROJECT, "No project"],
        ...(state?.settings?.projects || []).map((project) => [
          project.id,
          project.label || project.id,
        ]),
      ];
      projectSelect.replaceChildren(
        ...projects.map(([id, label]) => new Option(label, id)),
      );
      if (!projects.some(([id]) => id === view.project))
        view.project = NO_PROJECT;
      projectSelect.value = view.project;
      rowErrors.clear(); // a reload starts clean; a re-read on focus keeps the errors on their rows
      const reads = loadStates(view.clis);
      // One CLI at a time: the admin runs one operation at once and answers 429 to a second.
      for (const { id } of view.clis) {
        if (id === "deepseek") continue; // Its private state has no catalog command.
        const cached = integrationCatalogs.get(id);
        // catalogPending is shared with the Providers page so one CLI scan runs at a time.
        if ((!force && cached && !cached.error) || catalogPending.has(id))
          continue;
        catalogPending.add(id);
        try {
          integrationCatalogs.set(id, await requestCatalog(id));
        } catch (error) {
          integrationCatalogs.set(id, {
            ...(cached || { items: [] }),
            error: error.message,
          });
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
  document.addEventListener(
    "provider-notices",
    () => view.built && !view.loading && renderList(),
  );
  // Focus and visibilitychange fire together: the second one shares the read in flight.
  let rereading = null;
  const reread = () => {
    if (
      location.hash === "#plugins" &&
      view.built &&
      !view.loading &&
      !working &&
      document.visibilityState === "visible"
    )
      rereading ||= loadStates(view.clis)
        .then(render)
        .finally(() => (rereading = null));
  };
  window.addEventListener("focus", reread);
  document.addEventListener("visibilitychange", reread);
  enter();
})();
