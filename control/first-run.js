// First-run wizard (#69, D-052). A classic script loaded after admin.js: it reuses that file's
// globals ($, element, request, say, load, providerName, providerIcon, providerLoginButton) and keeps
// its own state inside this function. Nothing is stored in the browser: the choices go to the
// backend (/api/settings, then /api/first-run).
(() => {
  // Each entry translates when shown. Literal t() calls keep tests/test_locale_catalogs.py exact.
  const STEPS = [
    () => HarnessI18n.t("first_run.step.appearance"),
    () => HarnessI18n.t("first_run.step.providers"),
    () => HarnessI18n.t("first_run.step.defaults"),
  ];
  const SIGN_IN_PROVIDERS = ["codex", "claude"]; // the ones that can be turned on from here
  const LOGIN_PROVIDERS = new Set(SIGN_IN_PROVIDERS);
  const UNVERIFIED = {
    key_saved_unverified: () => HarnessI18n.t("first_run.unverified.key_saved"),
    credential_present_unverified: () =>
      HarnessI18n.t("first_run.unverified.credential_present"),
    timeout: () => HarnessI18n.t("first_run.unverified.timeout"),
    error: () => HarnessI18n.t("first_run.unverified.error"),
  };
  const dialog = $("first-run");
  const body = $("first-run-body");
  const failure = $("first-run-error");
  let wiz;

  const fresh = () => ({
    step: 0,
    shown: -1, // the step whose title last took focus
    busy: false,
    originalTheme: document.documentElement.dataset.palette,
    theme: document.documentElement.dataset.palette,
    rows: null, // the scan, or null while it runs
    models: {}, // provider id -> {model: [efforts]} for signed-in providers
    enable: {}, // provider id -> turn it on at the end
    model: "",
    effort: "",
    onTop: false,
  });

  const desktop = () => navigator.userAgent.includes("Electron");
  const named = (id) => providerName({ id, name: id });
  const group = (label) => {
    const box = element("div", undefined, "first-run-group");
    box.setAttribute("role", "group");
    box.setAttribute("aria-label", label);
    return box;
  };
  const showError = (message) => {
    failure.textContent = message || "";
    failure.hidden = !message;
  };

  function renderThemes() {
    const picker = group(HarnessI18n.t("first_run.group.theme"));
    picker.classList.add("theme-picker");
    for (const t of HarnessTheme.themes) {
      const button = element("button", undefined, "theme-choice");
      button.type = "button";
      button.dataset.themeChoice = t.id;
      button.dataset.testid = "first-run-theme-" + t.id;
      const swatches = element("span", undefined, "theme-swatches");
      swatches.setAttribute("aria-hidden", "true");
      for (const color of t.colors) {
        const swatch = element("span");
        swatch.style.backgroundColor = color; // property assignment: no inline style attribute string
        swatches.append(swatch);
      }
      button.append(
        swatches,
        element("strong", t.name, "theme-name"),
        element(
          "small",
          t.mode === "dark"
            ? HarnessI18n.t("first_run.theme.dark")
            : HarnessI18n.t("first_run.theme.light"),
        ),
      );
      button.onclick = () => {
        wiz.theme = t.id;
        HarnessTheme.apply(t.id, false); // preview only; the choice is saved on Finish
      };
      picker.append(button);
    }
    return [
      element("p", HarnessI18n.t("first_run.theme.hint"), "hint"),
      picker,
    ];
  }

  function providerStatus(row) {
    if (row.signed_in === true)
      return element(
        "span",
        HarnessI18n.t("first_run.provider.ready"),
        "pill good",
      );
    if (row.signed_in === null) {
      const unverified =
        UNVERIFIED[row.detail] ||
        (() => HarnessI18n.t("first_run.unverified.other"));
      return element("span", unverified(), "pill");
    }
    if (!row.found)
      return element(
        "span",
        row.id === "deepseek"
          ? HarnessI18n.t("first_run.provider.no_key")
          : HarnessI18n.t("first_run.provider.not_installed", {
              name: named(row.id),
            }),
        "pill",
      );
    return element(
      "span",
      HarnessI18n.t("first_run.provider.not_signed_in"),
      "pill",
    );
  }

  function providerRow(row) {
    const item = element("li", undefined, "first-run-provider");
    item.dataset.testid = "first-run-provider-" + row.id;
    item.dataset.signedIn = String(row.signed_in);
    item.dataset.found = String(row.found);
    const actions = element("span", undefined, "first-run-provider-actions");
    if (row.found && row.signed_in === false && LOGIN_PROVIDERS.has(row.id)) {
      const login = providerLoginButton({
        id: row.id,
        name: row.id,
        found: true,
      });
      login.dataset.testid = "first-run-login-" + row.id;
      actions.append(login);
    }
    item.append(
      providerIcon(row.id),
      element("strong", named(row.id)),
      providerStatus(row),
      actions,
    );
    return item;
  }

  async function scan() {
    wiz.rows = null;
    render();
    try {
      wiz.rows = (await request("first-run/scan", {})).providers;
    } catch (error) {
      wiz.rows = [];
      showError(
        HarnessI18n.t("first_run.scan.failed", { message: error.message }),
      );
    }
    if (wiz.step === 1) render();
  }

  function renderProviders() {
    if (!wiz.rows)
      return [
        element("p", HarnessI18n.t("first_run.providers.checking"), "hint"),
      ];
    const list = element("ul", undefined, "first-run-providers");
    list.append(...wiz.rows.map(providerRow));
    const again = element(
      "button",
      HarnessI18n.t("first_run.providers.rescan"),
      "button secondary",
    );
    again.type = "button";
    again.dataset.testid = "first-run-rescan";
    again.onclick = scan;
    return [
      element("p", HarnessI18n.t("first_run.providers.hint"), "hint"),
      list,
      again,
    ];
  }

  async function listModels() {
    const ready = wiz.rows.filter(
      (row) => row.signed_in === true && SIGN_IN_PROVIDERS.includes(row.id),
    );
    const results = await Promise.allSettled(
      ready.map((row) => request("check", { provider: row.id })),
    );
    wiz.models = {};
    results.forEach((result, index) => {
      const id = ready[index].id;
      if (result.status !== "fulfilled") return;
      const known = Object.entries(result.value.models || {}).filter(
        ([model]) => HarnessUI.selectableModel(id, model),
      );
      if (known.length) wiz.models[id] = Object.fromEntries(known);
    });
    for (const id of SIGN_IN_PROVIDERS)
      if (wiz.models[id] && !(id in wiz.enable)) wiz.enable[id] = true;
  }

  function modelOptions() {
    return Object.entries(wiz.models)
      .filter(([id]) => wiz.enable[id])
      .flatMap(([id, models]) =>
        Object.entries(models).map(([model, efforts]) => ({
          id,
          model,
          efforts,
        })),
      );
  }

  function labelled(text, control) {
    const label = element("label", text);
    label.append(control);
    return label;
  }

  function renderModelChoice() {
    const options = modelOptions();
    const model = element("select", undefined, "form-select");
    model.dataset.testid = "first-run-model";
    model.append(new Option(HarnessI18n.t("first_run.model.choose"), ""));
    for (const o of options)
      model.append(new Option(named(o.id) + " · " + o.model, o.model));
    model.value = options.some((o) => o.model === wiz.model) ? wiz.model : "";
    wiz.model = model.value;
    const effort = element("select", undefined, "form-select");
    effort.dataset.testid = "first-run-effort";
    const fill = () => {
      const levels =
        options
          .find((o) => o.model === wiz.model)
          ?.efforts.filter((e) => e !== "configured") || [];
      effort.replaceChildren(
        new Option(HarnessI18n.t("first_run.model.default_effort"), ""),
        ...levels.map((e) => new Option(e, e)),
      );
      effort.value = levels.includes(wiz.effort) ? wiz.effort : "";
      wiz.effort = effort.value;
      effort.disabled = !levels.length;
    };
    model.onchange = () => {
      wiz.model = model.value;
      fill();
    };
    effort.onchange = () => (wiz.effort = effort.value);
    fill();
    return [
      labelled(HarnessI18n.t("first_run.model.default_model"), model),
      labelled(HarnessI18n.t("first_run.model.effort"), effort),
    ];
  }

  function renderDefaults() {
    const nodes = [];
    const ids = SIGN_IN_PROVIDERS.filter((id) => wiz.models[id]);
    if (!ids.length)
      nodes.push(
        element("p", HarnessI18n.t("first_run.defaults.no_provider"), "hint"),
      );
    const enabled = group(HarnessI18n.t("first_run.group.enable"));
    for (const id of ids) {
      const row = toggle(
        HarnessI18n.t("first_run.defaults.turn_on", { name: named(id) }),
        wiz.enable[id],
        () => {},
        "",
      );
      const box = row.querySelector("input");
      box.dataset.testid = "first-run-enable-" + id;
      box.onchange = () => {
        wiz.enable[id] = box.checked;
        render();
      };
      enabled.append(row);
    }
    nodes.push(enabled);
    if (ids.length) nodes.push(...renderModelChoice());
    if (desktop()) {
      const row = toggle(
        HarnessI18n.t("first_run.defaults.always_on_top"),
        wiz.onTop,
        () => {},
        "",
      );
      const box = row.querySelector("input");
      box.dataset.testid = "first-run-always-on-top";
      box.onchange = () => (wiz.onTop = box.checked);
      nodes.push(row);
    }
    return nodes;
  }

  const RENDER = [renderThemes, renderProviders, renderDefaults];

  function render() {
    showError("");
    const heading = element("h3", STEPS[wiz.step]());
    heading.tabIndex = -1;
    heading.dataset.testid = "first-run-step-title";
    body.replaceChildren(heading, ...RENDER[wiz.step]());
    $("first-run-progress").textContent = HarnessI18n.t("first_run.progress", {
      step: wiz.step + 1,
      total: STEPS.length,
    });
    $("first-run-back").hidden = wiz.step === 0;
    const last = wiz.step === STEPS.length - 1;
    $("first-run-next").textContent = last
      ? HarnessI18n.t("first_run.action.finish")
      : HarnessI18n.t("first_run.action.next");
    $("first-run-next").dataset.testid = last
      ? "first-run-finish"
      : "first-run-next";
    HarnessTheme.apply(wiz.theme, false); // refreshes aria-pressed on the new theme buttons
    // New step, or focus fell out of the dialog with the replaced nodes: land on the step title.
    if (wiz.shown !== wiz.step || !dialog.contains(document.activeElement))
      heading.focus();
    wiz.shown = wiz.step;
  }

  async function go(step) {
    wiz.step = step;
    render();
    if (step === 1) await scan();
    if (step === 2) {
      setBusy(true);
      await listModels();
      setBusy(false);
      if (wiz.step === 2) render();
    }
  }

  function setBusy(busy) {
    wiz.busy = busy;
    dialog.setAttribute("aria-busy", String(busy));
    for (const id of ["first-run-back", "first-run-next", "first-run-skip"])
      $(id).disabled = busy;
  }

  async function enableProviders() {
    const chosen = SIGN_IN_PROVIDERS.filter(
      (id) => wiz.enable[id] && wiz.models[id],
    );
    if (!chosen.length) return;
    const settings = structuredClone((await request("state")).settings);
    for (const id of chosen) {
      const service = settings.services[id];
      if (!service)
        throw new Error(
          HarnessI18n.t("first_run.error.unavailable", { name: named(id) }),
        );
      const models = Object.keys(wiz.models[id]);
      service.added = true;
      service.enabled = true;
      service.models = [models.includes(wiz.model) ? wiz.model : models[0]];
    }
    await request("settings", settings);
  }

  function chosenPrefs() {
    const prefs = { theme: wiz.theme };
    if (wiz.model)
      prefs.chat_selection = { model: wiz.model, effort: wiz.effort };
    if (desktop()) prefs.always_on_top = wiz.onTop;
    return prefs;
  }

  async function conclude(work, message) {
    if (wiz.busy) return;
    setBusy(true);
    showError("");
    try {
      await work();
      closeWizard(false);
      await load({ select: false });
      say(message);
    } catch (error) {
      showError(error.message); // settings may be saved; the wizard stays open and completed stays false
    } finally {
      setBusy(false);
    }
  }

  const finish = () =>
    conclude(async () => {
      await enableProviders();
      await request("first-run", { prefs: chosenPrefs() });
    }, HarnessI18n.t("first_run.done.complete"));
  const skip = () =>
    conclude(
      () => request("first-run", {}),
      HarnessI18n.t("first_run.done.skipped"),
    );

  function closeWizard(revert = true) {
    if (revert) HarnessTheme.apply(wiz.originalTheme, false);
    if (dialog.open) dialog.close();
  }

  function open() {
    wiz = fresh();
    setBusy(false);
    if (!dialog.open) dialog.showModal();
    render();
  }

  $("first-run-back").onclick = () => go(wiz.step - 1);
  $("first-run-next").onclick = () =>
    wiz.step === STEPS.length - 1 ? finish() : go(wiz.step + 1);
  $("first-run-skip").onclick = skip;
  $("first-run-close").onclick = () => closeWizard();
  // Escape closes for now: the setup is not marked done, so it returns on the next open.
  dialog.addEventListener("cancel", (event) => {
    if (wiz?.busy) event.preventDefault();
    else HarnessTheme.apply(wiz.originalTheme, false);
  });
  $("first-run-again").onclick = () =>
    action(async () => {
      await request("first-run:reset", {});
      open();
    });

  (async () => {
    try {
      const status = await request("first-run");
      // Only an explicit false opens it; the admin shown inside the harness never auto-opens.
      if (
        status.completed === false &&
        !document.documentElement.dataset.embedded
      )
        open();
    } catch {
      // The wizard is optional; the admin works without it.
    }
  })();
})();
