"use strict";
const $ = (id) => document.getElementById(id);
let state,
  settings,
  working = false,
  wizard = false,
  editing = null,
  unsaved = false;
const settingsSearchIndexState = (window.settingsSearchIndexState = {
  lastGoodIndex: null,
  refreshing: true,
  lastError: null,
});
function settingsSearchChanged(status, error = null) {
  if (status === "refreshing") {
    settingsSearchIndexState.refreshing = true;
    settingsSearchIndexState.lastError = null;
  } else if (status === "ready") {
    settingsSearchIndexState.refreshing = false;
    settingsSearchIndexState.lastError = null;
  } else if (status === "error") {
    settingsSearchIndexState.refreshing = false;
    settingsSearchIndexState.lastError = error;
  }
  document.dispatchEvent(
    new CustomEvent("keepharness:settings-index-change", {
      detail: { status },
    }),
  );
}
let profileModel = "",
  profileDirty = false,
  discoveredModelFiles = [];
function visibleProviders() {
  return state.inventory.services.filter((info) => info.id !== "gemini");
}
function say(text, error = false) {
  HarnessUI.notice(
    $("network").open ? $("network-feedback") : $("feedback"),
    text,
    { error, persistent: !error && /…$/.test(text) },
  );
}
let activeOperation = null,
  operationOpener = null;
async function request(path, data) {
  const starts =
    ["provider-login", "integration", "model-install", "local-start"].includes(
      path,
    ) && data !== undefined;
  if (starts) {
    $("operation-dialog").showModal();
    $("operation-message").textContent = HarnessI18n.t(
      "admin.operation.starting",
    );
    $("operation-retry").hidden = true;
    activeOperation = null;
    $("operations").replaceChildren();
    $("operations-panel").hidden = true;
  }
  try {
    const result = await requestRaw(path, data);
    if (starts) {
      activeOperation = { path, data, id: result.id };
      $("operation-message").textContent = HarnessI18n.t(
        "admin.operation.started",
      );
      pollOperations();
    }
    return result;
  } catch (error) {
    if (starts) {
      $("operation-message").textContent = error.message;
      pollOperations();
    }
    throw error;
  }
}
async function requestRaw(path, data) {
  let r;
  try {
    r = await fetch("/api/" + path, {
      ...(data === undefined
        ? {}
        : {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "X-Harness-Admin": "1",
            },
            body: JSON.stringify(data),
          }),
      signal: AbortSignal.timeout(30000),
    });
  } catch (e) {
    throw Error(
      e.name === "TimeoutError"
        ? HarnessI18n.t("admin.request.timeout")
        : HarnessI18n.t("admin.request.connect_failed"),
    );
  }
  let value;
  try {
    value = await r.json();
  } catch {
    throw Error(
      HarnessI18n.t("admin.request.unexpected_response", {
        status: r.status,
      }),
    );
  }
  if (!r.ok) {
    const sentence =
      {
        model_not_available: HarnessI18n.t("admin.error.model_not_available"),
        authentication_required: HarnessI18n.t(
          "admin.error.authentication_required",
        ),
        permission_denied: HarnessI18n.t("admin.error.permission_denied"),
        operation_failed: HarnessI18n.t("admin.error.operation_failed"),
        invalid_request: HarnessI18n.t("admin.error.invalid_request"),
        invalid_json: HarnessI18n.t("admin.error.invalid_json"),
        provider_state_conflict: HarnessI18n.t(
          "admin.error.provider_state_conflict",
        ),
        provider_state_write_unsupported: HarnessI18n.t(
          "admin.error.provider_state_write_unsupported",
        ),
        provider_state_version_untested: HarnessI18n.t(
          "admin.error.provider_state_version_untested",
        ),
        provider_state_validation_failed: HarnessI18n.t(
          "admin.error.provider_state_validation_failed",
        ),
        provider_state_unreadable: HarnessI18n.t(
          "admin.error.provider_state_unreadable",
        ),
        provider_command_failed: HarnessI18n.t(
          "admin.error.provider_command_failed",
        ),
        provider_unknown: HarnessI18n.t("admin.error.provider_unknown"),
        project_unknown: HarnessI18n.t("admin.error.project_unknown"),
      }[value.error] ||
      value.error ||
      HarnessI18n.t("admin.error.generic");
    // Provider-state errors carry the adapter's own sentence (already capped by the server).
    const detail = value.message || value.provider_message;
    const error = Error(detail ? sentence + " " + detail : sentence);
    error.status = r.status;
    error.body = value;
    throw error;
  }
  return value;
}
function dirty() {
  unsaved = true;
  $("dirty").textContent = HarnessI18n.t("admin.common.unsaved");
}
window.addEventListener("beforeunload", (event) => {
  if (unsaved || profileDirty) {
    event.preventDefault();
    event.returnValue = "";
  }
});
function element(tag, text, cls) {
  const el = document.createElement(tag);
  if (text !== undefined) el.textContent = text;
  if (cls) el.className = cls;
  if (cls?.includes("button")) {
    el.classList.add("btn");
    if (cls.split(" ").includes("primary")) el.classList.add("btn-primary");
  }
  if (cls?.split(" ").includes("panel") || cls === "provider-card")
    el.classList.add("card");
  return el;
}
function toggle(text, checked, change, detail) {
  const label = element("label", undefined, "toggle-row");
  const input = element("input");
  input.type = "checkbox";
  input.setAttribute("aria-label", text);
  if (detail) input.setAttribute("aria-description", detail);
  input.checked = checked;
  input.onchange = () => {
    change(input.checked);
    dirty();
  };
  const span = element("span");
  span.append(element("strong", text));
  if (detail) span.append(element("small", detail));
  label.append(input, span);
  return label;
}
function fieldHelp(input, text) {
  const help = element("small", text, "field-help");
  help.id = input.id + "-help";
  input.setAttribute("aria-describedby", help.id);
  input.insertAdjacentElement("afterend", help);
}
function localModelLabel(
  id,
  info = visibleProviders().find((service) => service.id === "local"),
) {
  const runtime = info?.runtimes?.find((item) => item.id === id);
  return runtime?.model_file?.split(/[\\/]/).pop() || runtime?.name || id;
}
function pendingAuthentication(id) {
  return (
    ["codex", "claude", "gemini", "deepseek"].includes(id) &&
    state.authentication[id] === false
  );
}
function providerIcon(id) {
  if (id === "deepseek") {
    const mark = element("span", "🐋", "provider-mark");
    mark.setAttribute("aria-hidden", "true");
    return mark;
  }
  return icon(
    {
      local: "layers",
      codex: "brand-openai",
      claude: "brand-claude",
      gemini: "brand-gemini",
    }[id] || "message",
  );
}
function providerName(info) {
  return HarnessUI.providerName(info.id, info.name); // D42: the app's names
}
function providerModelName(id) {
  return (
    {
      "deepseek-flash": "DeepSeek V4.1 Flash",
      "deepseek-v4-pro": "DeepSeek V4 Pro",
    }[id] || id
  );
}
function modelIdentity(provider, model, label = model) {
  const name = String(model || "").toLowerCase(),
    span = element("span", undefined, "model-identity");
  const symbol = name.includes("qwen")
    ? "✦"
    : name.includes("gemma")
      ? "💎"
      : name.includes("llama")
        ? "🦙"
        : null;
  if (symbol) {
    const mark = element("span", symbol, "model-mark");
    mark.setAttribute("aria-hidden", "true");
    span.append(mark);
  } else
    span.append(
      providerIcon(
        name.includes("deepseek")
          ? "deepseek"
          : name.includes("gpt-")
            ? "codex"
            : /claude|sonnet|opus|haiku/.test(name)
              ? "claude"
              : name.includes("gemini")
                ? "gemini"
                : provider,
      ),
    );
  span.append(
    document.createTextNode(label || HarnessI18n.t("admin.model.not_provided")),
  );
  return span;
}

// What to do next, per provider: Codex shows a link (and, on a host without a browser, a code);
// only Claude Code asks for a code to be pasted back.
const LOGIN_STEPS = {
  codex: HarnessI18n.t("admin.login.step_codex"),
  claude: HarnessI18n.t("admin.login.step_claude"),
};
// The one-time code Codex prints for device sign-in, shown on its own so it can be read and copied.
const DEVICE_CODE = /\b[A-Z0-9]{4}-[A-Z0-9]{4,6}\b/;
const DEVICE_CODE_NOTE = HarnessI18n.t("admin.login.device_code_note");
// The providers' sign-in pages: a sign-in URL on any other host gets no Copy or Open button.
const AUTH_HOSTS = new Set([
  "auth.openai.com",
  "claude.com",
  "platform.claude.com",
  "claude.ai",
  "console.anthropic.com",
]);
function loginUrlRow(raw) {
  let url;
  try {
    url = new URL(raw);
  } catch {
    return null;
  }
  if (url.protocol !== "https:" || !AUTH_HOSTS.has(url.hostname)) {
    const text = element("p", raw, "login-url-text");
    text.dataset.testid = "login-url-text";
    return text;
  }
  const row = element("div", undefined, "login-url");
  const field = element("input");
  field.readOnly = true;
  field.value = raw;
  field.dataset.testid = "login-url";
  field.setAttribute("aria-label", HarnessI18n.t("admin.login.url_label"));
  const copy = element(
    "button",
    HarnessI18n.t("admin.common.copy"),
    "button secondary",
  );
  copy.onclick = async () => {
    try {
      await navigator.clipboard.writeText(raw);
      say(HarnessI18n.t("admin.login.link_copied"));
    } catch {
      say(HarnessI18n.t("admin.login.copy_unavailable"));
    }
  };
  const open = element(
    "button",
    HarnessI18n.t("admin.common.open"),
    "button secondary",
  );
  open.onclick = () => window.open(url.href, "_blank", "noopener");
  row.append(field, copy, open);
  return row;
}
function operationTitle(job) {
  return job.kind === "provider-login" && job.provider
    ? HarnessI18n.t("admin.login.title", {
        provider: providerName({ id: job.provider, name: job.provider }),
      })
    : HarnessI18n.t("admin.operation.title", { id: job.id.slice(0, 6) });
}
function providerLoginButton(info) {
  const button = element(
    "button",
    HarnessI18n.t("admin.login.button"),
    "button secondary",
  );
  button.setAttribute(
    "aria-label",
    HarnessI18n.t("admin.login.button_aria", { provider: providerName(info) }),
  );
  button.disabled = !info.found;
  button.onclick = () =>
    action(async () => {
      await request("provider-login", { provider: info.id });
      say(LOGIN_STEPS[info.id] || LOGIN_STEPS.codex);
    });
  return button;
}
function providerCheckButton(info) {
  const button = element(
    "button",
    HarnessI18n.t("admin.account.check"),
    "button secondary",
  );
  button.setAttribute(
    "aria-label",
    HarnessI18n.t("admin.account.check_aria", { provider: providerName(info) }),
  );
  button.disabled = !info.found;
  button.onclick = () =>
    action(async () => {
      const data = await request("check", { provider: info.id });
      state.authentication[info.id] = data.authenticated;
      state.models[info.id] = data.models;
      renderProviders();
      say(
        data.authenticated
          ? HarnessI18n.t("admin.account.verified")
          : HarnessI18n.t("admin.account.renew_needed"),
      );
    });
  return button;
}

const effortLabels = {
  configured: HarnessI18n.t("admin.effort.configured"),
  low: HarnessI18n.t("admin.effort.low"),
  medium: HarnessI18n.t("admin.effort.medium"),
  high: HarnessI18n.t("admin.effort.high"),
  xhigh: HarnessI18n.t("admin.effort.xhigh"),
  max: HarnessI18n.t("admin.effort.max"),
  ultra: HarnessI18n.t("admin.effort.ultra"),
  none: HarnessI18n.t("admin.effort.none"),
};
function modelDescription(provider, model) {
  const descriptions = {
    "gpt-6-astra": HarnessI18n.t("admin.model.desc_gpt_6_astra"),
    "gpt-5.6-sol": HarnessI18n.t("admin.model.desc_gpt_5_6_sol"),
    "gpt-5.6-terra": HarnessI18n.t("admin.model.desc_gpt_5_6_terra"),
    "gpt-5.6-luna": HarnessI18n.t("admin.model.desc_gpt_5_6_luna"),
    "gpt-5.5": HarnessI18n.t("admin.model.desc_gpt_5_5"),
  };
  const efforts = state.models[provider]?.[model] || [];
  return (
    (descriptions[model] || HarnessI18n.t("admin.model.description_default")) +
    (efforts.filter((e) => e !== "configured").length
      ? " " +
        HarnessI18n.t("admin.model.efforts", {
          efforts: efforts
            .filter((e) => e !== "configured")
            .map((e) => effortLabels[e] || e)
            .join(", "),
        })
      : "")
  );
}
async function refreshInventory() {
  state.inventory = await request("scan", {});
  renderProviders();
  renderProfile();
}
function remoteServersPanel(info) {
  const panel = element("section", undefined, "remote-servers");
  panel.setAttribute("aria-label", HarnessI18n.t("admin.remote.title"));
  const urlLabel = element(
      "label",
      HarnessI18n.t("admin.remote.address_label"),
    ),
    keyLabel = element("label", HarnessI18n.t("admin.remote.key_label")),
    url = element("input"),
    key = element("input");
  url.type = "url";
  url.autocomplete = "off";
  url.placeholder = "http://machine.tailnet.ts.net:8080";
  key.type = "password";
  key.autocomplete = "off";
  urlLabel.append(url);
  keyLabel.append(key);
  const add = element(
    "button",
    HarnessI18n.t("admin.remote.add"),
    "button secondary",
  );
  add.onclick = () =>
    action(async () => {
      if (!url.value.trim())
        throw Error(HarnessI18n.t("admin.remote.address_required"));
      const result = await request("remote-model-add", {
        url: url.value.trim(),
        key: key.value.trim(),
      });
      key.value = "";
      await refreshInventory();
      say(HarnessI18n.t("admin.remote.added", { count: result.models.length }));
    });
  const saved = element("div", undefined, "remote-server-list");
  for (const server of info.remote_servers || []) {
    const row = element("p", undefined, "remote-server-row"),
      name = element(
        "span",
        server.has_key
          ? HarnessI18n.t("admin.remote.url_key_saved", { url: server.url })
          : server.url,
      ),
      status = element(
        "span",
        server.reachable
          ? HarnessI18n.t("admin.remote.model_count", {
              count: server.models.length,
            })
          : HarnessI18n.t("admin.remote.unreachable"),
        "connector-status connector-status-" +
          (server.reachable ? "connected" : "failed"),
      ),
      remove = element(
        "button",
        HarnessI18n.t("admin.common.remove"),
        "button secondary",
      );
    if (server.error) name.append(element("small", server.error));
    status.setAttribute("role", "status");
    remove.setAttribute(
      "aria-label",
      HarnessI18n.t("admin.remote.remove_aria", { url: server.url }),
    );
    remove.onclick = () =>
      action(async () => {
        if (
          !confirm(
            HarnessI18n.t("admin.remote.remove_confirm", { url: server.url }),
          )
        )
          return;
        await request("remote-model-remove", { url: server.url });
        await refreshInventory();
        say(HarnessI18n.t("admin.remote.removed"));
      });
    row.append(name, status, remove);
    saved.append(row);
  }
  panel.append(
    element("h4", HarnessI18n.t("admin.remote.title")),
    element("p", HarnessI18n.t("admin.remote.hint"), "hint"),
    urlLabel,
    keyLabel,
    add,
    saved,
  );
  return panel;
}
function providerCard(info) {
  const id = info.id,
    spec = settings.services[id],
    card = element("article", undefined, "provider-card");
  card.dataset.provider = id;
  const top = element("div", undefined, "provider-top");
  const logo = element("span", undefined, "provider-logo");
  logo.append(providerIcon(id));
  top.append(logo);
  const title = element("div", undefined, "provider-title");
  title.append(
    element("h3", providerName(info)),
    element(
      "small",
      id === "local"
        ? HarnessI18n.t("admin.provider.local_tagline")
        : id === "deepseek"
          ? HarnessI18n.t("admin.provider.deepseek_tagline")
          : HarnessI18n.t("admin.provider.cli_tagline"),
    ),
  );
  top.append(
    title,
    element(
      "span",
      info.api
        ? HarnessI18n.t("admin.provider.api_key")
        : info.found
          ? HarnessI18n.t("admin.provider.found")
          : HarnessI18n.t("admin.provider.not_found"),
      "pill" + (info.found ? " good" : ""),
    ),
  );
  card.append(top);
  const body = element("div", undefined, "provider-body"),
    meta = element("div", undefined, "provider-meta");
  meta.append(
    element(
      "span",
      state.authentication[id]
        ? id === "local"
          ? HarnessI18n.t("admin.provider.server_available")
          : HarnessI18n.t("admin.provider.authenticated")
        : info.credential_present
          ? HarnessI18n.t("admin.provider.credential_found")
          : id === "local"
            ? HarnessI18n.t("admin.provider.server_not_verified")
            : HarnessI18n.t("admin.provider.login_not_verified"),
    ),
  );
  const check = element(
    "button",
    id === "local"
      ? HarnessI18n.t("admin.provider.check_models")
      : HarnessI18n.t("admin.account.check"),
    "button secondary",
  );
  check.disabled = !info.found;
  check.hidden = id === "deepseek" && !state.credentials?.deepseek;
  check.onclick = () =>
    action(async () => {
      const data = await request("check", { provider: id });
      state.authentication[id] = data.authenticated;
      state.models[id] = data.models;
      renderProviders();
      renderProfile();
      say(
        data.authenticated
          ? id === "local"
            ? HarnessI18n.t("admin.provider.server_verified")
            : HarnessI18n.t("admin.provider.account_verified")
          : id === "local"
            ? HarnessI18n.t("admin.provider.local_not_responding")
            : HarnessI18n.t("admin.provider.login_first"),
      );
    });
  if (
    id === "local" ||
    (!state.settings.services[id]?.added &&
      !state.settings.services[id]?.enabled)
  ) {
    meta.append(check);
    if (["codex", "claude", "gemini"].includes(id))
      meta.insertBefore(providerLoginButton(info), check);
  }
  body.append(meta);
  if (id === "local" || id === "deepseek") {
    const inference =
      id === "local"
        ? HarnessI18n.t("admin.provider.local_inference")
        : HarnessI18n.t("admin.provider.deepseek_inference");
    body.append(
      element(
        "p",
        HarnessI18n.t("admin.provider.inference_note", { inference }),
        "hint",
      ),
    );
  }
  if (id === "local" && (info.runtimes || []).length) {
    const servers = element("div", undefined, "detected-servers");
    servers.append(
      element("strong", HarnessI18n.t("admin.provider.servers_found")),
    );
    for (const runtime of info.runtimes) {
      const row = element(
        "p",
        runtime.runtime +
          " · " +
          runtime.url +
          " · " +
          localModelLabel(runtime.id, info),
      );
      servers.append(row);
    }
    body.append(servers);
  }
  if (id === "local") body.append(remoteServersPanel(info));
  if (id === "deepseek") {
    const tokenLabel = element(
        "label",
        HarnessI18n.t("admin.provider.deepseek_key_label"),
      ),
      token = element("input");
    token.type = "password";
    token.autocomplete = "off";
    token.placeholder = state.credentials?.deepseek
      ? HarnessI18n.t("admin.provider.deepseek_key_saved_placeholder")
      : HarnessI18n.t("admin.provider.deepseek_key_placeholder");
    tokenLabel.append(token);
    const saveToken = element(
      "button",
      HarnessI18n.t("admin.provider.deepseek_save"),
      "button primary",
    );
    saveToken.onclick = () =>
      action(async () => {
        if (!token.value.trim() && !state.credentials?.deepseek)
          throw Error(HarnessI18n.t("admin.provider.deepseek_key_required"));
        if (token.value.trim()) {
          await request("provider-token", {
            provider: id,
            token: token.value.trim(),
          });
          state.credentials = { ...state.credentials, deepseek: true };
          token.value = "";
        }
        const result = await request("check", { provider: id });
        state.models[id] = result.models || {};
        state.authentication[id] = result.authenticated === true;
        if (!result.authenticated)
          throw Error(HarnessI18n.t("admin.provider.deepseek_not_confirmed"));
        state.credentials = { ...state.credentials, deepseek: true };
        renderProviders();
        say(
          HarnessI18n.t("admin.provider.deepseek_verified", {
            balance: (result.balance?.balance_infos || [])
              .map((b) => b.currency + " " + b.total_balance)
              .join(" · "),
          }),
        );
      });
    body.append(
      tokenLabel,
      saveToken,
      element(
        "p",
        HarnessI18n.t("admin.provider.deepseek_saving_hint"),
        "hint",
      ),
    );
  }

  const label = element(
    "p",
    HarnessI18n.t("admin.provider.models_label"),
    "field-label",
  );
  body.append(
    label,
    element("p", HarnessI18n.t("admin.provider.models_hint"), "hint"),
  );
  const choices = element("div", undefined, "model-list");
  const models =
    id === "local"
      ? info.models || Object.keys(state.models[id] || {})
      : Object.keys(state.models[id] || {});
  for (const m of new Set(
    [...models, ...spec.models].filter((m) => HarnessUI.selectableModel(id, m)),
  ))
    choices.append(
      toggle(
        id === "local" ? localModelLabel(m, info) : providerModelName(m),
        spec.models.includes(m),
        (yes) => {
          spec.models = yes
            ? [...spec.models, m]
            : spec.models.filter((x) => x !== m);
          if (id === "local") renderProfile();
        },
        id === "local"
          ? localModelLabel(m, info) !== m
            ? HarnessI18n.t("admin.model.server_identifier", { id: m })
            : HarnessI18n.t("admin.model.name_not_provided")
          : state.authentication[id] === true && !models.includes(m)
            ? HarnessI18n.t("admin.model.unavailable")
            : modelDescription(id, m),
      ),
    );
  if (!choices.children.length)
    choices.append(
      element(
        "p",
        id === "local"
          ? HarnessI18n.t("admin.provider.no_models_local")
          : !info.found
            ? HarnessI18n.t("admin.provider.install_cli")
            : HarnessI18n.t("admin.provider.check_to_list"),
        "hint",
      ),
    );
  for (const row of choices.querySelectorAll(".toggle-row strong"))
    row.prepend(providerIcon(id));
  body.append(choices);
  if (id === "gemini")
    body.append(
      element("p", HarnessI18n.t("admin.provider.gemini_ended"), "hint"),
    );
  if (id === "claude")
    body.append(
      element("p", HarnessI18n.t("admin.provider.claude_ids"), "hint"),
    );
  if (id !== "local")
    body.append(
      toggle(
        HarnessI18n.t("admin.provider.preferred"),
        settings.default_backend === id,
        (yes) => {
          settings.default_backend = yes ? id : "";
          renderProviders();
        },
        HarnessI18n.t("admin.provider.preferred_hint"),
      ),
    );
  const advanced = element("details", undefined, "advanced");
  advanced.append(
    element("summary", HarnessI18n.t("admin.provider.access")),
    element("p", HarnessI18n.t("admin.provider.access_hint"), "hint"),
  );
  body.append(advanced);

  card.append(body);
  const bottom = element("div", undefined, "provider-enabled");
  bottom.append(
    toggle(
      HarnessI18n.t("admin.provider.make_available"),
      spec.enabled,
      (yes) => (spec.enabled = yes),
      HarnessI18n.t("admin.provider.make_available_hint"),
    ),
  );
  if (!state.settings.services[id]?.added) card.append(bottom);
  return card;
}
function renderProviders() {
  $("provider-options").replaceChildren(
    ...visibleProviders()
      .filter(
        (info) =>
          !state.settings.services[info.id]?.added &&
          !state.settings.services[info.id]?.enabled &&
          !state.settings.services[info.id]?.models?.length,
      )
      .map((info) => {
        const button = element(
          "button",
          undefined,
          "button secondary discovery-option",
        );
        button.dataset.provider = info.id;
        const status = info.api
          ? HarnessI18n.t("admin.provider.api_byok")
          : info.found
            ? HarnessI18n.t("admin.provider.found")
            : HarnessI18n.t("admin.provider.not_installed");
        button.append(providerIcon(info.id));
        const copy = element("span");
        copy.append(
          element("strong", providerName(info)),
          element(
            "small",
            status,
            "pill" + (info.found && !info.api ? " good" : ""),
          ),
        );
        button.append(copy);
        button.onclick = () => {
          editing = info.id;
          renderProviders();
          showStep(0);
          $("wizard-back").focus();
        };
        return button;
      }),
  );
  if (!$("provider-options").children.length)
    $("provider-options").append(
      element("p", HarnessI18n.t("admin.provider.all_added"), "hint"),
    );
  $("provider-options").hidden = !!editing;
  $("provider-discovery-heading").hidden = !!editing;
  const info = visibleProviders().find((x) => x.id === editing);
  $("provider-cards").replaceChildren(...(info ? [providerCard(info)] : []));
  $("permission-editor").replaceChildren();
  if (info) {
    const advanced = $("provider-cards").querySelector(".advanced");
    advanced.open = true;
    if (info.id === "local") {
      advanced.hidden = true;
      $("permission-editor").append(
        element("p", HarnessI18n.t("admin.provider.permissions_hint"), "hint"),
      );
    }
  }
  renderDashboard();
  HarnessUI.decorate($("provider-dialog"));
}
function integrationEngine(provider = editing) {
  return {
    codex: "codex",
    claude: "claude",
    local: "codex",
    deepseek: "codex",
  }[provider];
}
function integrationKind() {
  return step === 4 ? "plugin" : "mcp";
}
function integrationDescription(item) {
  const metadata = integrationCatalogs
    .get(integrationEngine())
    ?.items?.find((entry) => entry.id === item.id);
  return (
    item.description ||
    metadata?.description ||
    HarnessI18n.t("admin.integration.description_missing")
  );
}
const connectorIdentity = {
  node_repl: [HarnessI18n.t("admin.connector.javascript_terminal"), "⌨️"],
  "qwen-local-agent": [HarnessI18n.t("admin.connector.local_qwen_agent"), "🧠"],
  "codex-app-tools": [HarnessI18n.t("admin.connector.codex_tools"), "codex"],
  sites: [HarnessI18n.t("admin.connector.sites"), "🌐"],
  browser: [HarnessI18n.t("admin.connector.browser"), "🧭"],
  chrome: ["Chrome", "🌐"],
  "unified-computer-use": [
    HarnessI18n.t("admin.connector.computer_control"),
    "🖥️",
  ],
  visualize: [HarnessI18n.t("admin.connector.visualizations"), "📈"],
  documents: [HarnessI18n.t("admin.connector.documents"), "📝"],
  pdf: ["PDF", "📄"],
  spreadsheets: [HarnessI18n.t("admin.connector.spreadsheets"), "📊"],
  presentations: [HarnessI18n.t("admin.connector.presentations"), "📽️"],
  "template-creator": [HarnessI18n.t("admin.connector.template_creator"), "📐"],
  drive: ["Google Drive", "📁"],
  github: ["GitHub", "📦"],
  linear: ["Linear", "☑️"],
};
function connectorKey(item) {
  return String(item.name || item.id || "")
    .replace(/^(?:mcp|plugin):/, "")
    .split("@")[0];
}
function connectorLabel(item) {
  if (item.display_name || item.title) return item.display_name || item.title;
  const key = connectorKey(item);
  return (
    connectorIdentity[key]?.[0] ||
    key.replace(/[-_]+/g, " ").replace(/\b\w/g, (char) => char.toUpperCase()) ||
    HarnessI18n.t("admin.connector.fallback")
  );
}
function connectorIcon(item) {
  const mark = element("span", undefined, "connector-icon");
  mark.setAttribute("aria-hidden", "true");
  const symbol =
    connectorIdentity[connectorKey(item)]?.[1] ||
    (item.kind === "plugin" ? "🧩" : "🔌");
  if (symbol === "codex") mark.append(providerIcon("codex"));
  else mark.textContent = symbol;
  return mark;
}
// Changes made to a provider outside KeepHarness (issue #43, D-041). One store feeds the notices block
// of the Plugins page and the one of Settings > Providers; customize.js hands over what its reads return.
const NOTICE_SCOPE = "sem-projeto";
const providerNotices = new Map(); // provider id -> the Notice list of its last provider-state read
const toasted = new Set(); // notice ids already announced in this page session (memory only)
const onOff = (enabled) =>
  enabled
    ? HarnessI18n.t("admin.common.on")
    : HarnessI18n.t("admin.common.off");
const acked = new Set(); // dismissed notice ids; they embed detected_at, so one never legitimately returns
function setProviderNotices(provider, list) {
  providerNotices.set(
    provider,
    Array.isArray(list) ? list.filter((notice) => !acked.has(notice.id)) : [],
  );
}
// The sentence as [text, item id, text]: the id is shown in <code>, the rest is plain text.
function noticeParts(provider, notice) {
  const cli = HarnessUI.providerName(provider);
  const time = new Date(notice.detected_at).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  });
  const [kind, id] = String(notice.item_id).includes(":")
    ? String(notice.item_id).split(/:(.*)/s)
    : ["item", String(notice.item_id)];
  // Older pending skill notices stored the folder name instead of its source file.
  const source =
    kind === "skill" && notice.source === id ? "SKILL.md" : notice.source;
  const detail = " " + HarnessI18n.t("admin.notice.detail", { source, time });
  if (notice.change === "reverted")
    return [
      HarnessI18n.t("admin.notice.reverted_lead", { cli, kind }),
      id,
      HarnessI18n.t("admin.notice.reverted_tail", {
        state: onOff(notice.after),
        detail,
        previous: onOff(notice.before),
      }),
    ];
  const what =
    {
      added: HarnessI18n.t("admin.notice.added"),
      removed: HarnessI18n.t("admin.notice.removed"),
    }[notice.change] ||
    HarnessI18n.t("admin.notice.turned", { state: onOff(notice.after) });
  return [
    HarnessI18n.t("admin.notice.changed_lead", { cli, kind }),
    id,
    HarnessI18n.t("admin.notice.changed_tail", { what, detail }),
  ];
}
const noticeSentence = (provider, notice) =>
  noticeParts(provider, notice).join("");
const noticeBlocks = () => ["provider-state-notices", "plugins-notices"].map($);
// Redraws both blocks from the store, toasts the ids not announced yet (one toast per batch) and
// tells the Plugins list to redraw its row markers.
function showProviderNotices() {
  const all = [...providerNotices].flatMap(([provider, list]) =>
    list.map((notice) => ({ provider, notice })),
  );
  for (const box of noticeBlocks()) {
    box.hidden = !all.length;
    if (!all.length) {
      box.replaceChildren();
      continue;
    }
    const head = element("div", undefined, "provider-notices-head");
    head.append(
      element(
        "strong",
        HarnessI18n.t("admin.notices.outside_title", { count: all.length }),
      ),
    );
    for (const [provider, list] of providerNotices) {
      if (list.length < 2) continue;
      const dismissAll = element(
        "button",
        HarnessI18n.t("admin.notices.dismiss_all", {
          provider: HarnessUI.providerName(provider),
        }),
        "button secondary",
      );
      dismissAll.dataset.testid = "provider-notice-dismiss-all";
      dismissAll.onclick = () => ackProviderNotices(provider, list);
      head.append(dismissAll);
    }
    const items = element("ul", undefined, "provider-notices-list");
    for (const { provider, notice } of all) {
      const [lead, id, tail] = noticeParts(provider, notice);
      const dismiss = element(
        "button",
        HarnessI18n.t("admin.common.dismiss"),
        "button secondary",
      );
      dismiss.setAttribute(
        "aria-label",
        HarnessI18n.t("admin.notices.dismiss_aria", { text: lead + id + tail }),
      );
      dismiss.onclick = () => ackProviderNotices(provider, [notice]);
      const line = element("span");
      line.append(lead, element("code", id), tail);
      const li = element("li", undefined, "provider-notice");
      li.dataset.testid = "provider-notice";
      li.dataset.noticeId = notice.id;
      li.dataset.provider = provider;
      li.append(line, dismiss);
      items.append(li);
    }
    if (all.length === 1) {
      box.replaceChildren(head, items);
      continue;
    }
    // Several changes fold into one line so up to 100 of them never push the page down.
    const folded = element("details");
    folded.open = box.querySelector("details")?.open || false;
    folded.append(
      element("summary", HarnessI18n.t("admin.notices.show_each")),
      items,
    );
    box.replaceChildren(head, folded);
  }
  // Only the Plugins and Providers screens toast; an id announced elsewhere would be lost, so it waits.
  const fresh = ["#plugins", "#providers"].includes(location.hash)
    ? all.filter(({ notice }) => !toasted.has(notice.id))
    : [];
  fresh.forEach(({ notice }) => toasted.add(notice.id));
  if (fresh.length) {
    const [{ provider, notice }] = fresh;
    const text = noticeSentence(provider, notice);
    HarnessUI.toast(
      fresh.length > 1
        ? HarnessI18n.t("admin.notices.toast_many", { count: fresh.length })
        : notice.change === "reverted"
          ? text
          : HarnessI18n.t("admin.notices.toast_one", { text }),
    );
  }
  document.dispatchEvent(new Event("provider-notices"));
}
// The server accepts at most 100 ids per call; the rest stay listed and can be dismissed again.
function ackProviderNotices(provider, notices) {
  const ids = notices.slice(0, 100).map((notice) => notice.id);
  return action(async () => {
    await request("provider-state/notices:ack", {
      provider,
      project_id: NOTICE_SCOPE,
      notice_ids: ids,
    });
    ids.forEach((id) => acked.add(id));
    setProviderNotices(provider, providerNotices.get(provider));
    showProviderNotices();
    await refreshProviderNotices();
    // The Dismiss button is gone: keep the focus on the screen instead of <body>.
    const screen = onProviders()
      ? document.querySelector("#overview h1")
      : $("plugins-panel");
    const next = noticeBlocks()
      .find((box) => !box.hidden && box.offsetParent)
      ?.querySelector("button");
    if (next) return next.focus();
    screen.tabIndex = -1;
    screen.addEventListener("blur", () => screen.removeAttribute("tabindex"), {
      once: true,
    });
    screen.focus();
  });
}
// Focus and visibilitychange fire together: callers share the read that is already in flight.
let noticesRead = null;
const refreshProviderNotices = () =>
  (noticesRead ||= readProviderNotices().finally(() => (noticesRead = null)));
async function readProviderNotices() {
  await Promise.all(
    ["codex", "claude"].map(async (id) => {
      try {
        setProviderNotices(
          id,
          (
            await request(
              "provider-state?provider=" + id + "&project_id=" + NOTICE_SCOPE,
            )
          ).external_changes,
        );
      } catch {
        // A CLI that is missing or unreadable has nothing to announce here (its own screens say why),
        // and an old notice must not stay on show for a state that can no longer be read.
        setProviderNotices(id, []);
      }
    }),
  );
  showProviderNotices();
}
function renderDashboard() {
  const configured = visibleProviders().filter((i) => {
    const s = state.settings.services[i.id];
    return s && (s.added || s.enabled || s.models.length);
  });
  $("configured-providers").replaceChildren(
    ...configured.map((info) => {
      const s = state.settings.services[info.id],
        card = element("article", undefined, "panel configured-card");
      card.classList.toggle("selected", editing === info.id);
      card.dataset.configuredProvider = info.id;
      const heading = element("div", undefined, "configured-heading"),
        mark = element("span", undefined, "provider-logo");
      mark.append(providerIcon(info.id));
      heading.append(mark, element("h3", providerName(info)));
      card.append(
        heading,
        element(
          "span",
          pendingAuthentication(info.id)
            ? HarnessI18n.t("admin.providers.pending_authorization")
            : s.enabled
              ? HarnessI18n.t("admin.providers.enabled")
              : HarnessI18n.t("admin.providers.disabled"),
          "pill" +
            (s.enabled && !pendingAuthentication(info.id) ? " good" : ""),
        ),
        element(
          "p",
          s.models.length
            ? undefined
            : HarnessI18n.t("admin.providers.no_model_selected"),
          "configured-models",
        ),
      );
      // A provider the harness had to take offline says why (for example "Sign in required").
      const offline = Object.values(
        state.status.unavailable_models?.[info.id] || {},
      )[0];
      if (offline) card.append(element("p", offline, "hint"));
      card
        .querySelector(".configured-models")
        .append(
          ...s.models.map((m) =>
            modelIdentity(
              info.id,
              m,
              info.id === "local"
                ? localModelLabel(m, info)
                : providerModelName(m),
            ),
          ),
        );
      const actions = element("div", undefined, "card-actions");
      const edit = element(
        "button",
        HarnessI18n.t("admin.providers.edit"),
        "button secondary",
      );
      edit.setAttribute(
        "aria-label",
        HarnessI18n.t("admin.providers.edit_named", {
          name: providerName(info),
        }),
      );
      edit.dataset.editProvider = info.id;
      edit.onclick = () => openWizard(info.id);
      const remove = element(
        "button",
        HarnessI18n.t("admin.providers.delete"),
        "button secondary",
      );
      remove.onclick = () =>
        action(async () => {
          if (
            !confirm(
              HarnessI18n.t("admin.providers.remove_confirm", {
                name: providerName(info),
              }),
            )
          )
            return;
          await request("provider-delete", { provider: info.id });
          await load();
          say(HarnessI18n.t("admin.providers.removed"));
        });
      edit.prepend(icon("edit"));
      remove.prepend(icon("trash"));
      actions.append(edit, remove);
      if (["codex", "claude", "gemini"].includes(info.id))
        actions.prepend(providerLoginButton(info), providerCheckButton(info));
      card.append(actions);
      const availability = toggle(
        HarnessI18n.t("admin.providers.availability_label"),
        s.enabled,
        () => {},
        HarnessI18n.t("admin.providers.availability_hint"),
      );
      const checkbox = availability.querySelector("input");
      checkbox.setAttribute(
        "aria-label",
        HarnessI18n.t("admin.providers.availability_named", {
          name: providerName(info),
        }),
      );
      checkbox.onchange = () => {
        const enabled = checkbox.checked;
        if (working) {
          checkbox.checked = s.enabled;
          return;
        }
        action(async () => {
          checkbox.disabled = true;
          try {
            const saved = structuredClone(state.settings);
            saved.services[info.id].enabled = enabled;
            await request("settings", saved);
            await load({ select: false });
            say(
              enabled
                ? HarnessI18n.t("admin.providers.service_enabled")
                : HarnessI18n.t("admin.providers.service_disabled"),
            );
          } finally {
            checkbox.checked = s.enabled;
            checkbox.disabled = false;
          }
        });
      };
      card.append(availability);
      return card;
    }),
  );
  if (!configured.length)
    $("configured-providers").append(
      element(
        "p",
        HarnessI18n.t("admin.providers.workspace_ready"),
        "empty-state",
      ),
    );
}
function openWizard(provider = null) {
  if (
    (unsaved || profileDirty) &&
    !confirm(HarnessI18n.t("admin.providers.discard_open_confirm"))
  )
    return;
  settings = structuredClone(state.settings);
  unsaved = false;
  profileDirty = false;
  $("feedback").hidden = true;
  wizard = true;
  editing = provider;
  step = 0;
  renderProviders();
  renderProjects();
  showStep(0);
  $("wizard-title").textContent = provider
    ? HarnessI18n.t("admin.providers.edit_named", {
        name: providerName(visibleProviders().find((i) => i.id === provider)),
      })
    : HarnessI18n.t("admin.providers.add_title");
  $("provider-dialog").hidden = false;
  renderProfile();
  $("wizard-feedback").append($("feedback"));
  HarnessUI.decorate();
  if (!provider) $("provider-options").querySelector("button")?.focus();
}
function dashboard() {
  if (
    (unsaved || profileDirty) &&
    !confirm(HarnessI18n.t("admin.providers.discard_confirm"))
  )
    return;
  profileDirty = false;
  wizard = false;
  editing = null;
  showStep(0);
  renderDashboard();
}

function renderProjects() {
  $("project-list").replaceChildren(
    element("p", HarnessI18n.t("admin.projects.add_hint"), "hint"),
  );
  $("project-count").textContent = settings.projects.length;
}

let tailnetNoticeShown = false;
function renderStatus() {
  const s = state.status;
  const diagnostics = [];
  if (s.startup_error)
    diagnostics.push(
      HarnessI18n.t("admin.status.startup_error", { error: s.startup_error }),
    );
  if (s.last_exit)
    diagnostics.push(
      HarnessI18n.t("admin.status.last_exit", {
        code: s.last_exit.code,
        time: new Date(s.last_exit.at * 1000).toISOString(),
        uptime: s.last_exit.uptime_seconds,
      }),
    );
  $("runtime-diagnostics").textContent = diagnostics.join("\n");
  $("runtime-diagnostics").hidden = diagnostics.length === 0;
  $("environment-tools").replaceChildren(
    ...(state.inventory.tools || []).map((tool) => {
      const row = element("div");
      row.append(
        element(
          "h3",
          tool.present
            ? HarnessI18n.t("admin.status.tool_available", { name: tool.name })
            : HarnessI18n.t("admin.status.tool_missing", { name: tool.name }),
        ),
      );
      for (const [distro, hint] of Object.entries(tool.package_hints || {}))
        row.append(element("p", distro + ": " + hint, "hint"));
      return row;
    }),
  );
  for (const provider of state.inventory.services)
    for (const failure of provider.runtime_errors || [])
      $("environment-tools").append(
        element("p", failure.url + ": " + failure.error, "hint"),
      );
  $("add-provider").disabled = working;
  $("wizard-content-lock").disabled = working;
  $("wizard-next").disabled = working;
  // Persistent, so it is said once per page load and never buries a later message.
  if (s.tailnet_signin_off && !tailnetNoticeShown) {
    tailnetNoticeShown = true;
    say(s.tailnet_signin_off, true);
  }
  $("runtime-badge").textContent = s.running
    ? HarnessI18n.t("admin.status.harness_active")
    : HarnessI18n.t("admin.status.harness_stopped");
  $("runtime-badge").classList.toggle("good", s.running);
  // The link carries the real address as soon as the status gives it, and works only while the harness runs.
  const open = $("open-harness");
  open.href = (s.shared ? s.remote_url : s.local_url) || "#";
  open.setAttribute("aria-disabled", String(!s.running));
  open.title = s.running
    ? HarnessI18n.t("admin.status.open_harness_title")
    : HarnessI18n.t("admin.status.harness_stopped_hint");
  $("save").disabled = working;
}
$("open-harness").onclick = (event) => {
  if (event.currentTarget.getAttribute("aria-disabled") === "true")
    event.preventDefault();
};
function render() {
  renderProviders();
  renderProjects();
  renderStatus();
  renderProfile();
  renderMcpDefaults();
  showStep(step);
  $("found-count").textContent = visibleProviders().filter(
    (x) => x.found,
  ).length;
  $("uploads").checked = settings.uploads_enabled;
  $("full-access").checked = settings.full_access === true;
  $("uploads").closest(".panel-bottom").hidden = true;
  $("platform-note").textContent = HarnessI18n.t("admin.status.platform_note", {
    platform: state.inventory.platform,
  });
}
async function load({ select = true } = {}) {
  settingsSearchChanged("refreshing");
  try {
    state = await request("state");
    if (state.authentication.claude === false) {
      try {
        const checked = await request("check", { provider: "claude" });
        state.authentication.claude = checked.authenticated;
        state.models.claude = checked.models;
      } catch {
        state.authentication.claude = null;
      }
    }
    settings = structuredClone(state.settings);
    unsaved = false;
    if (select) {
      const available = visibleProviders().filter((i) => {
        const s = settings.services[i.id];
        return s && (s.added || s.enabled || s.models.length);
      });
      editing = available.some((i) => i.id === editing) ? editing : null;
      wizard = !!editing;
    }
    render();
    if (wizard)
      $("wizard-title").textContent = HarnessI18n.t(
        "admin.providers.edit_named",
        {
          name:
            visibleProviders().find((i) => i.id === editing)?.name ||
            HarnessI18n.t("admin.providers.generic_name"),
        },
      );
    HarnessUI.decorate();
    settingsSearchChanged("ready");
  } catch (error) {
    settingsSearchChanged("error", error);
    throw error;
  }
}
$("refresh-log-tail").onclick = async () => {
  const button = $("refresh-log-tail");
  button.disabled = true;
  try {
    const result = await request("logs");
    $("log-tail").textContent =
      result.lines.join("\n") || HarnessI18n.t("admin.logs.empty");
  } catch (error) {
    $("log-tail").textContent = HarnessI18n.t("admin.logs.read_failed", {
      message: error.message,
    });
  } finally {
    button.disabled = false;
  }
};
async function action(fn) {
  if (working) return;
  working = true;
  const trigger = document.activeElement?.closest("button");
  if (trigger && !$("operation-dialog").open) operationOpener = trigger;
  const disabled = trigger?.disabled;
  const label =
    trigger?.textContent?.trim() ||
    trigger?.getAttribute("aria-label") ||
    HarnessI18n.t("admin.busy.default_label");
  if (trigger) trigger.disabled = true;
  $("wizard-content-lock").disabled = true;
  $("config-mcp").inert = true;
  if (state) renderStatus();
  document.body.setAttribute("aria-busy", "true");
  const busy = $("busy-status");
  busy.hidden = false;
  busy.replaceChildren(
    icon("refresh"),
    document.createTextNode(HarnessI18n.t("admin.busy.processing", { label })),
  );
  if (trigger) {
    trigger.setAttribute("aria-busy", "true");
    trigger.classList.add("is-processing");
  }
  try {
    await fn();
  } catch (e) {
    say(e.message, true);
  } finally {
    working = false;
    $("wizard-content-lock").disabled = false;
    $("config-mcp").inert = false;
    busy.hidden = true;
    document.body.removeAttribute("aria-busy");
    if (trigger?.isConnected) {
      trigger.disabled = disabled;
      trigger.removeAttribute("aria-busy");
      trigger.classList.remove("is-processing");
      if (
        trigger === operationOpener &&
        !$("operation-dialog").open &&
        document.activeElement === document.body
      )
        trigger.focus();
    }
    if (state) renderStatus();
    if (wizard && !editing && $("provider-dialog").open)
      $("provider-options").querySelector("button")?.focus();
  }
}

function collect() {
  settings.uploads_enabled = $("uploads").checked;
  // spec.models can still hold ids that HarnessUI.selectableModel() hides from
  // the toggle list (e.g. unversioned Claude aliases); never send those back.
  for (const [id, spec] of Object.entries(settings.services))
    if (Array.isArray(spec.models))
      spec.models = spec.models.filter((m) => HarnessUI.selectableModel(id, m));
  return settings;
}
$("scan").onclick = () =>
  action(async () => {
    state.inventory = await request("scan", {});
    renderProviders();
    renderProjects();
    renderStatus();
    renderProfile();
    say(HarnessI18n.t("admin.scan.complete"));
  });
$("save").onclick = () =>
  action(async () => {
    if (profileDirty) throw Error(HarnessI18n.t("admin.save.profile_pending"));
    if (!editing) throw Error(HarnessI18n.t("admin.save.choose_provider"));
    const spec = settings.services[editing],
      catalog = Object.keys(state.models[editing] || {});
    if (spec.enabled && pendingAuthentication(editing))
      throw Error(HarnessI18n.t("admin.save.pending_authorization"));
    if (
      spec.enabled &&
      editing !== "local" &&
      state.authentication[editing] === true &&
      spec.models.some((model) => !catalog.includes(model))
    )
      throw Error(HarnessI18n.t("admin.save.models_unavailable"));
    settings.services[editing].added = true;
    try {
      await request("settings", collect());
    } catch (e) {
      HarnessUI.toast(
        HarnessI18n.t("admin.save.failed", { message: e.message }),
        {
          error: true,
        },
      );
      throw e;
    }
    unsaved = false;
    $("dirty").textContent = HarnessI18n.t("admin.save.saved");
    HarnessUI.toast(
      state.status.running
        ? HarnessI18n.t("admin.save.saved_running")
        : HarnessI18n.t("admin.save.saved_starting"),
    );
    try {
      const savedProvider = editing;
      await load();
      wizard = false;
      editing = null;
      showStep(0);
      renderDashboard();
      $("dirty").textContent = HarnessI18n.t("admin.save.saved");
      document
        .querySelector(
          '[data-configured-provider="' +
            savedProvider +
            '"] button[data-edit-provider]',
        )
        ?.focus();
    } catch (e) {
      throw Error(HarnessI18n.t("admin.save.refresh_failed"));
    }
  });
// D11: Full access stays out of the chat's access menu until the owner turns it on here.
$("full-access").onchange = () => {
  const toggle = $("full-access"),
    enabled = toggle.checked;
  if (working) {
    toggle.checked = !enabled;
    return;
  }
  action(async () => {
    toggle.disabled = true;
    settingsSearchChanged("changed");
    try {
      await request("settings", {
        ...structuredClone(state.settings),
        full_access: enabled,
      });
      await load({ select: false });
      say(
        enabled
          ? HarnessI18n.t("admin.access.full_access_on")
          : HarnessI18n.t("admin.access.full_access_off"),
      );
    } finally {
      toggle.checked = state.settings.full_access === true;
      toggle.disabled = false;
      settingsSearchChanged("changed");
    }
  });
};
$("uploads").onchange = () => {
  settings.uploads_enabled = $("uploads").checked;
  if (!settings.uploads_enabled)
    for (const s of Object.values(settings.services))
      s.permissions.upload = false;
  renderProviders();
  dirty();
};
let folderPickerTarget = null,
  folderPickerCurrent = null,
  folderPickerSequence = 0;
async function browseFolders(path = "") {
  const sequence = ++folderPickerSequence;
  folderPickerCurrent = null;
  $("folder-picker-use").disabled = true;
  $("folder-picker-parent").disabled = true;
  $("folder-picker-error").hidden = true;
  $("folder-picker-list").replaceChildren();
  $("folder-picker-breadcrumb").replaceChildren();
  $("folder-picker-status").textContent = HarnessI18n.t(
    "admin.folders.loading",
  );
  $("folder-picker-list").setAttribute("aria-busy", "true");
  try {
    const data = await request(
      "folders" + (path ? "?path=" + encodeURIComponent(path) : ""),
    );
    if (sequence !== folderPickerSequence) return;
    folderPickerCurrent = data;
    $("folder-picker-use").disabled = false;
    $("folder-picker-parent").disabled = !data.parent;
    const crumb = $("folder-picker-breadcrumb");
    let accumulated = "";
    for (const [index, name] of [
      "/",
      ...data.path.split("/").filter(Boolean),
    ].entries()) {
      accumulated = index ? accumulated + "/" + name : "";
      const destination = accumulated || "/";
      const button = element("button", name, "button secondary");
      button.type = "button";
      button.onclick = () => browseFolders(destination);
      crumb.append(button);
    }
    for (const folder of data.directories) {
      const button = element(
        "button",
        undefined,
        "button secondary folder-picker-entry",
      );
      button.type = "button";
      button.append(icon("stack-2"), document.createTextNode(folder.name));
      button.title = folder.path;
      button.onclick = () => browseFolders(folder.path);
      $("folder-picker-list").append(button);
    }
    $("folder-picker-status").textContent = data.truncated
      ? HarnessI18n.t("admin.folders.truncated")
      : HarnessI18n.t(
          data.directories.length ? "admin.folders.some" : "admin.folders.none",
          { count: data.directories.length },
        );
  } catch (e) {
    if (sequence !== folderPickerSequence) return;
    $("folder-picker-status").textContent = HarnessI18n.t(
      "admin.folders.list_failed",
    );
    $("folder-picker-error").textContent = e.message;
    $("folder-picker-error").hidden = false;
  } finally {
    if (sequence === folderPickerSequence)
      $("folder-picker-list").removeAttribute("aria-busy");
  }
}
function chooseFolder(target) {
  folderPickerTarget = target;
  $("folder-picker").showModal();
  browseFolders(typeof target === "function" ? "" : $(target).value.trim());
}
$("choose-model-folder").onclick = () => chooseFolder("model-folder");
$("folder-picker-create").onclick = async () => {
  if (!folderPickerCurrent) return;
  const button = $("folder-picker-create"),
    sequence = folderPickerSequence;
  button.disabled = true;
  $("folder-picker-error").hidden = true;
  try {
    const result = await request("folders/create", {
      parent: folderPickerCurrent.path,
      name: $("folder-picker-new-name").value.trim(),
    });
    if (sequence !== folderPickerSequence) return;
    $("folder-picker-new-name").value = "";
    await browseFolders(result.path);
  } catch (e) {
    if (sequence !== folderPickerSequence) return;
    $("folder-picker-error").textContent = e.message;
    $("folder-picker-error").hidden = false;
  } finally {
    button.disabled = false;
  }
};
$("folder-picker-home").onclick = () => browseFolders();
$("folder-picker-parent").onclick = () => {
  if (folderPickerCurrent?.parent) browseFolders(folderPickerCurrent.parent);
};
$("folder-picker-close").onclick = () => $("folder-picker").close();
$("folder-picker").addEventListener("close", () => {
  folderPickerSequence++;
});
$("folder-picker-use").onclick = () => {
  if (!folderPickerCurrent || !folderPickerTarget) return;
  if (typeof folderPickerTarget === "function") {
    folderPickerTarget(folderPickerCurrent.path);
    $("folder-picker").close();
    return;
  }
  $(folderPickerTarget).value = folderPickerCurrent.path;
  $("folder-picker").close();
  $(folderPickerTarget).focus();
};
$("theme").onclick = () => $("appearance-dialog").showModal();
$("appearance-close").onclick = () => $("appearance-dialog").close();
$("integration-run").onclick = () =>
  action(async () => {
    const transport = $("integration-transport").value,
      source = $("integration-source").value.trim();
    const data = {
      provider: $("integration-provider").value,
      action: $("integration-action").value,
      name: $("integration-name").value.trim(),
      transport,
      url: source,
    };
    if (transport === "stdio" && data.action === "connector_add") {
      try {
        data.command = JSON.parse(source);
      } catch {
        throw Error(HarnessI18n.t("admin.integrations.command_json_example"));
      }
      if (
        !Array.isArray(data.command) ||
        !data.command.length ||
        data.command.some((value) => typeof value !== "string") ||
        !data.command[0].trim()
      )
        throw Error(HarnessI18n.t("admin.integrations.command_list_strings"));
    }
    await request("integration", data);
    say(HarnessI18n.t("admin.operations.started"));
    pollOperations();
  });
$("integration-refresh").onclick = () =>
  action(async () => {
    const fresh = await request("state");
    state.integrations = fresh.integrations;
    renderProviders();
    say(HarnessI18n.t("admin.integrations.updated"));
  });
$("install-model-button").onclick = () =>
  action(async () => {
    await request("model-install", {
      model: $("install-model").value,
      accepted: $("model-consent").checked,
      runtime: $("install-runtime").value,
    });
    say(HarnessI18n.t("admin.models.download_started"));
    pollOperations();
  });
let operationTimer,
  operationRequest = 0;
async function pollOperations() {
  const sequence = ++operationRequest;
  try {
    const fresh = await request("state");
    if (sequence !== operationRequest) return;
    $("operations-status").hidden = true;
    if (
      state &&
      JSON.stringify(state.authentication) !==
        JSON.stringify(fresh.authentication)
    ) {
      state.authentication = fresh.authentication;
      state.models = fresh.models;
      renderDashboard();
    }
    const allJobs = fresh.operations || [];
    const jobs = activeOperation?.id
      ? allJobs.filter((j) => j.id === activeOperation.id)
      : allJobs.filter((j) => j.state === "running");
    $("operations-panel").hidden = !jobs.length;
    const names = {
      running: HarnessI18n.t("admin.operations.state_running"),
      completed: HarnessI18n.t("admin.operations.state_completed"),
      failed: HarnessI18n.t("admin.operations.state_failed"),
      cancelled: HarnessI18n.t("admin.operations.state_cancelled"),
    };
    // Keep a half-typed login code (and its focus) across the 2 s refresh.
    const typing = document.querySelector("#operations .operation-code input");
    const kept = typing && {
      id: typing.id,
      value: typing.value,
      focused: document.activeElement === typing,
    };
    $("operations").replaceChildren(
      ...jobs.map((j) => {
        const d = element("details");
        d.open =
          j.id === activeOperation?.id ||
          ["running", "failed"].includes(j.state);
        d.className = "operation-" + j.state;
        d.append(
          element(
            "summary",
            operationTitle(j) + " · " + (names[j.state] || j.state),
          ),
        );
        const output = /Traceback \(most recent call last\)/.test(
          j.output || "",
        )
          ? HarnessI18n.t("admin.operations.failed_generic")
          : j.output;
        const pre = element(
          "pre",
          output?.replace("[gemini_client_retired] ", "") ||
            HarnessI18n.t("admin.operations.waiting_cli"),
        );
        d.append(pre);
        const deviceCode =
          j.provider === "codex" && j.kind === "provider-login"
            ? DEVICE_CODE.exec(j.output || "")?.[0]
            : null;
        if (deviceCode) {
          const code = element(
            "p",
            HarnessI18n.t("admin.login.your_code"),
            "login-code",
          );
          const value = element("strong", deviceCode);
          value.dataset.testid = "login-code";
          code.append(value);
          d.append(code, element("p", DEVICE_CODE_NOTE, "hint"));
        }
        if (j.login_url) d.append(loginUrlRow(j.login_url) || "");
        // The other operations (and a harness without login_url) link the URLs in the log.
        const urls = new Set(
          j.login_url || j.state !== "running"
            ? []
            : (j.output || "").match(/https:\/\/[^\s<>"']+/g),
        );
        for (const match of urls) {
          try {
            const url = new URL(match);
            const link = element(
              "a",
              url.hostname === "antigravity.google"
                ? HarnessI18n.t("admin.login.migration_link")
                : HarnessI18n.t("admin.login.authorization_link"),
            );
            link.href = url.href;
            link.target = "_blank";
            link.rel = "noopener noreferrer";
            d.append(link);
          } catch {}
        }
        if (j.state === "running" && j.accepts_input) {
          // Container logins cannot receive the browser callback; Claude shows a code instead.
          const form = element("form", undefined, "operation-code");
          const label = element(
            "label",
            HarnessI18n.t("admin.login.paste_code"),
          );
          const input = element("input");
          input.id = "operation-code-" + j.id;
          input.type = "password";
          input.autocomplete = "off";
          input.spellcheck = false;
          input.required = true;
          label.htmlFor = input.id;
          const submit = element(
            "button",
            HarnessI18n.t("admin.login.send_code"),
            "button primary",
          );
          submit.type = "submit";
          form.append(label, input, submit);
          form.onsubmit = (event) => {
            event.preventDefault();
            action(async () => {
              await request("provider-login-code", {
                id: j.id,
                code: input.value.trim(),
              });
              input.value = "";
              say(HarnessI18n.t("admin.login.code_sent"));
              pollOperations();
            });
          };
          d.append(form);
        }
        if (j.state === "running") {
          const cancel = element(
            "button",
            HarnessI18n.t("admin.operations.cancel"),
            "button secondary",
          );
          cancel.onclick = () =>
            action(async () => {
              await request("cancel-operation", { id: j.id });
              pollOperations();
            });
          d.append(cancel);
        }
        return d;
      }),
    );
    const restored = kept && document.getElementById(kept.id);
    if (restored) {
      restored.value = kept.value;
      if (kept.focused) restored.focus();
    }
    const current = jobs.find((j) => j.id === activeOperation?.id);
    if (current) {
      $("operation-message").textContent = /gemini_client_retired/.test(
        current.output || "",
      )
        ? HarnessI18n.t("admin.login.migration_required")
        : current.state === "completed" &&
            activeOperation?.path === "provider-login"
          ? activeOperation.data.provider === "claude"
            ? HarnessI18n.t("admin.login.completed_claude")
            : HarnessI18n.t("admin.login.completed_other")
          : HarnessI18n.t("admin.operations.state_message", {
              state: (names[current.state] || current.state).toLowerCase(),
            });
      $("operation-retry").hidden =
        !["failed", "cancelled"].includes(current.state) ||
        /gemini_client_retired/.test(current.output || "");
    }
    clearTimeout(operationTimer);
    if (jobs.some((j) => j.state === "running"))
      operationTimer = setTimeout(pollOperations, 2000);
  } catch (e) {
    if (sequence !== operationRequest) return;
    $("operations-panel").hidden = false;
    HarnessUI.notice(
      $("operations-status"),
      HarnessI18n.t("admin.operations.refresh_failed"),
      { error: true },
    );
    clearTimeout(operationTimer);
    operationTimer = setTimeout(pollOperations, 5000);
  }
}

$("local-files").onclick = () =>
  action(async () => {
    const result = await request("local-files", {
      folder: $("model-folder").value,
    });
    discoveredModelFiles = result.files;
    renderProfile();
    $("local-files-status").textContent = HarnessI18n.t(
      "admin.local_models.files_found",
      { count: result.files.length },
    );
  });
$("add-local-model").onclick = () => {
  $("local-add").hidden = false;
  $("source-download").focus();
};
$("close-local-add").onclick = () => {
  $("local-add").hidden = true;
  $("add-local-model").focus();
};
for (const mode of ["download", "file"])
  $("source-" + mode).onclick = () => {
    for (const choice of ["download", "file"])
      $("source-" + choice).setAttribute(
        "aria-pressed",
        String(mode === choice),
      );
    $("local-download").hidden = mode !== "download";
    $("local-existing").hidden = mode !== "file";
  };
$("use-local-file").onclick = () => {
  const file = $("local-file").value;
  if (!file) {
    say(HarnessI18n.t("admin.local_models.select_gguf"), true);
    return;
  }
  if (
    profileDirty &&
    !confirm(HarnessI18n.t("admin.local_models.discard_confirm"))
  )
    return;
  profileModel = file;
  profileDirty = false;
  renderProfile();
  $("local-add").hidden = true;
  editor.open = true;
  binaryInput.focus();
};
$("local-start").onclick = () =>
  action(async () => {
    if (profileDirty)
      throw Error(HarnessI18n.t("admin.local_models.save_before_start"));
    await request("local-start", { file: profileModel, use_profile: true });
    say(HarnessI18n.t("admin.local_models.starting"));
    pollOperations();
  });

const providerDialog = element("dialog");
providerDialog.id = "provider-dialog";
providerDialog.setAttribute("aria-labelledby", "wizard-title");
const workspace = element("div", undefined, "provider-workspace");
$("configured-providers").before(workspace);
const list = element("div", undefined, "provider-list");
const listHeading = element("div", undefined, "provider-list-heading");
listHeading.append(
  element("h2", HarnessI18n.t("admin.providers.list_heading")),
  $("add-provider"),
);
list.append(listHeading, $("configured-providers"));
workspace.append(list);
document.body.append(providerDialog);
const emptyInspector = element("section", undefined, "dashboard-live");
emptyInspector.setAttribute(
  "aria-label",
  HarnessI18n.t("admin.dashboard.aria_label"),
);
emptyInspector.id = "inspector-empty";
$("dashboard").before(emptyInspector);
emptyInspector.innerHTML =
  "<div class=section-heading><div><h2>" +
  HarnessI18n.t("admin.dashboard.realtime_title") +
  "</h2><p>" +
  HarnessI18n.t("admin.dashboard.last_24h") +
  "<span id=dashboard-updated>" +
  HarnessI18n.t("admin.dashboard.checking") +
  "</span></p></div></div><div id=dashboard-metrics class=dashboard-metrics></div><section class=card><div class=panel-header><h3>" +
  HarnessI18n.t("admin.dashboard.local_server") +
  "</h3><small>" +
  HarnessI18n.t("admin.dashboard.machine_usage") +
  "</small></div><div id=server-resources class=server-resources></div></section><section class=card><div class=panel-header><h3>" +
  HarnessI18n.t("admin.dashboard.runs_readonly") +
  "</h3><small id=recent-count></small></div><div id=recent-runs></div></section><p class=hint>" +
  HarnessI18n.t("admin.dashboard.metric_hint_full") +
  "</p>";
const executionPage = element("section", undefined, "dashboard-live");
executionPage.id = "execution-page";
executionPage.setAttribute(
  "aria-label",
  HarnessI18n.t("admin.dashboard.runs_label"),
);
executionPage.append($("recent-runs").closest("section"));
const metricHint = emptyInspector.querySelector(".hint");
metricHint.textContent = HarnessI18n.t("admin.dashboard.metric_hint");
executionPage.append(
  element("p", HarnessI18n.t("admin.dashboard.history_hint"), "hint"),
);
emptyInspector.after(executionPage);

const panelCopy = {
  catalogs: [
    HarnessI18n.t("admin.panels.catalogs_title"),
    HarnessI18n.t("admin.panels.catalogs_text"),
  ],
  connection: [
    HarnessI18n.t("admin.panels.connection_title"),
    HarnessI18n.t("admin.panels.connection_text"),
  ],
  home: [
    HarnessI18n.t("admin.panels.home_title"),
    HarnessI18n.t("admin.panels.home_text"),
  ],
  plugins: [
    HarnessI18n.t("admin.panels.plugins_title"),
    HarnessI18n.t("admin.panels.plugins_text"),
  ],
  providers: [
    HarnessI18n.t("admin.panels.providers_title"),
    HarnessI18n.t("admin.panels.providers_text"),
  ],
  runs: [
    HarnessI18n.t("admin.panels.runs_title"),
    HarnessI18n.t("admin.panels.runs_text"),
  ],
};
function renderPanel() {
  const section = Object.hasOwn(panelCopy, location.hash.slice(1))
    ? location.hash.slice(1)
    : "home";
  $("dashboard").hidden = section !== "providers";
  $("catalog-panel").hidden = section !== "catalogs";
  $("plugins-panel").hidden = section !== "plugins";
  $("config-mcp").hidden = section !== "connection";
  emptyInspector.hidden = section !== "home";
  executionPage.hidden = section !== "runs";
  document.querySelector("#overview h1").textContent = panelCopy[section][0];
  document.querySelector("#overview .panel-description").textContent =
    panelCopy[section][1];
  document.querySelector(".notes").hidden = section !== "providers";
  document.querySelector(".actionbar").hidden = section !== "providers";

  const showEditor = section === "providers" && wizard;
  providerDialog.hidden = !showEditor;
  if (showEditor && !providerDialog.open) providerDialog.showModal();
  else if (!showEditor && providerDialog.open) providerDialog.close();
  const busy = $("busy-status");
  if (busy) (showEditor ? providerDialog : $("main")).prepend(busy);
  if (section !== "providers") $("network").close();
  document.querySelectorAll("[data-panel]").forEach((link) => {
    if (link.dataset.panel === section)
      link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });
}
for (const [section, name] of [
  ["home", "home"],
  ["providers", "plug"],
  ["runs", "list"],
  ["plugins", "cube"],
  ["catalogs", "list"],
  ["connection", "server"],
])
  document.querySelector('[data-panel="' + section + '"]').prepend(icon(name));
providerDialog.addEventListener("cancel", (event) => {
  event.preventDefault();
  if (!working) $("wizard-cancel").click();
});
const onProviders = () => location.hash === "#providers";
window.addEventListener("hashchange", () => {
  renderPanel();
  refreshDashboard();
  if (onProviders()) refreshProviderNotices();
});
// Coming back to the tab re-reads the notices (no timers); an operation in flight keeps its screen.
const rereadNotices = () => {
  if (onProviders() && !working && document.visibilityState === "visible")
    refreshProviderNotices();
};
window.addEventListener("focus", rereadNotices);
document.addEventListener("visibilitychange", rereadNotices);
renderPanel();
if (onProviders()) refreshProviderNotices();
const busyStatus = element("div");
busyStatus.id = "busy-status";
busyStatus.hidden = true;
busyStatus.setAttribute("role", "status");
$("main").prepend(busyStatus);
document.addEventListener(
  "click",
  (e) => {
    if (
      working &&
      e.target.closest("button") &&
      !e.target.closest("#feedback,#appearance-dialog")
    ) {
      e.preventDefault();
      e.stopImmediatePropagation();
      busyStatus.textContent = HarnessI18n.t("admin.operations.busy_wait");
    }
  },
  true,
);
const wizardContent = element("fieldset", undefined, "wizard-content");
wizardContent.id = "wizard-content-lock";
wizardContent.style.cssText = "border:0;margin:0;min-width:0";
const inspectorTabs = element("div", undefined, "inspector-tabs");
inspectorTabs.id = "inspector-tabs";
for (const [index, label] of [
  HarnessI18n.t("admin.wizard.tab_model_hardware"),
  HarnessI18n.t("admin.wizard.tab_plugins"),
  HarnessI18n.t("admin.wizard.tab_connectors"),
].entries()) {
  const button = element("button", label, "button secondary");
  button.dataset.inspectorStep = [0, 4, 3][index];
  button.onclick = () => showStep(Number(button.dataset.inspectorStep));
  inspectorTabs.append(button);
}
providerDialog.append($("wizard-heading"), inspectorTabs, wizardContent);
const wizardFeedback = element("div");
wizardFeedback.id = "wizard-feedback";
wizardContent.append(wizardFeedback);
for (const id of [
  "provider-wizard",
  "local-models",
  "projects",
  "integrations",
  "review",
])
  wizardContent.append($(id));
const wizardFooter = element("div", undefined, "wizard-footer");
wizardFooter.append(
  $("wizard-back"),
  $("wizard-progress"),
  $("wizard-next"),
  $("save"),
);
providerDialog.append(wizardFooter);
const operationsPanel = element("section", undefined, "panel operations-panel");
operationsPanel.id = "operations-panel";
operationsPanel.hidden = true;
const operationStatus = element("div");
operationStatus.id = "operations-status";
operationStatus.hidden = true;
operationsPanel.append(
  element("h3", HarnessI18n.t("admin.operations.in_progress_results")),
  operationStatus,
  $("operations"),
);
const operationDialog = element("dialog");
operationDialog.id = "operation-dialog";
operationDialog.setAttribute("aria-labelledby", "operation-title");
const title = element("h2", HarnessI18n.t("admin.operations.dialog_title"));
title.id = "operation-title";
const message = element("p");
message.id = "operation-message";
message.setAttribute("role", "status");
const close = element(
  "button",
  HarnessI18n.t("admin.operations.close"),
  "button secondary",
);
close.onclick = () => operationDialog.close();
const retry = element(
  "button",
  HarnessI18n.t("admin.operations.retry"),
  "button primary",
);
retry.id = "operation-retry";
retry.hidden = true;
retry.onclick = () => {
  const previous = activeOperation;
  if (previous) action(() => request(previous.path, previous.data));
};
operationDialog.append(title, message, operationsPanel, retry, close);
document.body.append(operationDialog);
const reopen = element(
  "button",
  HarnessI18n.t("admin.operations.in_progress_results"),
  "button secondary",
);
reopen.onclick = () => {
  operationOpener = reopen;
  operationDialog.showModal();
  pollOperations();
};
$("main").append(reopen);
operationDialog.addEventListener("close", () => operationOpener?.focus());

function icon(name) {
  return HarnessUI.icon({ layers: "stack-2", edit: "pencil" }[name] || name);
}
for (const [id, name, text] of [
  ["add-provider", "plus", HarnessI18n.t("admin.providers.add_provider")],
  ["scan", "scan", HarnessI18n.t("admin.providers.check_environment")],
]) {
  $(id).replaceChildren(icon(name), document.createTextNode(text));
}
let step = 0;
const steps = ["providers", "projects", "network"];
function showStepBase(value) {
  if (value !== step) $("feedback").hidden = true;
  step = Math.max(0, Math.min(4, value));
  $("dashboard").hidden = false;
  if (!wizard) {
    $("provider-dialog").hidden = true;
    $("main").insertBefore($("feedback"), $("overview"));
  }
  $("wizard-heading").hidden = !wizard;
  const existing = !!(
    editing &&
    (state.settings.services[editing]?.added ||
      state.settings.services[editing]?.enabled)
  );
  $("inspector-tabs").hidden = !existing;
  $("inspector-tabs").lastElementChild.hidden = !["codex", "claude"].includes(
    editing,
  );
  if (editing && ["codex", "claude"].includes(editing)) {
    $("integration-provider").value = editing;
    $("integration-provider").disabled = true;
  }
  [...$("inspector-tabs").children].forEach((b, i) =>
    b.setAttribute(
      "aria-pressed",
      String(Number(b.dataset.inspectorStep) === step),
    ),
  );
  $("provider-dialog").hidden = !wizard;
  $("provider-dialog").classList.toggle("local-inspector", editing === "local");
  document
    .querySelectorAll("[data-step]")
    .forEach(
      (el) =>
        (el.hidden =
          !wizard ||
          Number(el.dataset.step) !== step ||
          (el.id === "local-models" && editing !== "local")),
    );
  if (editing === "local") {
    $("local-models").hidden = !wizard || ![0, 1].includes(step);
    $("local-profile-summary").hidden = step !== 0;
    $("hardware-editor-details").hidden = step !== 0 || !profileModel;
    $("local-model-permissions").hidden = step !== 1;
    $("local-launch").hidden = step !== 0;
    $("local-import").hidden =
      step !== 0 ||
      !(visibleProviders().find((i) => i.id === "local")?.runtimes || []).some(
        (r) => r.model_file === profileModel,
      );
    $("add-local-model").hidden = step !== 0;
    if (step !== 0) $("local-add").hidden = true;
  }
  $("wizard-progress").textContent = wizard
    ? HarnessI18n.t("admin.wizard.step", {
        number: step + 1,
        name: [
          HarnessI18n.t("admin.wizard.step_choose"),
          HarnessI18n.t("admin.wizard.step_permissions"),
          HarnessI18n.t("admin.wizard.step_review"),
        ][step],
      })
    : "";
  $("wizard-back").hidden = !wizard || (step === 0 && (!editing || existing));
  $("wizard-back").textContent =
    step === 0
      ? HarnessI18n.t("admin.wizard.choose_other_provider")
      : HarnessI18n.t("admin.wizard.back");
  $("wizard-next").hidden = !wizard || existing || step === 2;
  $("save").hidden = !wizard || (!existing && step !== 2);
  $("save").textContent = existing
    ? HarnessI18n.t("admin.wizard.save_changes")
    : HarnessI18n.t("admin.wizard.complete_save");
  $("wizard-progress").hidden = existing;
  $("save").title = state.status.running
    ? HarnessI18n.t("admin.wizard.save_title")
    : "";
  if (editing) {
    $("provider-review").textContent = HarnessI18n.t(
      "admin.wizard.review_summary",
      {
        name: visibleProviders().find((i) => i.id === editing)?.name || editing,
      },
    );
  }
}
$("wizard-back").onclick = () => {
  if (step === 0) {
    openWizard();
    $("provider-options").querySelector("button")?.focus();
  } else showStep(step >= 2 ? 0 : step - 1);
};
$("wizard-next").onclick = () => {
  if (!editing) {
    say(HarnessI18n.t("admin.wizard.choose_provider"), true);
    return;
  }
  if (step === 0 && !settings.services[editing].models.length) {
    const info = visibleProviders().find((i) => i.id === editing);
    say(
      editing === "local"
        ? HarnessI18n.t("admin.wizard.local_model_needed")
        : !info?.found && !info?.api
          ? HarnessI18n.t("admin.wizard.install_cli")
          : HarnessI18n.t("admin.wizard.check_account"),
      true,
    );
    return;
  }
  showStep(step === 0 ? 2 : step + 1);
};
$("add-provider").onclick = () =>
  action(async () => {
    if (
      (unsaved || profileDirty) &&
      !confirm(HarnessI18n.t("admin.providers.discard_before_add"))
    )
      return;
    unsaved = false;
    profileDirty = false;
    const button = $("add-provider");
    button.disabled = true;
    const label = button.textContent;
    button.textContent = HarnessI18n.t("admin.providers.checking");
    try {
      state.inventory = await request("scan", {});
      openWizard();
    } finally {
      button.disabled = false;
      button.replaceChildren(icon("plus"), document.createTextNode(label));
    }
  });
$("wizard-cancel").onclick = () =>
  action(async () => {
    if (
      (unsaved || profileDirty) &&
      !confirm(HarnessI18n.t("admin.wizard.discard_cancel"))
    )
      return;
    const previousProvider = editing;
    profileDirty = false;
    wizard = false;
    editing = null;
    await load({ select: false });
    say(HarnessI18n.t("admin.wizard.edit_discarded"));
    (
      document.querySelector(
        '[data-configured-provider="' +
          previousProvider +
          '"] button[data-edit-provider]',
      ) || $("add-provider")
    ).focus();
  });
$("manage-network").onclick = () => {
  $("network-feedback").hidden = true;
  $("network").showModal();
};
$("network-close").onclick = () => $("network").close();
const hardwareFields = [
  ["n-gpu-layers", HarnessI18n.t("admin.localmodel.field.n_gpu_layers")],
  ["n-cpu-moe", HarnessI18n.t("admin.localmodel.field.n_cpu_moe")],
  ["cpu-range", HarnessI18n.t("admin.localmodel.field.cpu_range")],
  ["cpu-range-batch", HarnessI18n.t("admin.localmodel.field.cpu_range_batch")],
  ["threads", HarnessI18n.t("admin.localmodel.field.threads")],
  ["threads-batch", HarnessI18n.t("admin.localmodel.field.threads_batch")],
  ["ctx-size", HarnessI18n.t("admin.localmodel.field.ctx_size")],
  ["device", HarnessI18n.t("admin.localmodel.field.device")],
  ["main-gpu", HarnessI18n.t("admin.localmodel.field.main_gpu")],
  ["split-mode", HarnessI18n.t("admin.localmodel.field.split_mode")],
  ["tensor-split", HarnessI18n.t("admin.localmodel.field.tensor_split")],
  ["parallel", HarnessI18n.t("admin.localmodel.field.parallel")],
  ["flash-attn", HarnessI18n.t("admin.localmodel.field.flash_attn")],
  ["cache-type-k", HarnessI18n.t("admin.localmodel.field.cache_type_k")],
  ["cache-type-v", HarnessI18n.t("admin.localmodel.field.cache_type_v")],
  ["cache-ram", HarnessI18n.t("admin.localmodel.field.cache_ram")],
  ["reasoning", HarnessI18n.t("admin.localmodel.field.reasoning")],
  [
    "reasoning-format",
    HarnessI18n.t("admin.localmodel.field.reasoning_format"),
  ],
  [
    "reasoning-budget",
    HarnessI18n.t("admin.localmodel.field.reasoning_budget"),
  ],
  ["load-mode", HarnessI18n.t("admin.localmodel.field.load_mode")],
  ["cpu-strict", HarnessI18n.t("admin.localmodel.field.cpu_strict")],
  [
    "cpu-strict-batch",
    HarnessI18n.t("admin.localmodel.field.cpu_strict_batch"),
  ],
  ["temp", HarnessI18n.t("admin.localmodel.field.temp")],
  ["top-k", HarnessI18n.t("admin.localmodel.field.top_k")],
  ["top-p", HarnessI18n.t("admin.localmodel.field.top_p")],
  ["min-p", HarnessI18n.t("admin.localmodel.field.min_p")],
  ["repeat-penalty", HarnessI18n.t("admin.localmodel.field.repeat_penalty")],
  ["seed", HarnessI18n.t("admin.localmodel.field.seed")],
];
const profilePanel = $("local-profile-summary").parentElement;
const profilePicker = element("select");
profilePicker.id = "hardware-model";
profilePicker.className = "form-select";
const profileLabel = element(
  "label",
  HarnessI18n.t("admin.localmodel.model_for_profile"),
);
profileLabel.htmlFor = "hardware-model";
profileLabel.append(profilePicker);
profilePanel.insertBefore(profileLabel, $("local-profile-summary"));
const editor = element("details", undefined, "advanced");
editor.append(
  element("summary", HarnessI18n.t("admin.localmodel.advanced_summary")),
);
const editorGrid = element("div", undefined, "hardware-editor");
const binaryLabel = element(
  "label",
  HarnessI18n.t("admin.localmodel.binary_label"),
);
const binaryInput = element("input");
binaryInput.id = "profile-binary";
binaryInput.className = "form-control";
binaryLabel.append(binaryInput);
editor.append(binaryLabel);
const descriptionLabel = element(
    "label",
    HarnessI18n.t("admin.localmodel.description_label"),
  ),
  descriptionInput = element("textarea");
descriptionInput.id = "profile-description";
descriptionInput.className = "form-control";
descriptionInput.rows = 4;
descriptionInput.maxLength = 2000;
descriptionInput.placeholder = HarnessI18n.t(
  "admin.localmodel.description_placeholder",
);
descriptionLabel.append(descriptionInput);
editor.append(descriptionLabel);
const hardwareHelp = {
  "n-gpu-layers": HarnessI18n.t("admin.localmodel.help.n_gpu_layers"),
  "n-cpu-moe": HarnessI18n.t("admin.localmodel.help.n_cpu_moe"),
  "cpu-range": HarnessI18n.t("admin.localmodel.help.cpu_range"),
  "cpu-range-batch": HarnessI18n.t("admin.localmodel.help.cpu_range_batch"),
  threads: HarnessI18n.t("admin.localmodel.help.threads"),
  "threads-batch": HarnessI18n.t("admin.localmodel.help.threads_batch"),
  "ctx-size": HarnessI18n.t("admin.localmodel.help.ctx_size"),
  device: HarnessI18n.t("admin.localmodel.help.device"),
  "main-gpu": HarnessI18n.t("admin.localmodel.help.main_gpu"),
  "split-mode": HarnessI18n.t("admin.localmodel.help.split_mode"),
  "tensor-split": HarnessI18n.t("admin.localmodel.help.tensor_split"),
  parallel: HarnessI18n.t("admin.localmodel.help.parallel"),
  "flash-attn": HarnessI18n.t("admin.localmodel.help.flash_attn"),
  "cache-type-k": HarnessI18n.t("admin.localmodel.help.cache_type_k"),
  "cache-type-v": HarnessI18n.t("admin.localmodel.help.cache_type_v"),
  "cache-ram": HarnessI18n.t("admin.localmodel.help.cache_ram"),
  reasoning: HarnessI18n.t("admin.localmodel.help.reasoning"),
  "reasoning-format": HarnessI18n.t("admin.localmodel.help.reasoning_format"),
  "reasoning-budget": HarnessI18n.t("admin.localmodel.help.reasoning_budget"),
  "load-mode": HarnessI18n.t("admin.localmodel.help.load_mode"),
  "cpu-strict": HarnessI18n.t("admin.localmodel.help.cpu_strict"),
  "cpu-strict-batch": HarnessI18n.t("admin.localmodel.help.cpu_strict_batch"),
  temp: HarnessI18n.t("admin.localmodel.help.temp"),
  "top-k": HarnessI18n.t("admin.localmodel.help.top_k"),
  "top-p": HarnessI18n.t("admin.localmodel.help.top_p"),
  "min-p": HarnessI18n.t("admin.localmodel.help.min_p"),
  "repeat-penalty": HarnessI18n.t("admin.localmodel.help.repeat_penalty"),
  seed: HarnessI18n.t("admin.localmodel.help.seed"),
};
for (const [key, label] of hardwareFields) {
  const wrap = element("label", label),
    input = element("input");
  input.id = "profile-" + key;
  input.className = "form-control";
  input.placeholder = HarnessI18n.t("admin.localmodel.runtime_default");
  wrap.append(input);
  fieldHelp(
    input,
    HarnessI18n.t("admin.localmodel.help_with_blank", {
      help: hardwareHelp[key],
    }),
  );
  editorGrid.append(wrap);
}
const detectDevices = element(
  "button",
  HarnessI18n.t("admin.localmodel.detect_devices"),
  "button secondary",
);
detectDevices.id = "profile-detect-devices";
const deviceStatus = element("div");
deviceStatus.id = "profile-device-status";
deviceStatus.setAttribute("role", "status");
const deviceOptions = element("datalist");
deviceOptions.id = "profile-device-options";
editorGrid
  .querySelector("#profile-device")
  .setAttribute("list", deviceOptions.id);
detectDevices.onclick = () =>
  action(async () => {
    deviceStatus.textContent = HarnessI18n.t(
      "admin.localmodel.querying_devices",
    );
    try {
      const result = await request("local-devices", {
        binary: binaryInput.value.trim(),
      });
      deviceOptions.replaceChildren(
        ...result.devices.map((d) => new Option(d.name, d.id)),
      );
      deviceStatus.replaceChildren(
        element(
          "p",
          result.devices.length
            ? HarnessI18n.t("admin.localmodel.devices_reported")
            : HarnessI18n.t("admin.localmodel.no_gpu_reported"),
        ),
      );
      for (const d of result.devices)
        deviceStatus.append(element("p", d.id + " · " + d.name));
      if (!result.capabilities_verified)
        deviceStatus.append(
          element(
            "p",
            HarnessI18n.t("admin.localmodel.capabilities_unverified"),
            "hint",
          ),
        );
    } catch (e) {
      deviceStatus.textContent = e.message;
      throw e;
    }
  });
editor.append(
  detectDevices,
  deviceStatus,
  deviceOptions,
  element("p", HarnessI18n.t("admin.localmodel.device_hint"), "hint"),
);
editor.append(
  editorGrid,
  element("p", HarnessI18n.t("admin.localmodel.save_hint"), "hint"),
);
const profileSave = element(
  "button",
  HarnessI18n.t("admin.localmodel.profile_save"),
  "button primary",
);
profileSave.id = "profile-save";
profilePanel.append(editor, profileSave);
function markProfileDirty() {
  profileDirty = true;
  profileSave.textContent = HarnessI18n.t(
    "admin.localmodel.profile_save_dirty",
  );
}
editor.addEventListener("input", markProfileDirty);
const localPermissions = $("local-model-permissions");
localPermissions.append(
  element("h3", HarnessI18n.t("admin.localmodel.permissions_title")),
  element("p", HarnessI18n.t("admin.localmodel.permissions_hint"), "hint"),
);
const toolsLabel = element("label", undefined, "toggle-row"),
  toolsInput = element("input");
toolsInput.type = "checkbox";
toolsInput.id = "profile-tools";
toolsLabel.append(
  toolsInput,
  element("span", HarnessI18n.t("admin.localmodel.allow_tools")),
);
localPermissions.append(
  toolsLabel,
  element("p", HarnessI18n.t("admin.localmodel.tools_hint"), "hint"),
);
const modelPermissionFields = [
  ["read", HarnessI18n.t("admin.localmodel.perm_read_label")],
  ["write", HarnessI18n.t("admin.localmodel.perm_write_label")],
  ["upload", HarnessI18n.t("admin.localmodel.perm_upload_label")],
  ["internet", HarnessI18n.t("admin.localmodel.perm_internet_label")],
  ["shell", HarnessI18n.t("admin.localmodel.perm_shell_label")],
  ["hooks", HarnessI18n.t("admin.localmodel.perm_hooks_label")],
];
const permissionHelp = {
  read: HarnessI18n.t("admin.localmodel.perm_read_help"),
  write: HarnessI18n.t("admin.localmodel.perm_write_help"),
  upload: HarnessI18n.t("admin.localmodel.perm_upload_help"),
  internet: HarnessI18n.t("admin.localmodel.perm_internet_help"),
  shell: HarnessI18n.t("admin.localmodel.perm_shell_help"),
  hooks: HarnessI18n.t("admin.localmodel.perm_hooks_help"),
};
const permissionGrid = element("div", undefined, "permissions");
for (const [key, label] of modelPermissionFields) {
  const row = element("label", undefined, "toggle-row"),
    input = element("input");
  input.type = "checkbox";
  input.id = "profile-permission-" + key;
  const copy = element("span");
  copy.append(element("strong", label), element("small", permissionHelp[key]));
  row.append(input, copy);
  permissionGrid.append(row);
}
localPermissions.append(
  permissionGrid,
  element("p", HarnessI18n.t("admin.localmodel.permissions_footer"), "hint"),
);
function refreshPermissionAvailability() {
  for (const [key] of modelPermissionFields)
    $("profile-permission-" + key).disabled =
      key !== "upload" && !toolsInput.checked;
}
localPermissions.addEventListener("change", () => {
  markProfileDirty();
  refreshPermissionAvailability();
});
let modelRoots = [];
const modelRootsSection = element("section", undefined, "model-roots");
modelRootsSection.append(
  element("h3", HarnessI18n.t("admin.localmodel.roots_title")),
  element("p", HarnessI18n.t("admin.localmodel.roots_hint"), "hint"),
);
const modelRootsList = element("div");
modelRootsList.id = "model-roots-list";
const modelRootsAdd = element(
  "button",
  HarnessI18n.t("admin.localmodel.roots_add"),
  "button secondary",
);
modelRootsAdd.id = "model-roots-add";
modelRootsAdd.type = "button";
function renderModelRoots() {
  modelRootsList.replaceChildren();
  for (const root of modelRoots) {
    const row = element("div", undefined, "model-root-row"),
      remove = element(
        "button",
        HarnessI18n.t("sidebar.project.folder_remove"),
        "button secondary",
      );
    remove.type = "button";
    remove.setAttribute(
      "aria-label",
      HarnessI18n.t("admin.localmodel.root_remove_label", { path: root }),
    );
    remove.onclick = () => {
      modelRoots = modelRoots.filter((path) => path !== root);
      renderModelRoots();
      markProfileDirty();
    };
    row.append(element("span", root), remove);
    modelRootsList.append(row);
  }
  if (!modelRoots.length)
    modelRootsList.append(
      element("p", HarnessI18n.t("admin.localmodel.roots_empty"), "hint"),
    );
}
modelRootsAdd.onclick = () =>
  chooseFolder((path) => {
    if (!modelRoots.includes(path)) {
      modelRoots.push(path);
      renderModelRoots();
      markProfileDirty();
    }
    modelRootsAdd.focus();
  });
modelRootsSection.append(modelRootsList, modelRootsAdd);
localPermissions.append(modelRootsSection);
function profiles() {
  return (
    state.local_profiles ||
    (state.local_profile?.model_file
      ? { [state.local_profile.model_file]: state.local_profile }
      : {})
  );
}

profilePicker.onchange = () => {
  if (
    profileDirty &&
    !confirm(HarnessI18n.t("admin.localmodel.discard_confirm"))
  ) {
    profilePicker.value = profileModel;
    return;
  }
  profileModel = profilePicker.value;
  profileDirty = false;
  renderProfile();
};
function renderProfile() {
  // Servers on the network are not local processes: they never block starting a local model.
  const saved = profiles(),
    runtimes = (
      visibleProviders().find((x) => x.id === "local")?.runtimes || []
    ).filter((r) => !r.remote);
  const paths = [
    ...new Set(
      [
        ...runtimes.map((r) => r.model_file),
        ...Object.keys(saved),
        ...discoveredModelFiles.map((f) => f.path),
      ].filter(Boolean),
    ),
  ];
  if (!paths.includes(profileModel)) profileModel = paths[0] || "";
  profilePicker.replaceChildren(
    ...paths.map(
      (path) =>
        new Option(
          path.split("/").pop() +
            (saved[path]
              ? " · " + HarnessI18n.t("admin.localmodel.saved_profile")
              : " · " + HarnessI18n.t("admin.localmodel.no_saved_profile")),
          path,
        ),
    ),
  );
  if (!paths.length)
    profilePicker.append(
      new Option(HarnessI18n.t("admin.localmodel.find_gguf"), ""),
    );
  profilePicker.value = profileModel;
  profilePicker.disabled = !paths.length;
  deviceStatus.replaceChildren();
  const profile = saved[profileModel] || {},
    p = profile.performance || {},
    active = runtimes.find((r) => r.model_file === profileModel);
  const summary = $("local-profile-summary");
  summary.replaceChildren();
  $("local-add").hidden = paths.length ? $("local-add").hidden : false;
  function section(title, rows) {
    const card = element("section", undefined, "hardware-card");
    card.append(element("h3", title));
    for (const [name, value] of rows) {
      const row = element("div", undefined, "hardware-row");
      row.append(
        element("span", name),
        element(
          "strong",
          value ?? HarnessI18n.t("admin.localmodel.not_provided"),
        ),
      );
      card.append(row);
    }
    return card;
  }
  summary.append(
    section(HarnessI18n.t("admin.localmodel.section_model"), [
      [
        HarnessI18n.t("admin.localmodel.row_model"),
        profileModel
          ? profileModel.split("/").pop()
          : HarnessI18n.t("admin.localmodel.no_file_selected"),
      ],
      [
        HarnessI18n.t("admin.localmodel.saved_profile"),
        profile.model_file
          ? HarnessI18n.t("admin.localmodel.saved_yes")
          : HarnessI18n.t("admin.localmodel.saved_none"),
      ],
      [
        HarnessI18n.t("admin.localmodel.row_verified"),
        active
          ? active.runtime + " · " + active.id
          : HarnessI18n.t("admin.localmodel.not_running"),
      ],
    ]),
  );
  if (profile.description) {
    const description = element("section", undefined, "hardware-card");
    description.append(
      element("h3", HarnessI18n.t("admin.localmodel.section_description")),
      element("p", profile.description),
    );
    summary.append(description);
  }
  const grid = element("div", undefined, "hardware-grid");
  grid.append(
    section(HarnessI18n.t("admin.localmodel.section_gpu"), [
      [HarnessI18n.t("admin.localmodel.row_device"), p.device],
      [
        HarnessI18n.t("admin.localmodel.row_requested_layers"),
        p["n-gpu-layers"],
      ],
      [
        HarnessI18n.t("admin.localmodel.row_loaded_layers"),
        HarnessI18n.t("admin.localmodel.not_measured"),
      ],
      [
        HarnessI18n.t("admin.localmodel.vram_in_use"),
        HarnessI18n.t("admin.localmodel.no_telemetry"),
      ],
    ]),
    section(HarnessI18n.t("admin.localmodel.section_cpu"), [
      [HarnessI18n.t("admin.localmodel.row_moe_cpu"), p["n-cpu-moe"]],
      [HarnessI18n.t("admin.localmodel.row_logical_cpus"), p["cpu-range"]],
      [HarnessI18n.t("admin.localmodel.row_batch_cpus"), p["cpu-range-batch"]],
      [
        HarnessI18n.t("admin.localmodel.row_threads"),
        [p.threads ?? "—", p["threads-batch"] ?? "—"].join(" / "),
      ],
    ]),
  );
  if (profileModel) summary.append(grid);
  if (active) {
    const perf = active.performance || {},
      running = element("details", undefined, "advanced");
    running.append(
      element("summary", HarnessI18n.t("admin.localmodel.summary_running")),
      section(HarnessI18n.t("admin.localmodel.section_detected"), [
        [
          HarnessI18n.t("admin.localmodel.running_gpu_layers"),
          perf["n-gpu-layers"],
        ],
        [HarnessI18n.t("admin.localmodel.running_moe"), perf["n-cpu-moe"]],
        [HarnessI18n.t("admin.localmodel.row_logical_cpus"), perf["cpu-range"]],
        [HarnessI18n.t("admin.localmodel.running_ctx"), perf["ctx-size"]],
      ]),
    );
    summary.append(running);
  }
  summary.append(
    element("p", HarnessI18n.t("admin.localmodel.detected_hint"), "hint"),
  );
  if (profileModel) {
    const details = element("details");
    details.append(
      element("summary", HarnessI18n.t("admin.localmodel.summary_weights")),
      element("p", profileModel, "hint"),
    );
    summary.append(details);
  }
  $("dashboard-profile").textContent = HarnessI18n.t(
    "admin.localmodel.dashboard_count",
    { count: Object.keys(saved).length },
  );
  $("local-import").hidden = !active;
  $("local-import").textContent = HarnessI18n.t(
    "admin.localmodel.import_running",
  );
  editor.hidden = !profileModel || step !== 0;
  profileSave.disabled = !profileModel;
  localPermissions.hidden = step !== 0;
  profilePanel.querySelector("h3").textContent = profileModel
    ? HarnessI18n.t("admin.localmodel.panel_title_model", {
        name: profileModel.split("/").pop(),
      })
    : HarnessI18n.t("admin.localmodel.panel_title_none");
  if (!profileDirty) {
    modelRoots = [...(profile.allowed_roots || [])];
    renderModelRoots();
    toolsInput.checked = profile.capabilities?.tools === true;
    for (const [key] of modelPermissionFields)
      $("profile-permission-" + key).checked =
        profile.permissions?.[key] === true;
    refreshPermissionAvailability();
    binaryInput.value =
      profile.binary ||
      active?.binary ||
      runtimes.find((r) => r.binary)?.binary ||
      "";
    descriptionInput.value = profile.description || "";
    for (const [key] of hardwareFields)
      $("profile-" + key).value = p[key] || "";
    profileSave.textContent = HarnessI18n.t("admin.localmodel.profile_save");
  }
  const launchChoice = $("local-file").value;
  $("local-file").replaceChildren(
    ...paths.map((path) => new Option(path.split("/").pop(), path)),
  );
  if (paths.includes(launchChoice)) $("local-file").value = launchChoice;
  else $("local-file").value = profileModel;
  const cpuOnly = profile.performance?.["n-gpu-layers"] === "0";
  $("local-start").disabled =
    !profile.model_file || !!active || (!!runtimes.length && !cpuOnly);
  $("local-launch-status").textContent = active
    ? HarnessI18n.t("admin.localmodel.launch_running")
    : runtimes.length && !cpuOnly
      ? HarnessI18n.t("admin.localmodel.launch_other")
      : profile.model_file
        ? HarnessI18n.t("admin.localmodel.launch_ready")
        : HarnessI18n.t("admin.localmodel.launch_configure");
}
editor.id = "hardware-editor-details";
profileSave.onclick = () =>
  action(async () => {
    const performance = { ...(profiles()[profileModel]?.performance || {}) };
    for (const [key] of hardwareFields) {
      const value = $("profile-" + key).value.trim();
      if (value) performance[key] = value;
      else delete performance[key];
    }
    const permissions = {};
    for (const [key] of modelPermissionFields)
      permissions[key] = $("profile-permission-" + key).checked;
    const previous = profiles()[profileModel] || {};
    const profile = await request("local-profile", {
      ...previous,
      model_file: profileModel,
      binary: binaryInput.value.trim(),
      description: descriptionInput.value,
      performance,
      permissions: { ...(previous.permissions || {}), ...permissions },
      allowed_roots: [...modelRoots],
      capabilities: {
        ...(previous.capabilities || {}),
        tools: toolsInput.checked,
      },
    });
    state.local_profiles = { ...profiles(), [profile.model_file]: profile };
    profileDirty = false;
    renderProfile();
    HarnessUI.toast(
      HarnessI18n.t("admin.localmodel.profile_saved", {
        file: profile.model_file.split("/").pop(),
      }),
    );
  });
$("local-import").onclick = () =>
  action(async () => {
    if (
      profileDirty &&
      !confirm(HarnessI18n.t("admin.localmodel.import_confirm"))
    )
      return;
    const profile = await request("local-import", { file: profileModel });
    state.local_profiles = { ...profiles(), [profile.model_file]: profile };
    profileDirty = false;
    renderProfile();
    HarnessUI.toast(HarnessI18n.t("admin.localmodel.import_toast"));
  });
action(async () => {
  await load();
  pollOperations();
});

let importBundle = null;
$("export-settings").onclick = () =>
  action(async () => {
    const bundle = await request("settings-export", {});
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(bundle, null, 2)], { type: "application/json" }),
    );
    const link = element("a");
    link.href = url;
    link.download = "keepharness-settings.json";
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    say(HarnessI18n.t("admin.settings.export_done"));
  });
$("import-settings").onchange = () =>
  action(async () => {
    importBundle = null;
    $("import-preview").hidden = true;
    const file = $("import-settings").files[0];
    if (!file) return;
    if (file.size > 60000)
      throw Error(HarnessI18n.t("admin.settings.file_too_large"));
    let candidate;
    try {
      candidate = JSON.parse(await file.text());
    } catch {
      throw Error(HarnessI18n.t("admin.settings.invalid_json"));
    }
    const preview = await request("settings-import", {
      bundle: candidate,
      apply: false,
    });
    importBundle = candidate;
    $("import-summary").textContent = HarnessI18n.t(
      "admin.settings.import_summary",
      {
        services: preview.services.join(", "),
        projects: HarnessI18n.t("admin.settings.import_projects", {
          count: preview.projects,
        }),
        profile: preview.local_profile
          ? HarnessI18n.t("admin.settings.import_with_profile")
          : HarnessI18n.t("admin.settings.import_keeps_profile"),
      },
    );
    $("import-preview").hidden = false;
  });
$("apply-import").onclick = () =>
  action(async () => {
    if (!importBundle) return;
    await request("settings-import", { bundle: importBundle, apply: true });
    importBundle = null;
    $("import-preview").hidden = true;
    await load();
    $("network").close();
    say(HarnessI18n.t("admin.settings.import_done"));
  });
$("cancel-import").onclick = () => {
  importBundle = null;
  $("import-preview").hidden = true;
  $("import-settings").value = "";
};

function renderMcpDefaults() {
  const select = $("mcp-default-model"),
    saved = state.settings.mcp_defaults || {};
  select.replaceChildren(
    new Option(HarnessI18n.t("admin.mcp.default_auto"), ""),
  );
  for (const [backend, spec] of Object.entries(state.settings.services))
    if (spec.enabled)
      for (const model of spec.models.filter((m) =>
        HarnessUI.selectableModel(backend, m),
      )) {
        select.append(
          new Option(
            HarnessUI.providerName(backend) + " · " + model,
            JSON.stringify([backend, model]),
          ),
        );
      }
  select.value = saved.model
    ? JSON.stringify([saved.backend, saved.model])
    : "";
  renderMcpEfforts(saved.effort || "");
  $("save-mcp").disabled = false;
  $("mcp-save-note").textContent = state.status.running
    ? HarnessI18n.t("admin.mcp.save_note_running")
    : HarnessI18n.t("admin.mcp.save_note_stopped");
}
function renderMcpEfforts(preferred = "") {
  const select = $("mcp-default-effort");
  select.replaceChildren(
    new Option(HarnessI18n.t("admin.mcp.effort_auto"), ""),
  );
  const value = $("mcp-default-model").value;
  if (!value) {
    select.disabled = true;
    settingsSearchChanged("changed");
    return;
  }
  select.disabled = false;
  const [backend, model] = JSON.parse(value);
  const efforts =
    state.models[backend]?.[model] ||
    (["local", "claude", "gemini"].includes(backend) ? ["configured"] : []);
  for (const effort of efforts)
    select.append(new Option(effortLabels[effort] || effort, effort));
  if (preferred && !efforts.includes(preferred))
    select.append(
      new Option(
        HarnessI18n.t("admin.mcp.effort_saved", { effort: preferred }),
        preferred,
      ),
    );
  select.value = preferred;
  settingsSearchChanged("changed");
}
$("mcp-default-model").onchange = () => renderMcpEfforts();
$("save-mcp").onclick = () =>
  action(async () => {
    try {
      const draft = structuredClone(state.settings),
        value = $("mcp-default-model").value;
      if (value) {
        const [backend, model] = JSON.parse(value);
        draft.mcp_defaults = {
          backend,
          model,
          effort: $("mcp-default-effort").value,
        };
      } else draft.mcp_defaults = {};
      await request("settings", draft);
      state.settings = draft;
      settings.mcp_defaults = draft.mcp_defaults;
      HarnessUI.notice($("mcp-feedback"), HarnessI18n.t("admin.mcp.saved"));
    } catch (e) {
      HarnessUI.notice($("mcp-feedback"), e.message, { error: true });
    }
  });

let dashboardLoading = false;
async function refreshDashboard() {
  if (
    document.hidden ||
    ["#providers", "#plugins"].includes(location.hash) ||
    dashboardLoading
  )
    return;
  dashboardLoading = true;
  try {
    const data = await request("dashboard");
    if (!data.available) throw Error("Metrics temporarily unavailable");
    const format = (n) =>
      n == null
        ? HarnessI18n.t("admin.dashboard.not_provided")
        : Number(n).toLocaleString("en-US");
    $("dashboard-updated").textContent = HarnessI18n.t(
      "admin.dashboard.updated",
      { time: new Date(data.checked_at * 1000).toLocaleTimeString("en-US") },
    );
    const values = [
      [
        HarnessI18n.t("admin.dashboard.metric_requests"),
        format(Number(data.requests_per_second.toFixed(3))),
        HarnessI18n.t("admin.dashboard.metric_requests_detail"),
      ],
      [
        HarnessI18n.t("admin.dashboard.metric_running"),
        data.active + " / " + data.queued,
        HarnessI18n.t("admin.dashboard.metric_running_detail"),
      ],
      [
        HarnessI18n.t("admin.dashboard.metric_tokens"),
        data.input_tokens == null && data.output_tokens == null
          ? HarnessI18n.t("admin.dashboard.not_provided")
          : format((data.input_tokens || 0) + (data.output_tokens || 0)),
        HarnessI18n.t("admin.dashboard.metric_tokens_detail", {
          jobs: format(data.measured_jobs),
        }),
      ],
      [
        HarnessI18n.t("admin.dashboard.metric_output"),
        format(data.latest_output_tokens_per_second),
        HarnessI18n.t("admin.dashboard.metric_output_detail"),
      ],
    ];
    $("dashboard-metrics").replaceChildren(
      ...values.map(([label, value, detail]) => {
        const card = element("article", undefined, "card metric-card");
        card.append(
          element("span", label),
          element("strong", value),
          element("small", detail),
        );
        return card;
      }),
    );
    const hw = data.hardware;
    const gib = (n) =>
      n == null
        ? HarnessI18n.t("admin.dashboard.not_provided")
        : (n / 1073741824).toFixed(1) + " GiB";
    const resources = [
      [
        HarnessI18n.t("admin.dashboard.resource_cpu"),
        hw.cpu_percent == null
          ? HarnessI18n.t("admin.dashboard.waiting_sample")
          : format(hw.cpu_percent) + "%",
        hw.cpu_percent,
        100,
      ],
      [
        HarnessI18n.t("admin.dashboard.resource_memory"),
        gib(hw.memory_used) + " / " + gib(hw.memory_total),
        hw.memory_used,
        hw.memory_total,
      ],
    ];
    for (const gpu of hw.gpus) {
      resources.push(
        [
          HarnessI18n.t("admin.dashboard.resource_gpu_named", {
            name: gpu.name,
          }),
          format(gpu.percent) + "% · " + format(gpu.temperature) + " °C",
          gpu.percent,
          100,
        ],
        [
          HarnessI18n.t("admin.dashboard.resource_vram_named", {
            name: gpu.name,
          }),
          gib(gpu.vram_used) + " / " + gib(gpu.vram_total),
          gpu.vram_used,
          gpu.vram_total,
        ],
      );
    }
    if (!hw.gpus.length)
      resources.push([
        HarnessI18n.t("admin.dashboard.resource_gpu"),
        HarnessI18n.t("admin.dashboard.measurement_unavailable"),
        null,
        100,
      ]);
    $("server-resources").replaceChildren(
      ...resources.map(([label, value, current, max]) => {
        const box = element("div");
        box.append(element("span", label), element("strong", value));
        if (current != null && max) {
          const meter = document.createElement("meter");
          meter.min = 0;
          meter.max = max;
          meter.value = current;
          meter.setAttribute("aria-label", label);
          box.append(meter);
        }
        return box;
      }),
    );
    const names = {
      queued: HarnessI18n.t("admin.dashboard.state_queued"),
      running: HarnessI18n.t("admin.dashboard.state_running"),
      completed: HarnessI18n.t("admin.dashboard.state_completed"),
      failed: HarnessI18n.t("admin.dashboard.state_failed"),
      cancelled: HarnessI18n.t("admin.dashboard.state_cancelled"),
      interrupted: HarnessI18n.t("admin.dashboard.state_interrupted"),
    };
    $("recent-count").textContent = HarnessI18n.t(
      "admin.dashboard.recent_count",
      {
        shown: data.recent.length,
        total: data.recent_count ?? data.recent.length,
      },
    );
    $("recent-runs").setAttribute(
      "aria-label",
      HarnessI18n.t("admin.dashboard.recent_label", {
        shown: data.recent.length,
        total: data.recent_count ?? data.recent.length,
      }),
    );
    const holder = $("recent-runs");
    const retained = new Map(
      [...holder.querySelectorAll("details[data-job]")].map((e) => [
        e.dataset.job,
        e,
      ]),
    );
    const rows = data.recent.map((job) => {
      let row = retained.get(job.id);
      if (!row) {
        row = element("details", undefined, "execution-row");
        row.dataset.job = job.id;
        row.append(
          element("summary"),
          element("div", undefined, "execution-detail"),
        );
        row.addEventListener("toggle", () => {
          if (row.open) refreshExecution(row);
        });
      }
      row.firstElementChild.replaceChildren(
        document.createTextNode(
          new Date(job.created * 1000).toLocaleTimeString("en-US") + " · ",
        ),
        modelIdentity(
          job.backend,
          job.model,
          [job.backend, job.model].filter(Boolean).join(" · ") ||
            HarnessI18n.t("admin.dashboard.runner_not_provided"),
        ),
        document.createTextNode(
          " · " +
            [
              names[job.state] || job.state,
              job.project || HarnessI18n.t("sidebar.project.none"),
              HarnessI18n.t("admin.dashboard.output_tokens", {
                tokens: format(job.output_tokens),
              }),
            ].join(" · "),
        ),
      );
      if (row.open) refreshExecution(row);
      return row;
    });
    for (const child of [...holder.children])
      if (!rows.includes(child)) child.remove();
    for (const [i, row] of rows.entries())
      if (holder.children[i] !== row)
        holder.insertBefore(row, holder.children[i] || null);
    if (!rows.length)
      holder.replaceChildren(
        element(
          "p",
          HarnessI18n.t("admin.dashboard.empty_recent"),
          "empty-history",
        ),
      );
  } catch (e) {
    $("dashboard-updated").textContent = HarnessI18n.t(
      "admin.dashboard.refresh_failed",
    );
  } finally {
    dashboardLoading = false;
  }
}
refreshDashboard();
setInterval(refreshDashboard, 3000);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) refreshDashboard();
});

async function refreshExecution(row) {
  if (row.dataset.loading) return;
  row.dataset.loading = "1";
  try {
    const data = await request(
      "dashboard?job=" + encodeURIComponent(row.dataset.job),
    );
    if (!data || data.available === false) throw Error("execution_unavailable");
    const detail = row.lastElementChild;
    const signature = JSON.stringify(data);
    if (detail.dataset.signature === signature) return;
    detail.dataset.signature = signature;
    const events = data.events || [];
    const answer =
      data.answer ||
      events
        .filter((e) => e.type === "answer_delta")
        .map((e) => e.data.text || "")
        .join("");
    const nodes = [];
    for (const [title, value] of [
      [HarnessI18n.t("admin.execution.prompt"), data.prompt],
      [
        HarnessI18n.t("admin.execution.response"),
        answer || HarnessI18n.t("admin.execution.not_received"),
      ],
    ]) {
      nodes.push(element("h4", title), element("pre", value, "execution-text"));
    }
    if (data.metrics) {
      nodes.push(
        element("h4", HarnessI18n.t("admin.execution.section_metrics")),
        describeExecutionData(data.metrics),
      );
    }
    const agents = events.filter(
      (e) => e.type === "maestro_step" || e.type === "maestro_planning",
    );
    if (agents.length || data.orchestration) {
      nodes.push(
        element("h4", HarnessI18n.t("admin.execution.section_agents")),
        describeExecutionData(data.orchestration || agents.map((e) => e.data)),
      );
    }
    if (data.spans?.length) {
      nodes.push(element("h4", HarnessI18n.t("admin.execution.section_spans")));
      const spans = element("ol", undefined, "execution-spans");
      for (const span of data.spans) {
        const item = element("li");
        const outcome = span.attrs?.outcome || "unknown";
        const status =
          outcome === "unknown"
            ? span.end_ts == null
              ? span.start_ts == null
                ? HarnessI18n.t("admin.execution.status_pending")
                : HarnessI18n.t("admin.execution.status_in_progress")
              : HarnessI18n.t("admin.execution.status_unknown")
            : outcome;
        const duration =
          Number.isFinite(span.start_ts) && Number.isFinite(span.end_ts)
            ? HarnessI18n.t("admin.execution.span_seconds", {
                seconds: Math.max(0, span.end_ts - span.start_ts).toFixed(1),
              })
            : "";
        item.append(
          element("strong", span.name || span.kind),
          document.createTextNode(
            HarnessI18n.t("admin.execution.span_line", {
              kind: span.kind,
              status,
              duration,
            }),
          ),
        );
        spans.append(item);
      }
      nodes.push(spans);
    }
    nodes.push(
      element("h4", HarnessI18n.t("admin.execution.section_activity")),
    );
    const activity = element("ol", undefined, "execution-events");
    const labels = {
      thinking: HarnessI18n.t("admin.execution.event_thinking"),
      reasoning_summary: HarnessI18n.t(
        "admin.execution.event_reasoning_summary",
      ),
      tool_start: HarnessI18n.t("admin.execution.event_tool_start"),
      tool_end: HarnessI18n.t("admin.execution.event_tool_end"),
      context_usage: HarnessI18n.t("admin.execution.event_context_usage"),
      maestro_planning: HarnessI18n.t("admin.execution.event_planning"),
      maestro_step: HarnessI18n.t("admin.execution.event_agent_step"),
      completed: HarnessI18n.t("admin.dashboard.state_completed"),
      failed: HarnessI18n.t("admin.dashboard.state_failed"),
      cancelled: HarnessI18n.t("admin.dashboard.state_cancelled"),
      running: HarnessI18n.t("admin.dashboard.state_running"),
      queued: HarnessI18n.t("admin.dashboard.state_queued"),
    };
    for (const event of events
      .filter((e) => !["answer_delta", "reasoning_delta"].includes(e.type))
      .slice(-50)) {
      const li = element("li");
      li.append(
        element(
          "strong",
          new Date(event.time * 1000).toLocaleTimeString("en-US") +
            " · " +
            (labels[event.type] || event.type),
        ),
      );
      if (!["completed", "quota_before", "quota_after"].includes(event.type))
        li.append(describeExecutionData(event.data));
      activity.append(li);
    }
    nodes.push(activity);
    detail.replaceChildren(...nodes);
  } catch (e) {
    row.lastElementChild.textContent = HarnessI18n.t(
      "admin.execution.query_failed",
    );
  } finally {
    delete row.dataset.loading;
  }
}

function describeExecutionData(value) {
  const labels = {
    input_tokens: HarnessI18n.t("admin.execution.field_input_tokens"),
    output_tokens: HarnessI18n.t("admin.execution.field_output_tokens"),
    cached_tokens: HarnessI18n.t("admin.execution.field_cached_tokens"),
    thinking_tokens: HarnessI18n.t("admin.execution.field_thinking_tokens"),
    inference_seconds: HarnessI18n.t("admin.execution.field_run_time"),
    total_seconds: HarnessI18n.t("admin.execution.field_duration"),
    backend: HarnessI18n.t("composer.provider.fallback"),
    model: HarnessI18n.t("admin.execution.field_model"),
    effort: HarnessI18n.t("admin.execution.field_effort"),
    role: HarnessI18n.t("admin.execution.field_role"),
    steps: HarnessI18n.t("admin.execution.field_steps"),
    metrics: HarnessI18n.t("admin.execution.field_metrics"),
    text: HarnessI18n.t("admin.execution.field_message"),
    tool: HarnessI18n.t("admin.execution.field_tool"),
    index: HarnessI18n.t("admin.execution.field_step"),
    status: HarnessI18n.t("admin.execution.field_status"),
    answer: HarnessI18n.t("admin.execution.response"),
    context_usage: HarnessI18n.t("admin.execution.field_context"),
    result: HarnessI18n.t("admin.execution.field_result"),
    prompt: HarnessI18n.t("admin.execution.field_instruction"),
  };
  const box = element("div", undefined, "observed-data");
  if (value === null || typeof value !== "object") {
    box.textContent =
      value == null
        ? HarnessI18n.t("admin.dashboard.not_provided")
        : String(value);
    return box;
  }
  for (const [key, item] of Object.entries(value)) {
    const group = element("div", undefined, "observed-field");
    group.append(element("strong", labels[key] || key.replaceAll("_", " ")));
    if (item !== null && typeof item === "object")
      group.append(describeExecutionData(item));
    else
      group.append(
        element(
          "span",
          item == null
            ? HarnessI18n.t("admin.dashboard.not_provided")
            : String(item),
        ),
      );
    box.append(group);
  }
  return box;
}

function showStep(value) {
  showStepBase(value === 1 ? 0 : value);
  renderPanel();
  const engine = integrationEngine(),
    integrating = step === 3 || step === 4;
  $("projects").hidden = true;
  $("inspector-tabs").hidden = !wizard || !editing;
  for (const button of $("inspector-tabs").children)
    button.hidden = button.dataset.inspectorStep !== "0" && !engine;
  $("integrations").hidden = !wizard || !integrating || !engine;
  if (editing === "local") {
    $("local-model-permissions").hidden = step !== 0;
  }
  if (!wizard || !engine || !integrating) {
    if (wizard)
      $("wizard-progress").textContent =
        step === 0
          ? HarnessI18n.t("admin.wizard.step_models")
          : HarnessI18n.t("admin.wizard.step_review");
    return;
  }
  const plugins = integrationKind() === "plugin",
    title = plugins
      ? HarnessI18n.t("customize.chip.plugins")
      : HarnessI18n.t("admin.integrations.title_connectors");
  $("integration-provider").value = engine;
  $("integration-provider").disabled = true;
  $("integrations")
    .querySelector("h2")
    .replaceChildren(
      providerIcon(editing),
      document.createTextNode(
        title +
          " · " +
          providerName(visibleProviders().find((item) => item.id === editing)),
      ),
    );
  $("integration-help").textContent = plugins
    ? HarnessI18n.t("admin.integrations.help_plugins", {
        engine: engine === "claude" ? "Claude Code" : "Codex",
      })
    : HarnessI18n.t("admin.integrations.help_connectors");
  const options = plugins
    ? [
        [
          "plugin_install",
          HarnessI18n.t("admin.integrations.action_plugin_install"),
        ],
        [
          "plugin_remove",
          HarnessI18n.t("admin.integrations.action_plugin_remove"),
        ],
      ]
    : [
        [
          "connector_add",
          HarnessI18n.t("admin.integrations.action_connector_add"),
        ],
        ["login", HarnessI18n.t("admin.integrations.action_login")],
        [
          "connector_remove",
          HarnessI18n.t("admin.integrations.action_connector_remove"),
        ],
      ];
  const previous = $("integration-action").value;
  $("integration-action").replaceChildren(
    ...options.map(([value, label]) => new Option(label, value)),
  );
  if (options.some(([value]) => value === previous))
    $("integration-action").value = previous;
  renderIntegrationForm();
  renderIntegrationCliGuide();
  if (integrationCatalogs.has(engine)) renderCatalog();
  else loadCatalog();
  $("wizard-progress").textContent = HarnessI18n.t(
    "admin.wizard.progress_manage",
    { title },
  );
  $("wizard-next").hidden = true;
  $("wizard-back").hidden = false;
}

function renderIntegrationCliGuide() {
  const host = $("integrations").querySelector(".panel-body");
  if (!host) return;
  $("integration-cli-guide")?.remove();
  const guide = element("details", undefined, "advanced integration-cli-guide");
  guide.id = "integration-cli-guide";
  guide.append(
    element(
      "summary",
      integrationKind() === "plugin"
        ? HarnessI18n.t("admin.integrations.cli_summary_plugins")
        : HarnessI18n.t("admin.integrations.cli_summary_mcp"),
    ),
    element("p", HarnessI18n.t("admin.integrations.cli_hint"), "hint"),
  );
  const providers = [
    {
      id: "codex",
      name: "Codex",
      commands: [
        "codex mcp list",
        "codex mcp add <name> --url <https-url>",
        "codex plugin list --available --json",
        "codex plugin add <plugin@marketplace>",
      ],
    },
    {
      id: "claude",
      name: "Claude Code",
      commands: [
        "claude mcp list",
        "claude mcp add --transport http <name> <https-url>",
        "claude plugin list --available --json",
        "claude plugin install <plugin@marketplace>",
      ],
    },
  ];
  for (const provider of providers.filter(
    (item) => item.id === integrationEngine(),
  )) {
    const section = element("section", undefined, "integration-cli-provider"),
      heading = element("h3"),
      logo = element("span", undefined, "provider-logo");
    logo.append(providerIcon(provider.id));
    heading.append(logo, document.createTextNode(provider.name));
    section.append(heading);
    for (const command of provider.commands.filter((command) =>
      command.includes(integrationKind() === "plugin" ? " plugin " : " mcp "),
    )) {
      const code = element("code", command);
      section.append(code);
    }
    guide.append(section);
  }
  host.append(guide);
}
const integrationCatalogs = new Map(),
  catalogPending = new Set();
// The admin answers 429 to a second operation, so every catalog read (Providers and Plugins pages) goes through this one chain.
let catalogQueue = Promise.resolve();
function requestCatalog(provider) {
  const run = catalogQueue.then(() =>
    request("integration-catalog", { provider }),
  );
  catalogQueue = run.catch(() => {});
  return run;
}
let catalogVisibleCount = 40,
  catalogViewKey = "";
function renderCatalog() {
  const provider = $("integration-provider").value,
    data = integrationCatalogs.get(provider),
    query = $("catalog-search").value.trim().toLocaleLowerCase();
  const viewKey = JSON.stringify([provider, integrationKind(), query]);
  if (viewKey !== catalogViewKey) {
    catalogVisibleCount = 40;
    catalogViewKey = viewKey;
  }
  const items = (data?.items || []).filter(
    (item) =>
      item.kind === integrationKind() &&
      (item.name + " " + item.id + " " + (item.description || ""))
        .toLocaleLowerCase()
        .includes(query),
  );
  $("catalog-refresh").disabled = catalogPending.has(provider);
  $("catalog-items").setAttribute(
    "aria-busy",
    String(catalogPending.has(provider)),
  );
  $("catalog-status").textContent = catalogPending.has(provider)
    ? HarnessI18n.t("admin.integrations.catalog_searching", {
        provider: provider === "claude" ? "Claude Code" : "Codex",
      })
    : data?.error
      ? HarnessI18n.t(
          data.items?.length
            ? "admin.integrations.catalog_error_cached"
            : "admin.integrations.catalog_error",
          { error: data.error },
        )
      : data
        ? HarnessI18n.t("admin.integrations.catalog_count", {
            count: items.length,
            warnings: (data.warnings || []).join(" "),
          })
        : HarnessI18n.t("admin.integrations.catalog_idle");
  $("catalog-items").replaceChildren(
    ...items.slice(0, catalogVisibleCount).map((item) => {
      const card = element("article", undefined, "catalog-item"),
        title = element("strong");
      title.append(
        connectorIcon(item),
        document.createTextNode(connectorLabel(item)),
      );
      const detail = element("div");
      detail.append(
        title,
        element("small", item.id.replace(/^(plugin|mcp):/, ""), "subtle"),
      );
      detail.append(element("p", integrationDescription(item), "hint"));
      detail.append(
        element(
          "small",
          HarnessI18n.t(
            item.kind === "plugin"
              ? "admin.integrations.kind_plugin"
              : "admin.integrations.kind_mcp",
          ),
          "subtle",
        ),
      );
      card.append(
        detail,
        element(
          "span",
          HarnessI18n.t(
            item.status === "installed"
              ? "admin.integrations.status_installed"
              : item.status === "configured"
                ? "admin.integrations.status_configured"
                : "admin.integrations.status_available",
          ),
          "pill",
        ),
      );
      if (item.kind === "plugin" && item.status === "available") {
        const install = element(
          "button",
          HarnessI18n.t("customize.plugin.install"),
          "button secondary",
        );
        install.dataset.panelHelp = "admin.buttons.download";
        install.setAttribute(
          "aria-label",
          HarnessI18n.t("customize.card.install", { label: item.name }),
        );
        install.onclick = () =>
          action(async () => {
            await request("integration", {
              provider,
              action: "plugin_install",
              name: item.id.replace(/^plugin:/, ""),
            });
            say(HarnessI18n.t("admin.integrations.install_started"));
            pollOperations();
          });
        card.append(install);
      }
      return card;
    }),
  );
  if (data && !data.error && !items.length)
    $("catalog-items").append(
      element("p", HarnessI18n.t("admin.integrations.catalog_empty"), "hint"),
    );
  $("catalog-more").hidden = items.length <= catalogVisibleCount;
  $("catalog-count").textContent = items.length
    ? HarnessI18n.t("admin.integrations.catalog_showing", {
        shown: Math.min(catalogVisibleCount, items.length),
        total: items.length,
      })
    : "";
}
async function loadCatalog() {
  const provider = $("integration-provider").value;
  if (catalogPending.has(provider)) return;
  catalogPending.add(provider);
  renderCatalog();
  try {
    integrationCatalogs.set(provider, await requestCatalog(provider));
  } catch (error) {
    integrationCatalogs.set(provider, {
      ...(integrationCatalogs.get(provider) || { items: [] }),
      error: error.message,
    });
  } finally {
    catalogPending.delete(provider);
    renderCatalog();
  }
}
function createCatalog() {
  const host = $("integrations").querySelector(".panel-body"),
    section = element("section", undefined, "integration-catalog");
  const heading = element(
      "h3",
      HarnessI18n.t("admin.integrations.catalog_heading"),
    ),
    refresh = element(
      "button",
      HarnessI18n.t("admin.integrations.check_catalogs"),
      "button secondary",
    );
  refresh.id = "catalog-refresh";
  refresh.dataset.panelHelp = "admin.buttons.check";
  refresh.onclick = () => loadCatalog();
  const label = element(
      "label",
      HarnessI18n.t("admin.integrations.catalog_search_label"),
    ),
    search = element("input");
  search.id = "catalog-search";
  search.type = "search";
  search.placeholder = HarnessI18n.t(
    "admin.integrations.catalog_search_placeholder",
  );
  search.oninput = renderCatalog;
  label.append(search);
  const status = element("p", undefined, "hint");
  status.id = "catalog-status";
  status.setAttribute("role", "status");
  const items = element("div");
  items.id = "catalog-items";
  const count = element("p", undefined, "hint");
  count.id = "catalog-count";
  count.setAttribute("role", "status");
  const more = element(
    "button",
    HarnessI18n.t("admin.integrations.load_more"),
    "button secondary",
  );
  more.id = "catalog-more";
  more.dataset.panelHelp = "admin.buttons.continue";
  more.hidden = true;
  more.onclick = () => {
    catalogVisibleCount += 40;
    renderCatalog();
  };
  section.append(heading, refresh, label, items, count, more);
  host.prepend(section);
  $("integrations").prepend($("integrations").querySelector("h2"), status);
}
createCatalog();

// Help stays visible and is associated with its control for assistive technology.
const settingHelp = {
  "hardware-model": HarnessI18n.t("admin.help.hardware_model"),
  "profile-binary": HarnessI18n.t("admin.help.profile_binary"),
  "profile-description": HarnessI18n.t("admin.help.profile_description"),
  "integration-provider": HarnessI18n.t("admin.help.integration_provider"),
  "integration-action": HarnessI18n.t("admin.help.integration_action"),
  "integration-name": HarnessI18n.t("admin.help.integration_name"),
  "integration-transport": HarnessI18n.t("admin.help.integration_transport"),
  "integration-source": HarnessI18n.t("admin.help.integration_source"),
  "install-model": HarnessI18n.t("admin.help.install_model"),
  "install-runtime": HarnessI18n.t("admin.help.install_runtime"),
  "model-folder": HarnessI18n.t("admin.help.model_folder"),
  "local-file": HarnessI18n.t("admin.help.local_file"),
  "import-settings": HarnessI18n.t("admin.help.import_settings"),
  "mcp-default-model": HarnessI18n.t("admin.help.mcp_default_model"),
  "mcp-default-effort": HarnessI18n.t("admin.help.mcp_default_effort"),
};
for (const [id, help] of Object.entries(settingHelp)) fieldHelp($(id), help);

// Only connection creation needs a transport and server address.
function renderIntegrationForm() {
  const action = $("integration-action").value,
    connecting = action === "connector_add";
  for (const id of ["integration-transport", "integration-source"])
    $(id).closest("label").hidden = !connecting;
  $("integration-name").placeholder = action.startsWith("plugin_")
    ? "plugin@marketplace"
    : HarnessI18n.t("admin.integrations.name_placeholder");
  const local = $("integration-transport").value === "stdio";
  $("integration-source").closest("label").firstChild.textContent = local
    ? HarnessI18n.t("admin.integrations.source_command_label")
    : HarnessI18n.t("admin.integrations.source_url_label");
  $("integration-source").placeholder = local
    ? '["program", "argument"]'
    : "https://your-mcp-server/mcp";
}
$("integration-action").onchange = renderIntegrationForm;
$("integration-transport").onchange = renderIntegrationForm;
renderIntegrationForm();

// One action vocabulary keeps dynamic and static panel buttons consistent.
const buttonActions = [
  [/^Pin catalog/, "lock", "admin.buttons.pin_catalog"],
  [/^Provision runtime/, "download", "admin.buttons.provision_runtime"],
  [/^Re-trust hooks/, "shield", "admin.buttons.re_trust_hooks"],
  [/^Preview update/, "search", "admin.buttons.preview_update"],
  [/^Move pin/, "check", "admin.buttons.move_pin"],
  [/^Remove binding/, "trash", "admin.buttons.remove_binding"],
  [/^(Save|Complete and save|Apply)/, "device-floppy", "admin.buttons.save"],
  [/^(Edit)/, "pencil", "admin.buttons.edit"],
  [/^(Delete)/, "trash", "admin.buttons.delete"],
  [/^(Remove)/, "trash", "admin.buttons.remove"],
  [/^(Log in)/, "login", "admin.buttons.log_in"],
  [/^(Check|Detect|Query|Search)/, "scan", "admin.buttons.check"],
  [/^(Refresh|Retry)/, "refresh", "admin.buttons.refresh"],
  [/^(Add|Create)/, "plus", "admin.buttons.add"],
  [/^(Cancel|Close)/, "x", "admin.buttons.cancel"],
  [/^(Back|Choose another)/, "chevron-left", "admin.buttons.back"],
  [/^(Continue|Load more)/, "chevron-right", "admin.buttons.continue"],
  [/^(Download|Install)/, "download", "admin.buttons.download"],
  [/^Export/, "download", "admin.buttons.export"],
  [/^Import or export/, "adjustments", "admin.buttons.import_export"],
  [
    /^(Use current configuration|Copy configuration|Copy the running configuration)/,
    "copy",
    "admin.buttons.copy_configuration",
  ],
  [
    /^Use (file|an already downloaded file)/,
    "folder",
    "admin.buttons.use_file",
  ],
  [/^Use this folder/, "check", "admin.buttons.use_folder"],
  [/^Configure this file/, "adjustments", "admin.buttons.configure_file"],
  [/^Start/, "player-play", "admin.buttons.start"],
  [/^Run operation/, "player-play", "admin.buttons.run_operation"],
  [/^Operations in progress/, "list", "admin.buttons.operations"],
  [
    /^(Choose (models )?folder|Home folder|Parent folder)/,
    "folder",
    "admin.buttons.folder_nav",
  ],
  [/^(Model and hardware)/, "adjustments", "admin.buttons.model_hardware"],
  [/^Permissions/, "isolation", "admin.buttons.permissions"],
  [/^(Plugins|Connectors|Connection)/, "plug", "admin.buttons.connections"],
  [/^(Settings|Appearance)/, "settings", "admin.buttons.settings"],
  [/^Run setup again/, "settings", "admin.buttons.run_setup"],
  [/^Scan again/, "scan", "admin.buttons.scan_again"],
  [/^(Next|Finish|Skip setup)/, "player-play", "admin.buttons.first_run_next"],
];
function decoratePanelButtons() {
  for (const button of document.querySelectorAll("button")) {
    const label = (
      button.getAttribute("aria-label") || button.textContent
    ).trim();
    // The patterns are English: match the English text of a translated label.
    const englishLabel = HarnessI18n.source(label);
    const action = buttonActions.find(([pattern, , helpKey]) =>
      button.dataset.panelHelp
        ? button.dataset.panelHelp === helpKey
        : pattern.test(englishLabel),
    );
    const folder = button.closest(
      "#folder-picker-breadcrumb,#folder-picker-list",
    );
    const theme = button.dataset.themeChoice;
    const provider = button.classList.contains("discovery-option");
    const name = folder ? "folder" : theme ? "palette" : action?.[1];
    const explanation = folder
      ? HarnessI18n.t("admin.buttons.folder_explanation", {
          target: button.title || label,
        })
      : theme
        ? HarnessI18n.t("admin.buttons.theme_explanation", {
            theme: button.querySelector(".theme-name")?.textContent || theme,
          })
        : provider
          ? HarnessI18n.t("admin.buttons.provider_explanation")
          : action && HarnessI18n.t(action[2]);
    if (!button.querySelector("svg,.provider-mark") && name)
      button.prepend(icon(name));
    if (explanation && !button.title) button.title = explanation;
  }
}
decoratePanelButtons();
new MutationObserver(decoratePanelButtons).observe(document.body, {
  childList: true,
  subtree: true,
});
