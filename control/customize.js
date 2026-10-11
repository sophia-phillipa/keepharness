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
    ["plugins", HarnessI18n.t("customize.chip.plugins"), "plugin", "cube"],
    ["apps", HarnessI18n.t("customize.chip.apps"), "app", "world"],
    ["mcps", HarnessI18n.t("customize.chip.mcps"), "mcp", "plug"],
    ["skills", HarnessI18n.t("customize.chip.skills"), "skill", "list-check"],
    ["hooks", HarnessI18n.t("customize.chip.hooks"), "hook", "plug"],
    [
      "rules",
      HarnessI18n.t("customize.chip.rules"),
      "instructions",
      "list-check",
    ],
  ];
  const NO_PROJECT = "sem-projeto";
  // Add actions of the Plugins toolbar: [catalog action, testid stem]; addLabel gives the menu label.
  const ADD_ITEMS = [
    ["marketplace_add", "plugins-add-marketplace"],
    ["connector_add", "plugins-add-mcp"],
  ];
  // Menu label of an Add action; provider is null unless several providers offer one.
  const addLabel = (action, provider) => {
    if (action === "marketplace_add")
      return provider === null
        ? HarnessI18n.t("customize.add.marketplace")
        : HarnessI18n.t("customize.add.marketplace_to", { provider });
    return provider === null
      ? HarnessI18n.t("customize.add.mcp")
      : HarnessI18n.t("customize.add.mcp_to", { provider });
  };
  const view = {
    chip: "plugins",
    skillScope: "user",
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
  const SKILL_SCOPES = [
    ["user", HarnessI18n.t("customize.scope.user")],
    ["project", HarnessI18n.t("customize.scope.project")],
  ];
  let list,
    scopeTabs,
    note,
    status,
    menu,
    refreshButton,
    modeButton,
    projectSelect,
    trustPanel,
    addWrapper,
    addButton,
    addDialog,
    addTitle,
    addForm,
    addInput,
    addError,
    addSubmit,
    addInfo = null,
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
    item.marketplace ||
    String(item.id || "").split("@")[1] ||
    HarnessI18n.t("customize.marketplace.local");

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
  // Project tab: project and local items; User tab: everything else.
  function inScope(item) {
    return ["project", "local"].includes(item.scope);
  }

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
            {
              id: item.id,
              name: item.name,
              kind,
              scope: item.scope,
              status: "installed",
            },
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
      !confirm(HarnessI18n.t("customize.uninstall.confirm", { label, where }))
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
      say(HarnessI18n.t("customize.uninstall.started", { label, where }));
    });
  }
  // Up and Down move the focus through a menu's items, wrapping at both ends.
  function arrowMenu(event, popup) {
    const step = { ArrowDown: 1, ArrowUp: -1 }[event.key];
    if (!step) return;
    event.preventDefault();
    const items = [...popup.children];
    items[
      (items.indexOf(document.activeElement) + step + items.length) %
        items.length
    ].focus();
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
          ? HarnessI18n.t("customize.uninstall.from_provider", {
              provider: providerName(info),
            })
          : HarnessI18n.t("customize.uninstall.label"),
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
    popup.addEventListener("keydown", (event) => arrowMenu(event, popup));
    button.after(popup);
    button.setAttribute("aria-expanded", "true");
    menu = { node: popup, button };
    popup.firstElementChild.focus();
  }

  // One entry per provider and Add action. The provider name shows only when several providers offer an Add action.
  function addEntries() {
    const OPEN = { marketplace_add: openAddDialog, connector_add: addMcp };
    const offering = view.clis
      .map((info) => ({
        info,
        actions: integrationCatalogs.get(info.id)?.actions || [],
      }))
      .filter(({ actions }) =>
        ADD_ITEMS.some(([action]) => actions.includes(action)),
      );
    const named = offering.length > 1;
    return offering.flatMap(({ info, actions }) =>
      ADD_ITEMS.filter(([action]) => actions.includes(action)).map(
        ([action, testid]) => ({
          label: addLabel(action, named ? providerName(info) : null),
          testid: testid + "-" + info.id,
          open: () => OPEN[action](info),
        }),
      ),
    );
  }
  // The Add button shows on the Plugins chip only, like the Manage toggle beside it.
  function renderAdd() {
    const shown = view.chip === "plugins" && addEntries().length > 0;
    if (!shown) addWrapper.remove();
    else if (!addWrapper.isConnected) modeButton.after(addWrapper);
  }
  function openAddMenu() {
    const entries = addEntries();
    const reopen = menu?.button !== addButton;
    closeMenu();
    if (!reopen || !entries.length) return;
    const popup = node("div", undefined, "plugins-menu", "plugins-add-menu");
    popup.setAttribute("role", "menu");
    for (const { label, testid, open } of entries) {
      const entry = node("button", label, "plugins-menu-item", testid);
      entry.type = "button";
      entry.setAttribute("role", "menuitem");
      entry.onclick = () => {
        closeMenu(true);
        open();
      };
      popup.append(entry);
    }
    popup.addEventListener("keydown", (event) => arrowMenu(event, popup));
    addButton.after(popup);
    addButton.setAttribute("aria-expanded", "true");
    menu = { node: popup, button: addButton };
    popup.firstElementChild.focus();
  }
  function openAddDialog(info) {
    addInfo = info;
    addTitle.textContent = HarnessI18n.t("customize.add.marketplace_to", {
      provider: providerName(info),
    });
    addInput.value = "";
    addError.textContent = "";
    addDialog.showModal();
  }
  // The MCP server path reuses the provider wizard at its integrations step: no second form.
  function addMcp(info) {
    openWizard(info.id);
    if ($("provider-dialog").hidden) return;
    showStep(3);
  }
  async function submitAdd() {
    if (!addInfo || addSubmit.disabled) return;
    const info = addInfo;
    addError.textContent = "";
    addSubmit.disabled = true;
    try {
      await requestRaw("integration", {
        provider: info.id,
        action: "marketplace_add",
        source: addInput.value.trim(),
      });
    } catch (error) {
      addError.textContent = error.message;
      return;
    } finally {
      addSubmit.disabled = false;
    }
    addDialog.close();
    report(
      HarnessI18n.t("customize.add.started", { provider: providerName(info) }),
    );
    pollOperations();
  }
  function buildAddDialog() {
    const dialog = node(
      "dialog",
      undefined,
      "plugins-add-dialog",
      "plugins-add-dialog",
    );
    dialog.id = "plugins-add-dialog";
    addTitle = node("h2", undefined, "plugins-add-title", "plugins-add-title");
    addTitle.id = "plugins-add-title";
    dialog.setAttribute("aria-labelledby", "plugins-add-title");
    addForm = node("form", undefined, "plugins-add-form", "plugins-add-form");
    addInput = node(
      "input",
      undefined,
      "plugins-add-source",
      "plugins-add-source",
    );
    addInput.type = "text";
    addInput.required = true;
    addInput.autocomplete = "off";
    addInput.spellcheck = false;
    addInput.setAttribute("aria-describedby", "plugins-add-hint");
    const field = node(
      "label",
      HarnessI18n.t("customize.add.source"),
      "plugins-add-field",
    );
    field.append(addInput);
    const hint = node(
      "p",
      HarnessI18n.t("customize.add.hint"),
      "hint",
      "plugins-add-hint",
    );
    hint.id = "plugins-add-hint";
    addError = node("p", undefined, "plugins-add-error", "plugins-add-error");
    addError.setAttribute("role", "alert");
    const cancel = node(
      "button",
      HarnessI18n.t("customize.add.cancel"),
      "button secondary",
      "plugins-add-cancel",
    );
    cancel.type = "button";
    cancel.onclick = () => dialog.close();
    addSubmit = node(
      "button",
      HarnessI18n.t("customize.add.submit"),
      "button primary",
      "plugins-add-submit",
    );
    addSubmit.type = "submit";
    const actions = node("div", undefined, "plugins-add-actions");
    actions.append(cancel, addSubmit);
    addForm.append(field, hint, addError, actions);
    addForm.onsubmit = (event) => {
      event.preventDefault();
      void submitAdd();
    };
    dialog.append(addTitle, addForm);
    // The dialog returns focus to the Add button whichever way it closes (Escape, Cancel, success).
    dialog.addEventListener("close", () => addButton.focus());
    return dialog;
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
      report(HarnessI18n.t("customize.install.started", { label, where }));
      pollOperations();
    });
  }

  // The status line of the switches: #feedback can sit inside the closed provider wizard.
  function report(text) {
    status.textContent = text;
    status.hidden = !text;
  }
  const capitalized = (text) => text.charAt(0).toUpperCase() + text.slice(1);
  // Display names of the scopes and of the on/off words; the protocol values keep their English form.
  const scopeName = (scope) =>
    scope === "user"
      ? HarnessI18n.t("customize.scope.user")
      : scope === "project"
        ? HarnessI18n.t("customize.scope.project")
        : scope === "local"
          ? HarnessI18n.t("customize.scope.local")
          : capitalized(scope);
  const onOffText = (enabled) =>
    enabled
      ? HarnessI18n.t("customize.state.on")
      : HarnessI18n.t("customize.state.off");

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
          body.trust?.trusted
            ? HarnessI18n.t("customize.trust.trusted", {
                project: projectLabel(),
              })
            : HarnessI18n.t("customize.trust.untrusted", {
                project: projectLabel(),
              }),
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
            ? approved
              ? HarnessI18n.t("customize.mcp.now_approved", {
                  server,
                  project: projectLabel(),
                })
              : HarnessI18n.t("customize.mcp.now_not_approved", {
                  server,
                  project: projectLabel(),
                })
            : body.trust?.required
              ? HarnessI18n.t("customize.mcp.saved_trust_first", {
                  server,
                  project: projectLabel(),
                })
              : HarnessI18n.t("customize.mcp.denied", { server }),
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
    trustPanel.setAttribute(
      "aria-label",
      HarnessI18n.t("customize.trust.aria_label"),
    );
    if (trust?.inherited_from)
      trustPanel.append(
        node(
          "p",
          HarnessI18n.t("customize.trust.inherited", {
            from: trust.inherited_from,
          }),
          "project-trust-inherited",
        ),
      );
    if (trust?.required) {
      const copy = node("div", undefined, "project-trust-copy");
      copy.append(
        node(
          "strong",
          HarnessI18n.t("customize.trust.question", {
            project: projectLabel(),
          }),
        ),
        node("p", HarnessI18n.t("customize.trust.explain")),
      );
      const accept = node(
        "button",
        HarnessI18n.t("customize.trust.accept", { project: projectLabel() }),
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
          HarnessI18n.t("customize.trust.revoke", { project: projectLabel() }),
          "button secondary",
        );
      revoke.type = "button";
      revoke.disabled = !currentTrustContext(context);
      revoke.onclick = () => writeTrust(context, false);
      trustPanel.append(
        node("p", HarnessI18n.t("customize.trust.trusted_note")),
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
          item.enabled === false
            ? HarnessI18n.t("customize.mcp.status_disabled")
            : item.approved
              ? HarnessI18n.t("customize.mcp.status_approved")
              : HarnessI18n.t("customize.mcp.status_not_approved"),
        ),
      );
      const toggle = node(
        "button",
        item.approved
          ? HarnessI18n.t("customize.mcp.revoke", { server: item.server })
          : HarnessI18n.t("customize.mcp.approve", { server: item.server }),
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
          HarnessI18n.t("customize.state.changed", {
            label,
            state: onOffText(!stateItem.enabled),
            where,
          }),
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
            now
              ? HarnessI18n.t("customize.state.changed_now", {
                  where,
                  label,
                  state: onOffText(now.enabled),
                })
              : HarnessI18n.t("customize.state.changed_removed", {
                  where,
                  label,
                }),
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
          HarnessI18n.t("customize.plugin.changed_outside"),
          "pill plugins-changed",
          "plugin-changed",
        ),
      );
    let text = !read?.snapshot
      ? HarnessI18n.t("customize.plugin.where_text", {
          where,
          text:
            read?.error || HarnessI18n.t("customize.plugin.state_unreadable"),
        })
      : matchingStates.length > 1
        ? HarnessI18n.t("customize.plugin.multiple_installed", { where })
        : !stateItem && variants.some((item) => item.status === "installed")
          ? HarnessI18n.t("customize.plugin.catalog_unmatched", { where })
          : !stateItem && variants.length > 1
            ? HarnessI18n.t("customize.plugin.multiple_catalog", { where })
            : !stateItem &&
                variants.length === 1 &&
                variants[0].status === "available"
              ? HarnessI18n.t("customize.plugin.not_installed", { where })
              : !stateItem
                ? HarnessI18n.t("customize.plugin.not_offered", { where })
                : [
                    HarnessI18n.t("customize.plugin.source_line", {
                      where,
                      scope: scopeName(stateItem.scope),
                      source: stateItem.source,
                    }),
                    stateItem.reason,
                    stateItem.affects?.length &&
                      HarnessI18n.t("customize.plugin.affects", {
                        list: stateItem.affects.join(", "),
                      }),
                    rowErrors.get(info.id + "|" + stateItem.id),
                  ]
                    .filter(Boolean)
                    .join(" · ");
    const hint = node("small", text, "plugins-row-note", "plugin-note");
    hint.id = "plugin-note-" + ++noteCount;
    hint.dataset.provider = info.id;
    // A plugin skill is managed by its plugin: the reason text stays on the row, with no switch.
    const partOfPlugin = Boolean(stateItem?.plugin);
    if (stateItem && !partOfPlugin) {
      const input = node(
        "button",
        undefined,
        "plugins-switch",
        "plugin-switch",
      );
      input.type = "button";
      input.setAttribute("role", "switch");
      input.setAttribute(
        "aria-label",
        HarnessI18n.t("customize.plugin.switch_label", { label, where }),
      );
      input.setAttribute("aria-checked", String(stateItem.enabled));
      input.setAttribute("aria-describedby", hint.id);
      input.dataset.itemId = stateItem.id;
      input.dataset.provider = info.id;
      input.disabled = !stateItem.writable;
      input.onclick = () => writeState(group, info, stateItem);
      cell.append(input);
    } else if (
      !stateItem &&
      detail &&
      read?.snapshot &&
      variants.length === 1 &&
      variants[0].status === "available"
    ) {
      const variant = variants[0];
      const installButton = node(
        "button",
        HarnessI18n.t("customize.plugin.install"),
        "button secondary",
        "plugin-install",
      );
      installButton.type = "button";
      installButton.setAttribute(
        "aria-label",
        HarnessI18n.t("customize.plugin.install_label", { label, where }),
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
    open.setAttribute(
      "aria-label",
      HarnessI18n.t("customize.card.view_details", { label }),
    );
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
      actionButton.setAttribute(
        "aria-label",
        HarnessI18n.t("customize.card.actions", { label }),
      );
      actionButton.setAttribute("aria-haspopup", "menu");
      actionButton.setAttribute("aria-expanded", "false");
      actionButton.onclick = () => openMenu(actionButton, group, true);
    } else if (hasInstallTarget) {
      actionButton.textContent = "+";
      actionButton.setAttribute(
        "aria-label",
        HarnessI18n.t("customize.card.install", { label }),
      );
      actionButton.onclick = () => openDetail(group, true, source);
    } else {
      actionButton.textContent = "›";
      actionButton.setAttribute(
        "aria-label",
        HarnessI18n.t("customize.card.view_availability", { label }),
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
      if (!sources.size)
        sources.add(HarnessI18n.t("customize.marketplace.local"));
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
    back.setAttribute(
      "aria-label",
      HarnessI18n.t("customize.detail.back_aria"),
    );
    back.append(
      icon("chevron-left"),
      document.createTextNode(HarnessI18n.t("customize.detail.back")),
    );
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
      version.append(
        node("dt", HarnessI18n.t("customize.detail.version")),
        node("dd", versionValue),
      );
      detail.append(version);
    }
    for (const [name, entries] of [
      [HarnessI18n.t("customize.detail.apps"), metadata.apps],
      [HarnessI18n.t("customize.detail.skills"), metadata.skills],
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
      [HarnessI18n.t("customize.detail.developer"), metadata.developer],
      [HarnessI18n.t("customize.detail.source"), metadata.source],
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
    providers.append(node("h3", HarnessI18n.t("customize.detail.providers")));
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
    more.setAttribute(
      "aria-label",
      HarnessI18n.t("customize.card.actions", { label }),
    );
    more.setAttribute("aria-haspopup", "menu");
    more.setAttribute("aria-expanded", "false");
    more.disabled = !installedTargets(group).length;
    if (more.disabled) more.title = HarnessI18n.t("customize.row.no_installed");
    else more.onclick = () => openMenu(more, group);
    const menuWrap = node("div", undefined, "plugins-row-menu");
    menuWrap.append(more);
    const article = node("article", undefined, "plugins-row", "plugin-row");
    article.dataset.itemId = group.item.id;
    article.append(connectorIcon(group.item), text, badges, menuWrap);
    return article;
  }

  const resourceKind = () =>
    ({ hooks: "hook", rules: "instructions" })[view.chip];
  const resourceItems = (kind) =>
    view.clis.flatMap((info) =>
      (states.get(info.id)?.snapshot?.items || [])
        .filter((item) => item.kind === kind)
        .map((item) => ({ info, item })),
    );
  const fieldLabel = (key) =>
    capitalized(key.replace(/([a-z])([A-Z])/g, "$1 $2").replaceAll("_", " "));

  function resourceRow(info, item) {
    const row = node(
      "article",
      undefined,
      "plugins-resource-row",
      "provider-resource-row",
    );
    row.append(
      node("h4", item.name),
      node(
        "p",
        HarnessI18n.t("customize.plugin.source_line", {
          where: providerName(info),
          scope: scopeName(item.scope),
          source: item.source,
        }),
        "plugins-resource-source",
      ),
    );
    row.append(
      node(
        "p",
        (item.enabled
          ? HarnessI18n.t("customize.resource.enabled")
          : HarnessI18n.t("customize.resource.disabled")) +
          (item.reason ? " · " + item.reason : ""),
        "plugins-resource-status",
      ),
    );
    if (
      providerNotices.get(info.id)?.some((notice) => notice.item_id === item.id)
    )
      row.append(
        node(
          "span",
          HarnessI18n.t("customize.plugin.changed_outside"),
          "pill plugins-changed",
          "resource-changed",
        ),
      );
    const fields = node("dl", undefined, "plugins-resource-fields");
    for (const [key, value] of Object.entries(item.details || {})) {
      if (["preview", "content_sha256"].includes(key) || value === null)
        continue;
      const text =
        key === "size_bytes"
          ? HarnessI18n.t("customize.resource.size", { size: value })
          : typeof value === "object"
            ? JSON.stringify(value, null, 2)
            : String(value);
      fields.append(node("dt", fieldLabel(key)), node("dd", text));
    }
    row.append(fields);
    if (typeof item.details?.preview === "string") {
      const preview = node("details", undefined, "plugins-resource-preview");
      preview.append(
        node(
          "summary",
          HarnessI18n.t("customize.resource.preview", { name: item.name }),
        ),
        node("pre", item.details.preview),
      );
      row.append(preview);
    }
    return row;
  }

  function renderResources(kind) {
    const query = view.query.trim().toLocaleLowerCase();
    const sections = view.clis.map((info) => {
      const section = node("section", undefined, "plugins-resources");
      section.setAttribute(
        "aria-label",
        kind === "hook"
          ? HarnessI18n.t("customize.resource.section_hooks", {
              provider: providerName(info),
            })
          : HarnessI18n.t("customize.resource.section_rules", {
              provider: providerName(info),
            }),
      );
      section.append(node("h2", providerName(info)));
      const data = states.get(info.id);
      if (data?.error) {
        section.append(node("p", data.error, "hint"));
        return section;
      }
      for (const warning of data?.snapshot?.warnings || [])
        section.append(node("p", warning, "hint"));
      if (kind === "instructions")
        section.append(
          node("p", HarnessI18n.t("customize.resource.preview_note"), "hint"),
        );
      const items = (data?.snapshot?.items || []).filter(
        (item) =>
          item.kind === kind &&
          [
            item.name,
            item.scope,
            item.source,
            JSON.stringify(item.details || {}),
          ]
            .join(" ")
            .toLocaleLowerCase()
            .includes(query),
      );
      if (!items.length) {
        section.append(
          node(
            "p",
            query
              ? HarnessI18n.t("customize.resource.no_matches", {
                  query: view.query.trim(),
                })
              : kind === "hook"
                ? HarnessI18n.t("customize.resource.no_hooks")
                : HarnessI18n.t("customize.resource.no_rules"),
            "hint",
          ),
        );
        if (!query && kind === "hook")
          section.append(
            node("p", HarnessI18n.t("customize.resource.hooks_hint"), "hint"),
          );
      }
      for (const scope of [...new Set(items.map((item) => item.scope))]) {
        section.append(node("h3", scopeName(scope)));
        section.append(
          ...items
            .filter((item) => item.scope === scope)
            .map((item) => resourceRow(info, item)),
        );
      }
      return section;
    });
    list.replaceChildren(...sections);
    if (!sections.length)
      list.append(
        node("p", HarnessI18n.t("customize.plugin.no_provider_cli"), "hint"),
      );
  }

  function renderList() {
    list.setAttribute("aria-busy", String(view.loading));
    if (view.loading) {
      const wait = node(
        "p",
        HarnessI18n.t("customize.plugin.loading"),
        "hint",
        "plugins-loading",
      );
      wait.setAttribute("role", "status");
      return list.replaceChildren(wait);
    }
    const empty = (message) =>
      list.replaceChildren(node("p", message, "hint", "plugins-empty"));
    if (resourceKind()) return renderResources(resourceKind());
    if (view.chip !== "plugins") {
      const kind = CHIPS.find(([id]) => id === view.chip)[2];
      const query = view.query.trim().toLocaleLowerCase();
      const rows = installed(kind).filter(
        (group) =>
          (kind !== "skill" ||
            inScope(group.item) === (view.skillScope === "project")) &&
          connectorLabel(group.item).toLocaleLowerCase().includes(query),
      );
      return rows.length
        ? list.replaceChildren(...rows.map(row))
        : empty(
            kind === "app"
              ? HarnessI18n.t("customize.empty.apps")
              : kind === "mcp"
                ? HarnessI18n.t("customize.empty.mcps")
                : view.skillScope === "project"
                  ? HarnessI18n.t("customize.empty.project_skills")
                  : HarnessI18n.t("customize.empty.user_skills"),
          );
    }
    if (!view.clis.length)
      return empty(HarnessI18n.t("customize.plugin.no_cli"));
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
            ? HarnessI18n.t("customize.plugin.no_match", {
                query: view.query.trim(),
              })
            : HarnessI18n.t("customize.plugin.none_installed"),
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
          ? HarnessI18n.t("customize.plugin.no_match", {
              query: view.query.trim(),
            })
          : HarnessI18n.t("customize.plugin.none_available"),
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
        .map((line) =>
          HarnessI18n.t("customize.plugin.where_text", {
            where: providerName(info),
            text: line,
          }),
        );
    });
    note.textContent = lines.join("\n");
    note.hidden = !lines.length || view.loading;
  }

  function render() {
    closeMenu();
    renderAdd();
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
              ["hook", "instructions"].includes(kind)
                ? resourceItems(kind).length
                : kind === "plugin" && view.mode === "directory"
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
        view.mode === "directory"
          ? HarnessI18n.t("customize.toolbar.manage")
          : HarnessI18n.t("customize.toolbar.browse"),
      ),
    );
    modeButton.hidden = view.chip !== "plugins";
    scopeTabs.hidden = view.chip !== "skills";
    // The list is the tab panel of the selected Skills scope, and a plain list elsewhere.
    if (scopeTabs.hidden) {
      list.removeAttribute("role");
      list.removeAttribute("aria-labelledby");
    } else {
      list.setAttribute("role", "tabpanel");
      list.setAttribute(
        "aria-labelledby",
        "plugins-scope-tab-" + view.skillScope,
      );
    }
    for (const tab of scopeTabs.children) {
      const selected = tab.dataset.scope === view.skillScope;
      tab.setAttribute("aria-selected", String(selected));
      tab.tabIndex = selected ? 0 : -1;
    }
    const search = panel.querySelector(".plugins-search");
    search.placeholder =
      view.chip === "hooks"
        ? HarnessI18n.t("customize.search.hooks")
        : view.chip === "rules"
          ? HarnessI18n.t("customize.search.rules")
          : HarnessI18n.t("customize.search.plugins");
    search.setAttribute("aria-label", search.placeholder);
    renderList();
  }

  function build() {
    const chips = node("div", undefined, "plugins-chips");
    chips.setAttribute("role", "group");
    chips.setAttribute(
      "aria-label",
      HarnessI18n.t("customize.chip.group_label"),
    );
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
    search.placeholder = HarnessI18n.t("customize.search.plugins");
    search.setAttribute(
      "aria-label",
      HarnessI18n.t("customize.search.plugins"),
    );
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
      document.createTextNode(HarnessI18n.t("customize.toolbar.refresh")),
    );
    refreshButton.onclick = () => loadCatalogs(true);
    modeButton = node("button", undefined, "button secondary", "plugins-mode");
    modeButton.type = "button";
    modeButton.onclick = () => {
      view.mode = view.mode === "directory" ? "manage" : "directory";
      view.detailKey = "";
      render();
    };
    addWrapper = node("div", undefined, "plugins-add");
    addButton = node("button", undefined, "button secondary", "plugins-add");
    addButton.id = "plugins-add";
    addButton.type = "button";
    addButton.setAttribute("aria-haspopup", "menu");
    addButton.setAttribute("aria-expanded", "false");
    addButton.replaceChildren(
      icon("plus"),
      document.createTextNode(HarnessI18n.t("customize.toolbar.add")),
    );
    addButton.onclick = () => openAddMenu();
    addWrapper.append(addButton);
    addDialog = buildAddDialog();
    const toolbar = node("div", undefined, "plugins-toolbar");
    const projectLabelNode = node(
      "label",
      HarnessI18n.t("customize.toolbar.project"),
      "plugins-project-label",
    );
    projectSelect = node(
      "select",
      undefined,
      "plugins-project",
      "plugins-project",
    );
    projectSelect.setAttribute(
      "aria-label",
      HarnessI18n.t("customize.toolbar.project_aria"),
    );
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
    scopeTabs = node(
      "div",
      undefined,
      "plugins-scope-tabs",
      "plugins-scope-tabs",
    );
    scopeTabs.setAttribute("role", "tablist");
    scopeTabs.setAttribute(
      "aria-label",
      HarnessI18n.t("customize.scope.aria_label"),
    );
    scopeTabs.hidden = true;
    scopeTabs.addEventListener("keydown", (event) => {
      const tabs = [...scopeTabs.children];
      const current = tabs.indexOf(document.activeElement);
      const moves = {
        ArrowLeft: current - 1,
        ArrowRight: current + 1,
        Home: 0,
        End: tabs.length - 1,
      };
      if (!(event.key in moves)) return;
      event.preventDefault();
      const target = tabs[(moves[event.key] + tabs.length) % tabs.length];
      view.skillScope = target.dataset.scope;
      render();
      target.focus();
    });
    for (const [id, label] of SKILL_SCOPES) {
      const tab = node(
        "button",
        label,
        "plugins-chip plugins-scope-tab",
        "plugins-scope-tab-" + id,
      );
      tab.type = "button";
      tab.setAttribute("role", "tab");
      tab.id = "plugins-scope-tab-" + id;
      tab.setAttribute("aria-controls", "plugins-list");
      tab.dataset.scope = id;
      tab.onclick = () => {
        view.skillScope = id;
        render();
      };
      scopeTabs.append(tab);
    }
    list = node("div", undefined, "plugins-list", "plugins-list");
    list.id = "plugins-list";
    list.tabIndex = -1; // the focus fallback when a row disappears under a switch
    panel.append(toolbar, note, status, trustPanel, scopeTabs, list, addDialog);
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
  // An explicit Refresh (fresh) asks the server to skip its short coalescing window.
  async function loadStates(clis, fresh = false) {
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
              encodeURIComponent(project) +
              (fresh ? "&refresh=1" : ""),
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
          error:
            result?.error?.message ||
            HarnessI18n.t("customize.plugin.state_unreadable"),
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
        [NO_PROJECT, HarnessI18n.t("customize.toolbar.no_project")],
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
      const reads = loadStates(view.clis, force);
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
