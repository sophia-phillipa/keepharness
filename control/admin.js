"use strict";
const $ = (id) => document.getElementById(id);
let state,
  settings,
  working = false,
  wizard = false,
  editing = null,
  unsaved = false;
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
    $("operation-message").textContent = "Starting operation…";
    $("operation-retry").hidden = true;
    activeOperation = null;
    $("operations").replaceChildren();
    $("operations-panel").hidden = true;
  }
  try {
    const result = await requestRaw(path, data);
    if (starts) {
      activeOperation = { path, data, id: result.id };
      $("operation-message").textContent =
        "Operation started. Track progress below.";
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
        ? "The server took too long to respond. The operation may still be running on the server; check its status before trying again."
        : "Could not connect to the server. Check that it is running and try again.",
    );
  }
  let value;
  try {
    value = await r.json();
  } catch {
    throw Error(
      "The server returned an unexpected response (" +
        r.status +
        "). Try again after checking the server.",
    );
  }
  if (!r.ok) {
    const sentence =
      {
        model_not_available:
          "The selected model is not available. Check the provider models and choose again.",
        authentication_required: "Authenticate your account and check again.",
        permission_denied:
          "This action is not permitted. Review the provider permissions.",
        operation_failed:
          "The server could not complete this action. Check the server log for details.",
        invalid_request:
          "The request is missing required data. Check the fields and try again.",
        invalid_json: "The request could not be read. Reload the page and try again.",
        provider_state_conflict:
          "The provider changed since this page loaded. Review the refreshed state and try again.",
        provider_state_write_unsupported:
          "KeepHarness cannot change this item in the provider.",
        provider_state_version_untested:
          "This provider version has not been tested for this change.",
        provider_state_validation_failed:
          "The provider rejected the change as invalid.",
        provider_state_unreadable: "The provider state could not be read.",
        provider_command_failed: "The provider command failed.",
        provider_unknown: "There is no state adapter for this provider yet.",
        project_unknown: "This project is not registered.",
      }[value.error] ||
      value.error ||
      "Could not complete this action. Check the data and try again.";
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
  $("dirty").textContent = "Unsaved changes";
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
const accountAppStatusLabels = {
  connected: "Connected",
  needs_authentication: "Needs authentication",
  failed: "Failed",
  unknown: "Status unknown",
};
function accountAppRow(item) {
  const row = element("div", undefined, "toggle-row account-app-row"),
    span = element("span"),
    statusLabel =
      accountAppStatusLabels[item.status] || accountAppStatusLabels.unknown;
  span.append(element("strong", connectorLabel(item)));
  span.append(
    element("small", "Claude account connector · not selectable here"),
  );
  row.append(span);
  const status = element(
    "span",
    statusLabel,
    "connector-status connector-status-" + (item.status || "unknown"),
  );
  status.setAttribute("role", "status");
  row.append(status);
  return row;
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
  span.append(document.createTextNode(label || "Model not provided"));
  return span;
}

// What to do next, per provider: Codex shows a link (and, on a host without a browser, a code);
// only Claude Code asks for a code to be pasted back.
const LOGIN_STEPS = {
  codex:
    "Finish the sign-in in the browser. If the operation window shows a link and a code instead, open the link and enter the code there; then click Check account.",
  claude:
    "Sign in in the browser, paste the code it shows into the operation window (Send code), then wait for the account status to update.",
};
// The one-time code Codex prints for device sign-in, shown on its own so it can be read and copied.
const DEVICE_CODE = /\b[A-Z0-9]{4}-[A-Z0-9]{4,6}\b/;
const DEVICE_CODE_NOTE =
  "Device code sign-in must be turned on in your ChatGPT security settings (for a workspace: in its permissions) before this code works.";
function operationTitle(job) {
  return job.kind === "provider-login" && job.provider
    ? providerName({ id: job.provider, name: job.provider }) + " sign-in"
    : "Operation " + job.id.slice(0, 6);
}
function providerLoginButton(info) {
  const button = element("button", "Log in / Renew access", "button secondary");
  button.setAttribute(
    "aria-label",
    "Log in or renew access — " + providerName(info),
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
  const button = element("button", "Check account", "button secondary");
  button.setAttribute("aria-label", "Check account — " + providerName(info));
  button.disabled = !info.found;
  button.onclick = () =>
    action(async () => {
      const data = await request("check", { provider: info.id });
      state.authentication[info.id] = data.authenticated;
      state.models[info.id] = data.models;
      renderProviders();
      say(
        data.authenticated
          ? "Account verified."
          : "Your access needs to be renewed. Click Log in / Renew access.",
      );
    });
  return button;
}

const effortLabels = {
  configured: "Provider setting",
  low: "Low",
  medium: "Medium",
  high: "High",
  xhigh: "Very high",
  max: "Maximum",
  ultra: "Ultra",
  none: "No reasoning",
};
function modelDescription(provider, model) {
  const descriptions = {
    "gpt-6-astra": "Model for complex tasks that require deeper analysis.",
    "gpt-5.6-sol": "General-purpose model for coding and everyday work.",
    "gpt-5.6-terra": "Balanced model for everyday coding tasks.",
    "gpt-5.6-luna": "Model focused on speed and efficiency for coding tasks.",
    "gpt-5.5": "Previous-generation model for coding and general tasks.",
  };
  const efforts = state.models[provider]?.[model] || [];
  return (
    (descriptions[model] ||
      "Model offered by your account. The provider does not provide a description in this panel.") +
    (efforts.filter((e) => e !== "configured").length
      ? " Available efforts: " +
        efforts
          .filter((e) => e !== "configured")
          .map((e) => effortLabels[e] || e)
          .join(", ") +
        "."
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
  panel.setAttribute("aria-label", "Model server on your network");
  const urlLabel = element("label", "Server address"),
    keyLabel = element("label", "API key (optional)"),
    url = element("input"),
    key = element("input");
  url.type = "url";
  url.autocomplete = "off";
  url.placeholder = "http://machine.tailnet.ts.net:8080";
  key.type = "password";
  key.autocomplete = "off";
  urlLabel.append(url);
  keyLabel.append(key);
  const add = element("button", "Add server", "button secondary");
  add.onclick = () =>
    action(async () => {
      if (!url.value.trim()) throw Error("Enter the server address first.");
      const result = await request("remote-model-add", {
        url: url.value.trim(),
        key: key.value.trim(),
      });
      key.value = "";
      await refreshInventory();
      say(
        "Server added · " +
          result.models.length +
          (result.models.length === 1 ? " model" : " models") +
          " found. Choose the ones to make available.",
      );
    });
  const saved = element("div", undefined, "remote-server-list");
  for (const server of info.remote_servers || []) {
    const row = element("p", undefined, "remote-server-row"),
      name = element("span", server.url + (server.has_key ? " · key saved" : "")),
      status = element(
        "span",
        server.reachable
          ? server.models.length + (server.models.length === 1 ? " model" : " models")
          : "Unreachable",
        "connector-status connector-status-" +
          (server.reachable ? "connected" : "failed"),
      ),
      remove = element("button", "Remove", "button secondary");
    if (server.error) name.append(element("small", server.error));
    status.setAttribute("role", "status");
    remove.setAttribute("aria-label", "Remove " + server.url);
    remove.onclick = () =>
      action(async () => {
        if (!confirm("Remove " + server.url + "? Its saved API key is deleted too."))
          return;
        await request("remote-model-remove", { url: server.url });
        await refreshInventory();
        say("Network server removed.");
      });
    row.append(name, status, remove);
    saved.append(row);
  }
  panel.append(
    element("h4", "Model server on your network"),
    element(
      "p",
      "Use a model served by another machine (llama.cpp, Ollama, LM Studio...). The machine must be reachable over Tailscale or your local network, and its OpenAI-compatible server must listen on a network address. The API key is kept in a private file on this computer.",
      "hint",
    ),
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
        ? "Local inference · Codex agent"
        : id === "deepseek"
          ? "Your key · DeepSeek credits · Codex agent"
          : "Local CLI · cloud inference",
    ),
  );
  top.append(
    title,
    element(
      "span",
      info.api
        ? "API · own key"
        : info.found
          ? "Found on this machine"
          : "Not found",
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
          ? "● Server available"
          : "● Authenticated"
        : info.credential_present
          ? "Credential found · verify"
          : id === "local"
            ? "Server not verified yet"
            : "Login not verified",
    ),
  );
  const check = element(
    "button",
    id === "local" ? "Check models" : "Check account",
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
            ? "Server verified. Choose the models you want to make available."
            : "Account verified. Choose the models you want to make available."
          : id === "local"
            ? "The local server did not respond. Start the model below and click Check models."
            : "First, click Log in and complete login in the browser. Then return to the panel and click Check account.",
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
        ? "The model on your local server is what responds."
        : "The DeepSeek API model is what responds, using your DeepSeek credits.";
    body.append(
      element(
        "p",
        inference +
          " The Codex CLI is the engine that runs tools and maintains the session; this does not use an OpenAI model. Model and engine are separate choices. In this integration, the available engine is Codex. Other engines, such as Claude Code, depend on a compatible integration and are not yet available for this option.",
        "hint",
      ),
    );
  }
  if (id === "local" && (info.runtimes || []).length) {
    const servers = element("div", undefined, "detected-servers");
    servers.append(element("strong", "Servers found"));
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
    const tokenLabel = element("label", "Your DeepSeek API key (BYOK)"),
      token = element("input");
    token.type = "password";
    token.autocomplete = "off";
    token.placeholder = state.credentials?.deepseek
      ? "Saved key · fill in only to replace it"
      : "Paste your key from the DeepSeek platform";
    tokenLabel.append(token);
    const saveToken = element(
      "button",
      "Save key and verify",
      "button primary",
    );
    saveToken.onclick = () =>
      action(async () => {
        if (!token.value.trim() && !state.credentials?.deepseek)
          throw Error("Paste your DeepSeek API key before verifying.");
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
          throw Error(
            "The key was saved, but authentication was not confirmed. Check the key and try verifying again.",
          );
        state.credentials = { ...state.credentials, deepseek: true };
        renderProviders();
        say(
          "DeepSeek verified. " +
            (result.balance?.balance_infos || [])
              .map((b) => b.currency + " " + b.total_balance)
              .join(" · "),
        );
      });
    body.append(
      tokenLabel,
      saveToken,
      element(
        "p",
        "Saving the key applies the credential immediately on this machine; canceling the setup does not remove it. Conversations, files, and tools sent to the model consume DeepSeek credits.",
        "hint",
      ),
    );
  }

  const label = element("p", "Available models", "field-label");
  body.append(
    label,
    element(
      "p",
      "The provider connects your account; the model is the AI that responds. Check the models available in conversations. Reasoning effort adjusts how much the model analyzes before responding and can increase time and usage.",
      "hint",
    ),
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
            ? "Server identifier: " + m
            : "Full name and quantization not provided by the server."
          : state.authentication[id] === true && !models.includes(m)
            ? "Unavailable in the current catalog. Uncheck it and choose another model."
            : modelDescription(id, m),
      ),
    );
  if (!choices.children.length)
    choices.append(
      element(
        "p",
        id === "local"
          ? "No models available. Search for a local file or install a model below."
          : !info.found
            ? "Install this provider's CLI on this machine and click Check environment."
            : "Click Check account to list the models.",
        "hint",
      ),
    );
  for (const row of choices.querySelectorAll(".toggle-row strong"))
    row.prepend(providerIcon(id));
  body.append(choices);
  if (id === "gemini")
    body.append(
      element(
        "p",
        "Google has ended access to this CLI for individual accounts and AI Pro/Ultra subscriptions. Migration to Antigravity is required; that engine is not yet integrated into this panel.",
        "hint",
      ),
    );
  if (id === "claude")
    body.append(
      element(
        "p",
        "Models are listed by their full versioned id. Actual access depends on your account.",
        "hint",
      ),
    );
  if (id !== "local")
    body.append(
      toggle(
        "Preferred provider",
        settings.default_backend === id,
        (yes) => {
          settings.default_backend = yes ? id : "";
          renderProviders();
        },
        "Used for automatic selection (backend auto). An explicit model choice takes priority.",
      ),
    );
  const advanced = element("details", undefined, "advanced");
  advanced.append(
    element("summary", "Provider access"),
    element(
      "p",
      "Codex, Claude, and DeepSeek run directly on this computer, without a sandbox, with access to folders, files, terminal, and network. Permission controls are exclusive to local models.",
      "hint",
    ),
  );
  body.append(advanced);

  card.append(body);
  const bottom = element("div", undefined, "provider-enabled");
  bottom.append(
    toggle(
      "Make this service available",
      spec.enabled,
      (yes) => (spec.enabled = yes),
      "Makes the selected models available in conversations. Turning it off keeps the setup and the connected account.",
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
          ? "API / BYOK"
          : info.found
            ? "Found on this machine"
            : "Not installed";
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
      element(
        "p",
        "All available providers have already been added. You can edit them in the list.",
        "hint",
      ),
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
        element(
          "p",
          "Operation permissions belong to the model selected above. Projects are set up in the KeepHarness interface and are available to all models.",
          "hint",
        ),
      );
    }
    renderIntegrationSelection();
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
    "Description not provided by the provider."
  );
}
// D48: harness runs turn Codex apps off, so "-remote" plugins (Gmail, Drive, GitHub, Calendar)
// never load; the admin says so instead of letting them be allowed. Mirrors integrations_view.py.
const REMOTE_PLUGIN_NOTE =
  "Not available in KeepHarness runs: this plugin brings its tools as Codex apps, which KeepHarness keeps off. Add an MCP connector for the service instead.";
function appBasedPlugin(provider, item) {
  return (
    ["codex", "deepseek"].includes(provider) &&
    item.kind === "plugin" &&
    /@[^@]*-remote$/.test(item.id)
  );
}
function loadableIntegrations(provider) {
  return (state.integrations?.[provider] || []).filter(
    (item) => !appBasedPlugin(provider, item),
  );
}
function renderIntegrationSelection() {
  const host = $("integration-selection");
  host.replaceChildren();
  if (!integrationEngine()) return;
  const spec = settings.services[editing],
    available = state.integrations?.[editing] || [];
  for (const [kind, title, description] of [
    [
      "mcp",
      "Connectors",
      "An MCP connector gives access to the tools of a service or process. Each connection may require its own authorization.",
    ],
    [
      "plugin",
      "Plugins",
      editing === "claude"
        ? "In Claude Code, plugins are packages that can bundle skills, agents, hooks, and MCP servers."
        : "In Codex, plugins are packages that can bundle skills, apps, and MCP servers.",
    ],
  ]) {
    if (kind !== integrationKind()) continue;
    const section = element("section", undefined, "provider-connectors panel");
    section.append(
      element("h3", title),
      element(
        "p",
        description +
          " The selection below defines what KeepHarness requests from this provider's CLI in conversations. Save to apply.",
        "hint",
      ),
    );
    if (editing === "local")
      section.append(
        element(
          "p",
          "The local model uses an isolated environment: plugins and MCP connectors on the computer are not loaded in this integration. The catalog below belongs to the Codex engine; operations on it affect the shared profile of the engine.",
          "hint",
        ),
      );
    const list = element("div", undefined, "model-list");
    for (const item of available.filter((item) => item.kind === kind)) {
      const row = toggle(
        connectorLabel(item),
        (spec.integrations || []).includes(item.id),
        (yes) => {
          spec.integrations = yes
            ? [...new Set([...(spec.integrations || []), item.id])]
            : (spec.integrations || []).filter((id) => id !== item.id);
        },
        integrationDescription(item) +
          " " +
          (appBasedPlugin(editing, item)
            ? REMOTE_PLUGIN_NOTE
            : kind === "plugin"
              ? "Installed plugin · load in conversations"
              : "Configured connector · make tools available"),
      );
      // An already-allowed remote plugin stays untickable so the owner can remove it.
      if (
        editing === "local" ||
        (appBasedPlugin(editing, item) &&
          !(spec.integrations || []).includes(item.id))
      )
        row.querySelector("input").disabled = true;
      row.querySelector("strong").prepend(connectorIcon(item));
      list.append(row);
    }
    // Claude's own account connectors (claude.ai Gmail, claude.ai Drive, ...)
    // are not something the provider config can turn on or off here: show
    // them as read-only entries with their health status (F-41 UI side).
    if (kind === "mcp")
      for (const item of available.filter(
        (item) => item.kind === "account-app",
      )) {
        list.append(accountAppRow(item));
      }
    if (!list.children.length)
      list.append(
        element(
          "p",
          kind === "plugin"
            ? "No plugins installed for this provider."
            : "No connectors configured for this provider.",
          "hint",
        ),
      );
    section.append(list);
    host.append(section);
  }
}
const connectorIdentity = {
  node_repl: ["JavaScript Terminal", "⌨️"],
  "qwen-local-agent": ["Local Qwen Agent", "🧠"],
  "codex-app-tools": ["Codex Tools", "codex"],
  sites: ["Sites", "🌐"],
  browser: ["Browser", "🧭"],
  chrome: ["Chrome", "🌐"],
  "unified-computer-use": ["Computer Control", "🖥️"],
  visualize: ["Visualizations", "📈"],
  documents: ["Documents", "📝"],
  pdf: ["PDF", "📄"],
  spreadsheets: ["Spreadsheets", "📊"],
  presentations: ["Presentations", "📽️"],
  "template-creator": ["Template Creator", "📐"],
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
    "Integration"
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
const onOff = (enabled) => (enabled ? "on" : "off");
const acked = new Set(); // dismissed notice ids; they embed detected_at, so one never legitimately returns
function setProviderNotices(provider, list) {
  providerNotices.set(provider, Array.isArray(list) ? list.filter((notice) => !acked.has(notice.id)) : []);
}
// The sentence as [text, item id, text]: the id is shown in <code>, the rest is plain text.
function noticeParts(provider, notice) {
  const cli = HarnessUI.providerName(provider);
  const time = new Date(notice.detected_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
  const [kind, id] = String(notice.item_id).includes(":") ? String(notice.item_id).split(/:(.*)/s) : ["item", String(notice.item_id)];
  // Older pending skill notices stored the folder name instead of its source file.
  const source = kind === "skill" && notice.source === id ? "SKILL.md" : notice.source;
  const detail = " (" + source + ", " + time + ")";
  if (notice.change === "reverted")
    return [`Reverted by ${cli}: ${kind} `, id, ` is ${onOff(notice.after)} again${detail}. Your choice was ${onOff(notice.before)}.`];
  const what = { added: "was added", removed: "was removed" }[notice.change] || "was turned " + onOff(notice.after);
  return [`${cli} › ${kind} `, id, ` ${what}${detail}.`];
}
const noticeSentence = (provider, notice) => noticeParts(provider, notice).join("");
const noticeBlocks = () => ["provider-state-notices", "plugins-notices"].map($);
// Redraws both blocks from the store, toasts the ids not announced yet (one toast per batch) and
// tells the Plugins list to redraw its row markers.
function showProviderNotices() {
  const all = [...providerNotices].flatMap(([provider, list]) => list.map((notice) => ({ provider, notice })));
  for (const box of noticeBlocks()) {
    box.hidden = !all.length;
    if (!all.length) {
      box.replaceChildren();
      continue;
    }
    const head = element("div", undefined, "provider-notices-head");
    head.append(element("strong", all.length === 1 ? "Changed outside KeepHarness" : all.length + " changes outside KeepHarness"));
    for (const [provider, list] of providerNotices) {
      if (list.length < 2) continue;
      const dismissAll = element("button", "Dismiss all " + HarnessUI.providerName(provider), "button secondary");
      dismissAll.dataset.testid = "provider-notice-dismiss-all";
      dismissAll.onclick = () => ackProviderNotices(provider, list);
      head.append(dismissAll);
    }
    const items = element("ul", undefined, "provider-notices-list");
    for (const { provider, notice } of all) {
      const [lead, id, tail] = noticeParts(provider, notice);
      const dismiss = element("button", "Dismiss", "button secondary");
      dismiss.setAttribute("aria-label", "Dismiss: " + lead + id + tail);
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
    folded.append(element("summary", "Show each change"), items);
    box.replaceChildren(head, folded);
  }
  // Only the Plugins and Providers screens toast; an id announced elsewhere would be lost, so it waits.
  const fresh = ["#plugins", "#providers"].includes(location.hash) ? all.filter(({ notice }) => !toasted.has(notice.id)) : [];
  fresh.forEach(({ notice }) => toasted.add(notice.id));
  if (fresh.length) {
    const [{ provider, notice }] = fresh;
    const text = noticeSentence(provider, notice);
    HarnessUI.toast(
      fresh.length > 1
        ? fresh.length + " changes were made outside KeepHarness."
        : notice.change === "reverted"
          ? text
          : "Changed outside KeepHarness: " + text,
    );
  }
  document.dispatchEvent(new Event("provider-notices"));
}
// The server accepts at most 100 ids per call; the rest stay listed and can be dismissed again.
function ackProviderNotices(provider, notices) {
  const ids = notices.slice(0, 100).map((notice) => notice.id);
  return action(async () => {
    await request("provider-state/notices:ack", { provider, project_id: NOTICE_SCOPE, notice_ids: ids });
    ids.forEach((id) => acked.add(id));
    setProviderNotices(provider, providerNotices.get(provider));
    showProviderNotices();
    await refreshProviderNotices();
    // The Dismiss button is gone: keep the focus on the screen instead of <body>.
    const screen = onProviders() ? document.querySelector("#overview h1") : $("plugins-panel");
    const next = noticeBlocks().find((box) => !box.hidden && box.offsetParent)?.querySelector("button");
    if (next) return next.focus();
    screen.tabIndex = -1;
    screen.addEventListener("blur", () => screen.removeAttribute("tabindex"), { once: true });
    screen.focus();
  });
}
// Focus and visibilitychange fire together: callers share the read that is already in flight.
let noticesRead = null;
const refreshProviderNotices = () => (noticesRead ||= readProviderNotices().finally(() => (noticesRead = null)));
async function readProviderNotices() {
  await Promise.all(
    ["codex", "claude"].map(async (id) => {
      try {
        setProviderNotices(id, (await request("provider-state?provider=" + id + "&project_id=" + NOTICE_SCOPE)).external_changes);
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
            ? "Pending authorization"
            : s.enabled
              ? "Enabled"
              : "Disabled",
          "pill" +
            (s.enabled && !pendingAuthentication(info.id) ? " good" : ""),
        ),
        element(
          "p",
          s.models.length ? undefined : "No model selected",
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
      const edit = element("button", "Edit", "button secondary");
      edit.setAttribute("aria-label", "Edit " + providerName(info));
      edit.onclick = () => openWizard(info.id);
      const remove = element("button", "Delete", "button secondary");
      remove.onclick = () =>
        action(async () => {
          if (
            !confirm(
              "Remove " +
                providerName(info) +
                " from the panel? The account and CLI will not be uninstalled. The BYOK key for this provider will be deleted.",
            )
          )
            return;
          await request("provider-delete", { provider: info.id });
          await load();
          say("Provider removed.");
        });
      edit.prepend(icon("edit"));
      remove.prepend(icon("trash"));
      actions.append(edit, remove);
      if (["codex", "claude", "gemini"].includes(info.id))
        actions.prepend(providerLoginButton(info), providerCheckButton(info));
      card.append(actions);
      const availability = toggle(
        "Make this service available",
        s.enabled,
        () => {},
        "Turn on to use the models in conversations. Turn off to pause use without deleting the configuration.",
      );
      const checkbox = availability.querySelector("input");
      checkbox.setAttribute(
        "aria-label",
        "Make " + providerName(info) + " available",
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
                ? "Service enabled. Your models are available in conversations."
                : "Service disabled. Your configuration was kept.",
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
        "Your workspace is ready. Add a provider to choose models and permissions.",
        "empty-state",
      ),
    );
}
function openWizard(provider = null) {
  if (
    (unsaved || profileDirty) &&
    !confirm(
      "Discard unsaved changes, including the CPU and GPU profile, and open another provider?",
    )
  )
    return;
  settings = structuredClone(state.settings);
  unsaved = false;
  profileDirty = false;
  $("feedback").hidden = true;
  wizard = true;
  editing = provider;
  if (provider && provider !== "local" && integrationEngine(provider)) {
    const current = state.settings.services[provider];
    if (!current?.added && !current?.enabled && !current?.models?.length)
      settings.services[provider].integrations = loadableIntegrations(
        provider,
      ).map((item) => item.id);
  }
  step = 0;
  renderProviders();
  renderProjects();
  showStep(0);
  $("wizard-title").textContent = provider
    ? "Edit " + providerName(visibleProviders().find((i) => i.id === provider))
    : "Add provider";
  $("provider-dialog").hidden = false;
  renderProfile();
  $("wizard-feedback").append($("feedback"));
  HarnessUI.decorate();
  if (!provider) $("provider-options").querySelector("button")?.focus();
}
function dashboard() {
  if (
    (unsaved || profileDirty) &&
    !confirm("Discard unsaved changes, including the CPU and GPU profile?")
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
    element(
      "p",
      "Add projects in the KeepHarness sidebar. All enabled models are available in the projects you set up.",
      "hint",
    ),
  );
  $("project-count").textContent = settings.projects.length;
}

let tailnetNoticeShown = false;
function renderStatus() {
  const s = state.status;
  const diagnostics = [];
  if (s.startup_error) diagnostics.push("Startup error: " + s.startup_error);
  if (s.last_exit)
    diagnostics.push("Last exit: " + s.last_exit.code + " at " + new Date(s.last_exit.at * 1000).toISOString() + " (uptime " + s.last_exit.uptime_seconds + "s)");
  $("runtime-diagnostics").textContent = diagnostics.join("\n");
  $("runtime-diagnostics").hidden = diagnostics.length === 0;
  $("environment-tools").replaceChildren(...(state.inventory.tools || []).map(tool => {
    const row = element("div");
    row.append(element("h3", tool.name + " — " + (tool.present ? "Available" : "Missing")));
    for (const [distro, hint] of Object.entries(tool.package_hints || {}))
      row.append(element("p", distro + ": " + hint, "hint"));
    return row;
  }));
  for (const provider of state.inventory.services)
    for (const failure of provider.runtime_errors || [])
      $("environment-tools").append(element("p", failure.url + ": " + failure.error, "hint"));
  $("add-provider").disabled = working;
  $("wizard-content-lock").disabled = working;
  $("wizard-next").disabled = working;
  // Persistent, so it is said once per page load and never buries a later message.
  if (s.tailnet_signin_off && !tailnetNoticeShown) {
    tailnetNoticeShown = true;
    say(s.tailnet_signin_off, true);
  }
  $("runtime-badge").textContent = s.running
    ? "● Harness active"
    : "● Harness stopped";
  $("runtime-badge").classList.toggle("good", s.running);
  // The link carries the real address as soon as the status gives it, and works only while the harness runs.
  const open = $("open-harness");
  open.href = (s.shared ? s.remote_url : s.local_url) || "#";
  open.setAttribute("aria-disabled", String(!s.running));
  open.title = s.running ? "Open the harness chat" : "The harness is stopped. Start it from Providers, then open it.";
  $("save").disabled = working;
}
$("open-harness").onclick = (event) => {
  if (event.currentTarget.getAttribute("aria-disabled") === "true") event.preventDefault();
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
  $("platform-note").textContent =
    "Detected platform: " +
    state.inventory.platform +
    ". Isolated mode requires Linux and bubblewrap. Native mode uses the mechanisms of the installed CLI.";
}
async function load({ select = true } = {}) {
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
    $("wizard-title").textContent =
      "Edit " +
      (visibleProviders().find((i) => i.id === editing)?.name || "provider");
  HarnessUI.decorate();
}
$("refresh-log-tail").onclick = async () => {
  const button = $("refresh-log-tail");
  button.disabled = true;
  try {
    const result = await request("logs");
    $("log-tail").textContent = result.lines.join("\n") || "No harness log entries yet.";
  } catch (error) {
    $("log-tail").textContent = "Could not read the log: " + error.message;
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
    trigger?.textContent?.trim() || trigger?.getAttribute("aria-label") || "Load panel";
  if (trigger) trigger.disabled = true;
  $("wizard-content-lock").disabled = true;
  $("config-mcp").inert = true;
  if (state) renderStatus();
  document.body.setAttribute("aria-busy", "true");
  const busy = $("busy-status");
  busy.hidden = false;
  busy.replaceChildren(
    icon("refresh"),
    document.createTextNode(label + " · processing…"),
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
    say("Check complete. No permission was enabled.");
  });
$("save").onclick = () =>
  action(async () => {
    if (profileDirty)
      throw Error(
        "The CPU and GPU profile has pending changes. Use Save changes to this profile before saving the provider.",
      );
    if (!editing) throw Error("Choose a provider.");
    const spec = settings.services[editing],
      catalog = Object.keys(state.models[editing] || {});
    if (spec.enabled && pendingAuthentication(editing))
      throw Error(
        "Pending authorization. Use Log in and Check account, or turn off Make this service available to save only the setup.",
      );
    if (
      spec.enabled &&
      editing !== "local" &&
      state.authentication[editing] === true &&
      spec.models.some((model) => !catalog.includes(model))
    )
      throw Error(
        "There are models unavailable in the current catalog. Uncheck them and choose an available model.",
      );
    settings.services[editing].added = true;
    try {
      await request("settings", collect());
    } catch (e) {
      HarnessUI.toast("Could not save: " + e.message, { error: true });
      throw e;
    }
    unsaved = false;
    $("dirty").textContent = "Settings saved";
    HarnessUI.toast(
      state.status.running
        ? "Settings saved. Updating the active harness."
        : "Settings saved. The harness starts automatically with an enabled model.",
    );
    try {
      const savedProvider = editing;
      await load();
      wizard = false;
      editing = null;
      showStep(0);
      renderDashboard();
      $("dirty").textContent = "Settings saved";
      document
        .querySelector(
          '[data-configured-provider="' +
            savedProvider +
            '"] button[aria-label^="Edit"]',
        )
        ?.focus();
    } catch (e) {
      throw Error(
        "Settings were saved, but the panel could not be refreshed. Reload the page.",
      );
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
    try {
      await request("settings", { ...structuredClone(state.settings), full_access: enabled });
      await load({ select: false });
      say(
        enabled
          ? "Full access is now offered in the chat's access menu."
          : "Full access is no longer offered in the chat's access menu.",
      );
    } finally {
      toggle.checked = state.settings.full_access === true;
      toggle.disabled = false;
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
  $("folder-picker-status").textContent = "Loading folders from the server…";
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
      ? "Showing the first 200 subfolders. Open a folder or provide a more specific path."
      : data.directories.length +
        " subfolder(s). " +
        (!data.directories.length
          ? "This folder has no subfolders. You can choose it."
          : "Open a subfolder or choose the current folder.");
  } catch (e) {
    if (sequence !== folderPickerSequence) return;
    $("folder-picker-status").textContent = "Could not list this folder.";
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
        throw Error(
          'Enter the command as a valid JSON list, for example: ["program", "argument"].',
        );
      }
      if (
        !Array.isArray(data.command) ||
        !data.command.length ||
        data.command.some((value) => typeof value !== "string") ||
        !data.command[0].trim()
      )
        throw Error(
          "Enter the command as a JSON list of strings, starting with the program to run.",
        );
    }
    await request("integration", data);
    say("Operation started. Track the result below.");
    pollOperations();
  });
$("integration-refresh").onclick = () =>
  action(async () => {
    const fresh = await request("state");
    state.integrations = fresh.integrations;
    renderProviders();
    say(
      "Integrations updated. Select them in the connectors and plugins area.",
    );
  });
$("install-model-button").onclick = () =>
  action(async () => {
    await request("model-install", {
      model: $("install-model").value,
      accepted: $("model-consent").checked,
      runtime: $("install-runtime").value,
    });
    say("Download started. Track progress in Operations.");
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
      running: "In progress",
      completed: "Completed",
      failed: "Failed",
      cancelled: "Cancelled",
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
          ? "Could not complete the operation. Try again; if it persists, check the service."
          : j.output;
        const pre = element(
          "pre",
          output?.replace("[gemini_client_retired] ", "") ||
            "Waiting for the CLI…",
        );
        d.append(pre);
        const deviceCode =
          j.provider === "codex" && j.kind === "provider-login"
            ? DEVICE_CODE.exec(j.output || "")?.[0]
            : null;
        if (deviceCode) {
          const code = element("p", "Your code: ", "login-code");
          const value = element("strong", deviceCode);
          value.dataset.testid = "login-code";
          code.append(value);
          d.append(code, element("p", DEVICE_CODE_NOTE, "hint"));
        }
        const urls = new Set((j.output || "").match(/https:\/\/[^\s<>"']+/g));
        for (const match of urls) {
          try {
            const url = new URL(match);
            const link = element(
              "a",
              url.hostname === "antigravity.google"
                ? "Open migration guide in new tab ↗"
                : "Open authorization in new tab ↗",
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
          const label = element("label", "Paste the code Claude shows after you sign in");
          const input = element("input");
          input.id = "operation-code-" + j.id;
          input.type = "password";
          input.autocomplete = "off";
          input.spellcheck = false;
          input.required = true;
          label.htmlFor = input.id;
          const submit = element("button", "Send code", "button primary");
          submit.type = "submit";
          form.append(label, input, submit);
          form.onsubmit = (event) => {
            event.preventDefault();
            action(async () => {
              await request("provider-login-code", { id: j.id, code: input.value.trim() });
              input.value = "";
              say("Code sent. Finishing the login…");
              pollOperations();
            });
          };
          d.append(form);
        }
        if (j.state === "running") {
          const cancel = element("button", "Cancel", "button secondary");
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
        ? "Migration required: this client was discontinued by Google."
        : current.state === "completed" &&
            activeOperation?.path === "provider-login"
          ? activeOperation.data.provider === "claude"
            ? "Login completed. The account status was updated in the panel."
            : "Login completed. Close this window and click Check account."
          : "Operation " +
            (names[current.state] || current.state).toLowerCase() +
            ".";
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
      "Could not refresh operations. We will try again in 5 seconds.",
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
    $("local-files-status").textContent =
      result.files.length + " file(s) found. Select a file to configure.";
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
    say("Search for and select a GGUF file on this machine.", true);
    return;
  }
  if (profileDirty && !confirm("Discard unsaved changes to this profile?"))
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
    if (profileDirty) throw Error("Save this model profile before starting.");
    await request("local-start", { file: profileModel, use_profile: true });
    say(
      "Server starting. Track Operations and refresh the inventory after loading.",
    );
    pollOperations();
  });

const providerDialog = element("dialog");
providerDialog.id = "provider-dialog";
providerDialog.setAttribute("aria-labelledby", "wizard-title");
const workspace = element("div", undefined, "provider-workspace");
$("configured-providers").before(workspace);
const list = element("div", undefined, "provider-list");
const listHeading = element("div", undefined, "provider-list-heading");
listHeading.append(element("h2", "Providers"), $("add-provider"));
list.append(listHeading, $("configured-providers"));
workspace.append(list);
document.body.append(providerDialog);
const emptyInspector = element("section", undefined, "dashboard-live");
emptyInspector.setAttribute("aria-label", "Activity and performance");
emptyInspector.id = "inspector-empty";
$("dashboard").before(emptyInspector);
emptyInspector.innerHTML =
  "<div class=section-heading><div><h2>Real-time operation</h2><p>Last 24 hours · <span id=dashboard-updated>checking</span></p></div></div><div id=dashboard-metrics class=dashboard-metrics></div><section class=card><div class=panel-header><h3>Local server</h3><small>Machine-wide usage</small></div><div id=server-resources class=server-resources></div></section><section class=card><div class=panel-header><h3>Runs · read-only</h3><small id=recent-count></small></div><div id=recent-runs></div></section><p class=hint>Output per second: run average, including tools and time waiting on the provider. It does not represent raw GPU speed. Tokens are only what the services report. Finished runs leave this list after 30 minutes; history is preserved.</p>";
const executionPage = element("section", undefined, "dashboard-live");
executionPage.id = "execution-page";
executionPage.setAttribute("aria-label", "Runs");
executionPage.append($("recent-runs").closest("section"));
const metricHint = emptyInspector.querySelector(".hint");
metricHint.textContent =
  "Output per second: run average, including tools and time waiting on the provider. It does not represent raw GPU speed. Tokens are only what the services report.";
executionPage.append(
  element(
    "p",
    "Finished runs leave this list after 30 minutes; history is preserved.",
    "hint",
  ),
);
emptyInspector.after(executionPage);

const panelCopy = {
  catalogs: ["Catalogs and vault", "Manage pinned resources, prerequisites and private integration bindings."],
  connection: ["Connection / MCP", "Choose the model and effort that connected MCP clients use when they do not name one."],
  home: ["Home", "Track operations and server usage in real time."],
  plugins: ["Plugins", "Manage plugins, skills, and MCPs"],
  providers: [
    "AI Providers",
    "Connect your AI accounts, choose the models available in conversations, and configure access to files, tools, and services.",
  ],
  runs: ["Runs", "Inspect requests, responses, and run events in real time."],
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
  if (onProviders() && !working && document.visibilityState === "visible") refreshProviderNotices();
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
      busyStatus.textContent =
        "Wait for the operation in progress. Other actions will be available once it finishes.";
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
  "Model and hardware",
  "Plugins",
  "Connectors",
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
  element("h3", "Operations in progress and results"),
  operationStatus,
  $("operations"),
);
const operationDialog = element("dialog");
operationDialog.id = "operation-dialog";
operationDialog.setAttribute("aria-labelledby", "operation-title");
const title = element("h2", "Operation");
title.id = "operation-title";
const message = element("p");
message.id = "operation-message";
message.setAttribute("role", "status");
const close = element("button", "Close", "button secondary");
close.onclick = () => operationDialog.close();
const retry = element("button", "Retry", "button primary");
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
  "Operations in progress and results",
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
  ["add-provider", "plus", "Add provider"],
  ["scan", "scan", "Check environment"],
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
    ? "Step " +
      (step + 1) +
      " of 3 · " +
      ["Choose service and models", "Permissions", "Review and finish"][step]
    : "";
  $("wizard-back").hidden = !wizard || (step === 0 && (!editing || existing));
  $("wizard-back").textContent =
    step === 0 ? "Choose another provider" : "Back";
  $("wizard-next").hidden = !wizard || existing || step === 2;
  $("save").hidden = !wizard || (!existing && step !== 2);
  $("save").textContent = existing ? "Save changes" : "Complete and save";
  $("wizard-progress").hidden = existing;
  $("save").title = state.status.running
    ? "Apply changes without stopping the harness."
    : "";
  if (editing) {
    $("provider-review").textContent =
      "When finished, " +
      (visibleProviders().find((i) => i.id === editing)?.name || editing) +
      " will be added to the panel with the selected models.";
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
    say("Choose a provider to continue.", true);
    return;
  }
  if (step === 0 && !settings.services[editing].models.length) {
    const info = visibleProviders().find((i) => i.id === editing);
    say(
      editing === "local"
        ? "Search for or install a local model below; then check and select the model."
        : !info?.found && !info?.api
          ? "Install the CLI on this machine and use Check environment before continuing."
          : "Check the account and select at least one model.",
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
      !confirm(
        "Discard unsaved changes, including the CPU and GPU profile, to add a provider?",
      )
    )
      return;
    unsaved = false;
    profileDirty = false;
    const button = $("add-provider");
    button.disabled = true;
    const label = button.textContent;
    button.textContent = "Checking…";
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
      !confirm("Discard unsaved changes, including the CPU and GPU profile?")
    )
      return;
    const previousProvider = editing;
    profileDirty = false;
    wizard = false;
    editing = null;
    await load({ select: false });
    say("Edit discarded.");
    (
      document.querySelector(
        '[data-configured-provider="' +
          previousProvider +
          '"] button[aria-label^="Edit"]',
      ) || $("add-provider")
    ).focus();
  });
$("manage-network").onclick = () => {
  $("network-feedback").hidden = true;
  $("network").showModal();
};
$("network-close").onclick = () => $("network").close();
const hardwareFields = [
  ["n-gpu-layers", "GPU layers"],
  ["n-cpu-moe", "MoE layers on CPU"],
  ["cpu-range", "Logical CPUs (e.g., 2-7)"],
  ["cpu-range-batch", "Batch CPUs (e.g., 0-7)"],
  ["threads", "Threads"],
  ["threads-batch", "Batch threads"],
  ["ctx-size", "Context size"],
  ["device", "Runtime GPU(s) (e.g., Vulkan0 or CUDA0,CUDA1)"],
  ["main-gpu", "Main GPU (index)"],
  ["split-mode", "GPU split mode (none, layer or row)"],
  ["tensor-split", "GPU split ratio (e.g., 1,1)"],
  ["parallel", "Processing slots"],
  ["flash-attn", "Flash Attention (on, off, auto)"],
  ["cache-type-k", "K cache type"],
  ["cache-type-v", "V cache type"],
  ["cache-ram", "Cache RAM (MiB)"],
  ["reasoning", "Reasoning (on, off, auto)"],
  ["reasoning-format", "Reasoning format"],
  ["reasoning-budget", "Reasoning budget"],
  ["load-mode", "Load mode"],
  ["cpu-strict", "Strict CPU affinity (0 or 1)"],
  ["cpu-strict-batch", "Strict batch affinity (0 or 1)"],
  ["temp", "Temperature"],
  ["top-k", "Top K"],
  ["top-p", "Top P"],
  ["min-p", "Min P"],
  ["repeat-penalty", "Repeat penalty"],
  ["seed", "Random seed"],
];
const profilePanel = $("local-profile-summary").parentElement;
const profilePicker = element("select");
profilePicker.id = "hardware-model";
profilePicker.className = "form-select";
const profileLabel = element("label", "Model for this profile");
profileLabel.htmlFor = "hardware-model";
profileLabel.append(profilePicker);
profilePanel.insertBefore(profileLabel, $("local-profile-summary"));
const editor = element("details", undefined, "advanced");
editor.append(element("summary", "Advanced · running this model"));
const editorGrid = element("div", undefined, "hardware-editor");
const binaryLabel = element("label", "llama-server executable");
const binaryInput = element("input");
binaryInput.id = "profile-binary";
binaryInput.className = "form-control";
binaryLabel.append(binaryInput);
editor.append(binaryLabel);
const descriptionLabel = element(
    "label",
    "Description of this profile (optional)",
  ),
  descriptionInput = element("textarea");
descriptionInput.id = "profile-description";
descriptionInput.className = "form-control";
descriptionInput.rows = 4;
descriptionInput.maxLength = 2000;
descriptionInput.placeholder = "Note the purpose and choices of this profile.";
descriptionLabel.append(descriptionInput);
editor.append(descriptionLabel);
const hardwareHelp = {
  "n-gpu-layers":
    "Number of model layers loaded onto the GPU; 0 uses CPU only.",
  "n-cpu-moe":
    "Mixture-of-experts (MoE) layers kept on the CPU to reduce GPU memory usage.",
  "cpu-range":
    "Logical CPUs used when generating the response. Enter indexes or ranges.",
  "cpu-range-batch": "Logical CPUs used to process the input in batch.",
  threads: "Number of CPU threads used during generation.",
  "threads-batch": "Number of CPU threads used when processing the input.",
  "ctx-size":
    "Context token limit: instructions, history, and response share this space. Larger values require more memory.",
  device:
    "Identifiers of the GPUs the runtime will use. Check Detect GPUs before filling this in.",
  "main-gpu": "Index of the main GPU among the selected devices.",
  "split-mode":
    "none uses a single GPU; layer distributes layers; row distributes operations. Support depends on the runtime.",
  "tensor-split":
    "Distribution ratio across GPUs: 1,1 splits evenly; 1,2 assigns more to the second one.",
  parallel:
    "Number of simultaneous execution slots. More slots may require more memory.",
  "flash-attn":
    "Optimized attention implementation. auto lets the runtime decide based on support.",
  "cache-type-k":
    "Storage format for attention keys. Quantization can reduce memory usage and affect quality.",
  "cache-type-v":
    "Storage format for attention values. Depends on the model and runtime.",
  "cache-ram":
    "Context cache limit in RAM, in MiB. Behavior depends on the runtime.",
  reasoning:
    "Enables, disables, or automates reasoning generation, when supported.",
  "reasoning-format":
    "Format used by the runtime to separate reasoning from the response.",
  "reasoning-budget":
    "Reasoning token budget. Interpretation of special values depends on the runtime.",
  "load-mode": "Weight-loading mode offered by this version of the runtime.",
  "cpu-strict":
    "1 restricts execution to the selected CPUs; 0 allows the scheduler's default behavior.",
  "cpu-strict-batch":
    "Applies the CPU restriction to batch processing as well.",
  temp: "Controls response variability. Lower values tend to produce more predictable choices.",
  "top-k": "Limits sampling to the K most likely tokens.",
  "top-p": "Selects candidates until reaching this cumulative probability.",
  "min-p":
    "Discards tokens whose probability is low relative to the most likely candidate.",
  "repeat-penalty":
    "Reduces the tendency to repeat tokens. 1 applies no penalty.",
  seed: "Seed used for sampling; helps reproduce results under the same conditions.",
};
for (const [key, label] of hardwareFields) {
  const wrap = element("label", label),
    input = element("input");
  input.id = "profile-" + key;
  input.className = "form-control";
  input.placeholder = "Runtime default";
  wrap.append(input);
  fieldHelp(input, hardwareHelp[key] + " Blank: runtime default.");
  editorGrid.append(wrap);
}
const detectDevices = element(
  "button",
  "Detect GPUs and runtime resources",
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
    deviceStatus.textContent = "Querying devices without loading weights…";
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
            ? "Devices reported by the runtime:"
            : "No GPU reported by the runtime. Use 0 layers for CPU.",
        ),
      );
      for (const d of result.devices)
        deviceStatus.append(element("p", d.id + " · " + d.name));
      if (!result.capabilities_verified)
        deviceStatus.append(
          element(
            "p",
            "Could not verify all options for this executable.",
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
  element(
    "p",
    "Choose the identifiers reported by the runtime. With multiple GPUs, separate devices with a comma; the ratio 1,1 splits evenly, 1,2 assigns two parts to the second GPU. Support depends on the runtime. Switching cards requires reviewing this profile before starting.",
    "hint",
  ),
);
editor.append(
  editorGrid,
  element(
    "p",
    "Saving only changes the profile for this weights file. It does not modify the running model or the settings of other models. Empty fields use the runtime default.",
    "hint",
  ),
);
const profileSave = element(
  "button",
  "Save this model profile",
  "button primary",
);
profileSave.id = "profile-save";
profilePanel.append(editor, profileSave);
function markProfileDirty() {
  profileDirty = true;
  profileSave.textContent = "Save changes to this profile";
}
editor.addEventListener("input", markProfileDirty);
const localPermissions = $("local-model-permissions");
localPermissions.append(
  element("h3", "Permissions for this model"),
  element(
    "p",
    "These choices belong to the selected file. They are not inherited by other quantizations or models.",
    "hint",
  ),
);
const toolsLabel = element("label", undefined, "toggle-row"),
  toolsInput = element("input");
toolsInput.type = "checkbox";
toolsInput.id = "profile-tools";
toolsLabel.append(
  toolsInput,
  element("span", "Allow tools · compatibility provided by the administrator"),
);
localPermissions.append(
  toolsLabel,
  element(
    "p",
    "The model and runtime need to support tool calls. This choice is not an automatic validation. Without it, internet, terminal, and folder access are blocked. Text attachments can be used as context.",
    "hint",
  ),
);
const modelPermissionFields = [
  ["read", "Read folders"],
  ["write", "Modify files"],
  ["upload", "Receive attachments"],
  ["internet", "Access internet"],
  ["shell", "Run commands"],
  ["hooks", "CLI hooks"],
];
const permissionHelp = {
  read: "Allows reading files in authorized folders.",
  write: "Allows creating and modifying files in authorized folders.",
  upload: "Allows files sent in the conversation to be used as context.",
  internet: "Allows network access through the available tools.",
  shell: "Allows running commands under the model's isolation conditions.",
  hooks: "Allows the hooks configured in the CLI, when supported.",
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
  element(
    "p",
    "Internet authorizes the agent's network features; it does not provide web search by itself. For local models, access depends on available tools, such as terminal or authorized connectors. Changes will be applied the next time the harness starts.",
    "hint",
  ),
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
  element("h3", "Folders for this model · conversations without a project"),
  element(
    "p",
    "Without a project, the model uses only the folders added here, according to the chosen permissions. Inside a project, the project's folder and permissions add to this model's.",
    "hint",
  ),
);
const modelRootsList = element("div");
modelRootsList.id = "model-roots-list";
const modelRootsAdd = element("button", "Add folder", "button secondary");
modelRootsAdd.id = "model-roots-add";
modelRootsAdd.type = "button";
function renderModelRoots() {
  modelRootsList.replaceChildren();
  for (const root of modelRoots) {
    const row = element("div", undefined, "model-root-row"),
      remove = element("button", "Remove", "button secondary");
    remove.type = "button";
    remove.setAttribute("aria-label", "Remove folder " + root);
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
      element(
        "p",
        "No folder authorized for conversations without a project.",
        "hint",
      ),
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
  if (profileDirty && !confirm("Discard unsaved changes to this profile?")) {
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
            (saved[path] ? " · Saved profile" : " · No saved profile"),
          path,
        ),
    ),
  );
  if (!paths.length)
    profilePicker.append(new Option("Find a GGUF file to configure", ""));
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
        element("strong", value ?? "Not provided"),
      );
      card.append(row);
    }
    return card;
  }
  summary.append(
    section("Model and weights", [
      [
        "Model for this profile",
        profileModel ? profileModel.split("/").pop() : "No file selected",
      ],
      [
        "Saved profile",
        profile.model_file
          ? "Yes · only for this model"
          : "None · does not inherit from another model",
      ],
      [
        "Server verified",
        active
          ? active.runtime + " · " + active.id
          : "This file is not running",
      ],
    ]),
  );
  if (profile.description) {
    const description = element("section", undefined, "hardware-card");
    description.append(
      element("h3", "Profile description"),
      element("p", profile.description),
    );
    summary.append(description);
  }
  const grid = element("div", undefined, "hardware-grid");
  grid.append(
    section("GPU · saved profile", [
      ["Device", p.device],
      ["Requested layers", p["n-gpu-layers"]],
      ["Layers actually loaded", "Not measured"],
      ["VRAM in use", "No telemetry"],
    ]),
    section("CPU · saved profile", [
      ["MoE layers on CPU", p["n-cpu-moe"]],
      ["Logical CPUs", p["cpu-range"]],
      ["Batch CPUs", p["cpu-range-batch"]],
      [
        "Threads / batch",
        [p.threads ?? "—", p["threads-batch"] ?? "—"].join(" / "),
      ],
    ]),
  );
  if (profileModel) summary.append(grid);
  if (active) {
    const perf = active.performance || {},
      running = element("details", undefined, "advanced");
    running.append(
      element("summary", "Parameters of the running process"),
      section("Arguments detected in the process", [
        ["GPU · requested layers", perf["n-gpu-layers"]],
        ["CPU · MoE layers", perf["n-cpu-moe"]],
        ["Logical CPUs", perf["cpu-range"]],
        ["Configured context", perf["ctx-size"]],
      ]),
    );
    summary.append(running);
  }
  summary.append(
    element(
      "p",
      "Saved profile and detected arguments do not prove actual allocation. The profile will be applied the next time this model starts.",
      "hint",
    ),
  );
  if (profileModel) {
    const details = element("details");
    details.append(
      element("summary", "Full weights path"),
      element("p", profileModel, "hint"),
    );
    summary.append(details);
  }
  $("dashboard-profile").textContent =
    Object.keys(saved).length +
    " private profile(s) · settings per weights file";
  $("local-import").hidden = !active;
  $("local-import").textContent =
    "Copy the running configuration of this model";
  editor.hidden = !profileModel || step !== 0;
  profileSave.disabled = !profileModel;
  localPermissions.hidden = step !== 0;
  profilePanel.querySelector("h3").textContent = profileModel
    ? "Model · " + profileModel.split("/").pop()
    : "No model configured";
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
    profileSave.textContent = "Save this model profile";
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
    ? "This model is already running."
    : runtimes.length && !cpuOnly
      ? "Another model is running. To preserve it, use an explicit CPU profile (0 GPU layers), or stop the current server before loading another one on the GPU."
      : profile.model_file
        ? "Ready to start using only the profile for this file."
        : "Configure and save the profile for this file before starting.";
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
      "Profile saved for " +
        profile.model_file.split("/").pop() +
        ". The current process was preserved.",
    );
  });
$("local-import").onclick = () =>
  action(async () => {
    if (
      profileDirty &&
      !confirm(
        "Replace the changes to this profile with the running configuration?",
      )
    )
      return;
    const profile = await request("local-import", { file: profileModel });
    state.local_profiles = { ...profiles(), [profile.model_file]: profile };
    profileDirty = false;
    renderProfile();
    HarnessUI.toast("Configuration copied only for this model.");
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
    say(
      "Saved configuration exported without credentials. Unsaved changes are not included in the file.",
    );
  });
$("import-settings").onchange = () =>
  action(async () => {
    importBundle = null;
    $("import-preview").hidden = true;
    const file = $("import-settings").files[0];
    if (!file) return;
    if (file.size > 60000) throw Error("Configuration file is too large.");
    let candidate;
    try {
      candidate = JSON.parse(await file.text());
    } catch {
      throw Error(
        "The configuration file does not contain valid JSON. Select a file exported by the panel.",
      );
    }
    const preview = await request("settings-import", {
      bundle: candidate,
      apply: false,
    });
    importBundle = candidate;
    $("import-summary").textContent =
      "Replace saved choices: " +
      preview.services.join(", ") +
      " · " +
      preview.projects +
      " projects" +
      (preview.local_profile
        ? " · includes local profile"
        : " · keeps the current local profile") +
      ". When applied, enabled services will be started or updated automatically.";
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
    say(
      "Configuration imported. Enabled services were started or updated automatically.",
    );
  });
$("cancel-import").onclick = () => {
  importBundle = null;
  $("import-preview").hidden = true;
  $("import-settings").value = "";
};

function renderMcpDefaults() {
  const select = $("mcp-default-model"),
    saved = state.settings.mcp_defaults || {};
  select.replaceChildren(new Option("Automatic · current configuration", ""));
  for (const [backend, spec] of Object.entries(state.settings.services))
    if (spec.enabled)
      for (const model of spec.models.filter((m) =>
        HarnessUI.selectableModel(backend, m),
      )) {
        select.append(
          new Option(HarnessUI.providerName(backend) + " · " + model, JSON.stringify([backend, model])),
        );
      }
  select.value = saved.model
    ? JSON.stringify([saved.backend, saved.model])
    : "";
  renderMcpEfforts(saved.effort || "");
  $("save-mcp").disabled = false;
  $("mcp-save-note").textContent = state.status.running
    ? "Changes will be applied without stopping the harness."
    : "When you save an enabled model, the harness starts automatically. Existing conversations are not deleted.";
}
function renderMcpEfforts(preferred = "") {
  const select = $("mcp-default-effort");
  select.replaceChildren(new Option("Automatic · compatible effort", ""));
  const value = $("mcp-default-model").value;
  if (!value) {
    select.disabled = true;
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
    select.append(new Option(preferred + " · saved, check account", preferred));
  select.value = preferred;
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
      HarnessUI.notice(
        $("mcp-feedback"),
        "MCP default saved. The configuration is applied automatically to upcoming requests.",
      );
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
      n == null ? "Not provided" : Number(n).toLocaleString("en-US");
    $("dashboard-updated").textContent =
      "updated " + new Date(data.checked_at * 1000).toLocaleTimeString("en-US");
    const values = [
      [
        "Requests / second",
        format(Number(data.requests_per_second.toFixed(3))),
        "Average over the last 60 seconds",
      ],
      ["Running / queued", data.active + " / " + data.queued, "Current state"],
      [
        "Reported tokens",
        data.input_tokens == null && data.output_tokens == null
          ? "Not provided"
          : format((data.input_tokens || 0) + (data.output_tokens || 0)),
        format(data.measured_jobs) + " requests with metrics",
      ],
      [
        "Output / second",
        format(data.latest_output_tokens_per_second),
        "Last measured run",
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
      n == null ? "Not provided" : (n / 1073741824).toFixed(1) + " GiB";
    const resources = [
      [
        "CPU",
        hw.cpu_percent == null
          ? "Waiting for sample"
          : format(hw.cpu_percent) + "%",
        hw.cpu_percent,
        100,
      ],
      [
        "Memory",
        gib(hw.memory_used) + " / " + gib(hw.memory_total),
        hw.memory_used,
        hw.memory_total,
      ],
    ];
    for (const gpu of hw.gpus) {
      resources.push(
        [
          "GPU " + gpu.name,
          format(gpu.percent) + "% · " + format(gpu.temperature) + " °C",
          gpu.percent,
          100,
        ],
        [
          "VRAM " + gpu.name,
          gib(gpu.vram_used) + " / " + gib(gpu.vram_total),
          gpu.vram_used,
          gpu.vram_total,
        ],
      );
    }
    if (!hw.gpus.length)
      resources.push(["GPU", "Measurement unavailable", null, 100]);
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
      queued: "Queued",
      running: "Running",
      completed: "Completed",
      failed: "Failed",
      cancelled: "Cancelled",
      interrupted: "Interrupted",
    };
    $("recent-count").textContent =
      "Showing " +
      data.recent.length +
      " of " +
      (data.recent_count ?? data.recent.length) +
      " · last ones ended within 30 min";
    $("recent-runs").setAttribute(
      "aria-label",
      "Showing " +
        data.recent.length +
        " of " +
        (data.recent_count ?? data.recent.length) +
        " runs",
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
            "Runner not provided",
        ),
        document.createTextNode(
          " · " +
            [
              names[job.state] || job.state,
              job.project || "No project",
              format(job.output_tokens) + " output tokens",
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
          "No run active or finished in the last 30 minutes.",
          "empty-history",
        ),
      );
  } catch (e) {
    $("dashboard-updated").textContent =
      "Could not refresh; the latest data remains visible.";
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
      ["Prompt", data.prompt],
      ["Response", answer || "Not received yet"],
    ]) {
      nodes.push(element("h4", title), element("pre", value, "execution-text"));
    }
    if (data.metrics) {
      nodes.push(
        element("h4", "Reported tokens and metrics"),
        describeExecutionData(data.metrics),
      );
    }
    const agents = events.filter(
      (e) => e.type === "maestro_step" || e.type === "maestro_planning",
    );
    if (agents.length || data.orchestration) {
      nodes.push(
        element("h4", "Agents and planning"),
        describeExecutionData(data.orchestration || agents.map((e) => e.data)),
      );
    }
    if (data.spans?.length) {
      nodes.push(element("h4", "Execution spans"));
      const spans = element("ol", undefined, "execution-spans");
      for (const span of data.spans) {
        const item = element("li");
        const outcome = span.attrs?.outcome || "unknown";
        const status = outcome === "unknown"
          ? span.end_ts == null
            ? span.start_ts == null ? "Pending" : "In progress"
            : "Outcome unknown"
          : outcome;
        const duration = Number.isFinite(span.start_ts) && Number.isFinite(span.end_ts)
          ? ` · ${Math.max(0, span.end_ts - span.start_ts).toFixed(1)} s`
          : "";
        item.append(
          element("strong", span.name || span.kind),
          document.createTextNode(` · ${span.kind} · ${status}${duration}`),
        );
        spans.append(item);
      }
      nodes.push(spans);
    }
    nodes.push(element("h4", "Activity · last 500 events"));
    const activity = element("ol", undefined, "execution-events");
    const labels = {
      thinking: "Thinking",
      reasoning_summary: "Reasoning summary",
      tool_start: "Tool started",
      tool_end: "Tool finished",
      context_usage: "Context usage",
      maestro_planning: "Planning",
      maestro_step: "Agent step",
      completed: "Completed",
      failed: "Failed",
      cancelled: "Cancelled",
      running: "Running",
      queued: "Queued",
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
    row.lastElementChild.textContent =
      "Could not query this run. The update will be retried.";
  } finally {
    delete row.dataset.loading;
  }
}

function describeExecutionData(value) {
  const labels = {
    input_tokens: "Input tokens",
    output_tokens: "Output tokens",
    cached_tokens: "Cached tokens",
    thinking_tokens: "Thinking tokens",
    inference_seconds: "Run time (s)",
    total_seconds: "Duration (s)",
    backend: "Provider",
    model: "Model",
    effort: "Effort",
    role: "Role",
    steps: "Steps",
    metrics: "Metrics",
    text: "Message",
    tool: "Tool",
    index: "Step",
    status: "Status",
    answer: "Response",
    context_usage: "Context",
    result: "Result",
    prompt: "Instruction",
  };
  const box = element("div", undefined, "observed-data");
  if (value === null || typeof value !== "object") {
    box.textContent = value == null ? "Not provided" : String(value);
    return box;
  }
  for (const [key, item] of Object.entries(value)) {
    const group = element("div", undefined, "observed-field");
    group.append(element("strong", labels[key] || key.replaceAll("_", " ")));
    if (item !== null && typeof item === "object")
      group.append(describeExecutionData(item));
    else
      group.append(
        element("span", item == null ? "Not provided" : String(item)),
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
        step === 0 ? "Choose service and models" : "Review and finish";
    return;
  }
  const plugins = integrationKind() === "plugin",
    title = plugins ? "Plugins" : "Connectors";
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
    ? "Plugins are resource packages for the " +
      (engine === "claude" ? "Claude Code" : "Codex") +
      " engine. Check the description, install the package, and refresh the list. Then select the plugins you want to load and save. MCP servers are managed in the Connectors tab."
    : "MCP connectors let the model use tools from other services. To connect: choose Register MCP connector, enter a name and the official URL of the service (or the local command in JSON), and run it. If the service requires login, choose Authenticate connector and complete the official authorization. Refresh the list, select the connector, and save. Registering does not confirm that the connection is authenticated or working.";
  const options = plugins
    ? [
        ["plugin_install", "Install plugin"],
        ["plugin_remove", "Remove plugin"],
      ]
    : [
        ["connector_add", "Register MCP connector"],
        ["login", "Authenticate connector"],
        ["connector_remove", "Remove connector"],
      ];
  const previous = $("integration-action").value;
  $("integration-action").replaceChildren(
    ...options.map(([value, label]) => new Option(label, value)),
  );
  if (options.some(([value]) => value === previous))
    $("integration-action").value = previous;
  renderIntegrationForm();
  renderIntegrationCliGuide();
  renderIntegrationSelection();
  if (integrationCatalogs.has(engine)) renderCatalog();
  else loadCatalog();
  $("wizard-progress").textContent =
    title + " · select what the harness will load";
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
        ? "Manage plugins via CLI"
        : "Manage MCP connectors via CLI",
    ),
    element(
      "p",
      "The catalog above is queried by the server. MCP lists configured connectors; plugins includes options not yet installed from known marketplaces. Installation, login, and authorization only happen when you choose an operation.",
      "hint",
    ),
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
$("provider-options").addEventListener(
  "click",
  (event) => {
    const button = event.target.closest(".discovery-option");
    if (!button) return;
    const id = button.dataset.provider,
      current = state.settings.services[id];
    if (
      id !== "local" &&
      current &&
      !current.added &&
      !current.enabled &&
      !current.models?.length
    )
      settings.services[id].integrations = loadableIntegrations(id).map(
        (item) => item.id,
      );
  },
  true,
);

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
    ? "Searching connectors and plugins for " +
      (provider === "claude" ? "Claude Code" : "Codex") +
      " on this computer… Please wait."
    : data?.error
      ? "Could not query the catalog. " +
        data.error +
        (data.items?.length
          ? " Showing results from the last successful query."
          : "") +
        " Try again in Check catalogs."
      : data
        ? items.length + " result(s). " + (data.warnings || []).join(" ")
        : "Check the catalogs known by this server's CLI.";
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
          item.kind === "plugin" ? "Plugin" : "MCP connector",
          "subtle",
        ),
      );
      card.append(
        detail,
        element(
          "span",
          item.status === "installed"
            ? "Installed"
            : item.status === "configured"
              ? "Configured"
              : "Available to install",
          "pill",
        ),
      );
      if (item.kind === "plugin" && item.status === "available") {
        const install = element("button", "Install", "button secondary");
        install.setAttribute("aria-label", "Install " + item.name);
        install.onclick = () =>
          action(async () => {
            await request("integration", {
              provider,
              action: "plugin_install",
              name: item.id.replace(/^plugin:/, ""),
            });
            say(
              "Installation started. Track Operations; once finished, refresh integrations and select the plugin.",
            );
            pollOperations();
          });
        card.append(install);
      }
      return card;
    }),
  );
  if (data && !data.error && !items.length)
    $("catalog-items").append(
      element(
        "p",
        "No options found in this catalog. You can register another connector or marketplace via the CLI.",
        "hint",
      ),
    );
  $("catalog-more").hidden = items.length <= catalogVisibleCount;
  $("catalog-count").textContent = items.length
    ? "Showing " +
      Math.min(catalogVisibleCount, items.length) +
      " of " +
      items.length +
      " results."
    : "";
}
async function loadCatalog() {
  const provider = $("integration-provider").value;
  if (catalogPending.has(provider)) return;
  catalogPending.add(provider);
  renderCatalog();
  try {
    integrationCatalogs.set(
      provider,
      await requestCatalog(provider),
    );
  } catch (error) {
    integrationCatalogs.set(provider, {
      ...(integrationCatalogs.get(provider) || { items: [] }),
      error: error.message,
    });
  } finally {
    catalogPending.delete(provider);
    renderCatalog();
    renderIntegrationSelection();
  }
}
function createCatalog() {
  const host = $("integrations").querySelector(".panel-body"),
    section = element("section", undefined, "integration-catalog");
  const heading = element("h3", "Server catalog"),
    refresh = element("button", "Check catalogs", "button secondary");
  refresh.id = "catalog-refresh";
  refresh.onclick = () => loadCatalog();
  const label = element("label", "Search connectors and plugins"),
    search = element("input");
  search.id = "catalog-search";
  search.type = "search";
  search.placeholder = "Name or marketplace";
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
  const more = element("button", "Load more", "button secondary");
  more.id = "catalog-more";
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
  "hardware-model":
    "Each weights file has its own execution profile and permissions.",
  "profile-binary":
    "Path to the llama-server program that will load this model on this machine.",
  "profile-description":
    "Note to help identify the purpose of this profile; it is not sent to the model.",
  "integration-provider":
    "Provider whose profile will receive the integration.",
  "integration-action":
    "Choose between registering, authenticating, installing, or removing the integration.",
  "integration-name":
    "Identifier used by the service or the plugin marketplace.",
  "integration-transport":
    "HTTPS connects to a remote server; stdio starts a process on this machine.",
  "integration-source":
    "Address of the MCP server or a JSON list with the command and its arguments.",
  "install-model":
    "Weights that will be downloaded. Check the size and license before authorizing.",
  "install-runtime":
    "Where the weights will be stored and which runtime can load them.",
  "model-folder":
    "Folder on this machine where the panel will search for GGUF model files.",
  "local-file": "Weights file that will be associated with its own profile.",
  "import-settings":
    "Loads a file exported by the panel for review before applying.",
  "mcp-default-model":
    "Model used by MCP clients (apps that call the harness tools) when they do not choose another one.",
  "mcp-default-effort":
    "How much reasoning effort to request. Higher levels can increase time and usage; the options depend on the model.",
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
    : "gmail, drive or gitlab";
  const local = $("integration-transport").value === "stdio";
  $("integration-source").closest("label").firstChild.textContent = local
    ? "MCP server command as a JSON list"
    : "Official MCP server URL";
  $("integration-source").placeholder = local
    ? '["program", "argument"]'
    : "https://your-mcp-server/mcp";
}
$("integration-action").onchange = renderIntegrationForm;
$("integration-transport").onchange = renderIntegrationForm;
renderIntegrationForm();

// One action vocabulary keeps dynamic and static panel buttons consistent.
const buttonActions = [
  [/^Pin catalog/, "lock", "Runs this catalog from the chosen immutable revision."],
  [/^Provision runtime/, "download", "Creates the catalog environment and writable state declared by its manifest."],
  [/^Re-trust hooks/, "shield", "Trusts the catalog hook files as they are now, so they run again."],
  [/^Preview update/, "search", "Fetches the catalog and compares resource revisions without moving its pin."],
  [/^Move pin/, "check", "Applies the exact revision shown in the current update preview."],
  [/^Remove binding/, "trash", "Removes stored credentials and their project bindings."],
  [
    /^(Save|Complete and save|Apply)/,
    "device-floppy",
    "Saves and applies the changes to this configuration.",
  ],
  [/^(Edit)/, "pencil", "Opens editing in a window over the provider list."],
  [
    /^(Delete)/,
    "trash",
    "Removes the entry after confirmation; does not uninstall the service.",
  ],
  [/^(Remove)/, "trash", "Removes this item from the configuration."],
  [
    /^(Log in)/,
    "login",
    "Opens official authentication to connect or renew your account.",
  ],
  [
    /^(Check|Detect|Query|Search)/,
    "scan",
    "Checks the current status and refreshes the available options.",
  ],
  [
    /^(Refresh|Retry)/,
    "refresh",
    "Repeats the query to refresh the data or recover from a failure.",
  ],
  [/^(Add|Create)/, "plus", "Opens the setup or selection of a new item."],
  [
    /^(Cancel|Close)/,
    "x",
    "Closes this window; pending changes require confirmation before being discarded.",
  ],
  [
    /^(Back|Choose another)/,
    "chevron-left",
    "Returns to the previous step of the configuration.",
  ],
  [
    /^(Continue|Load more)/,
    "chevron-right",
    "Shows the next step or more results.",
  ],
  [
    /^(Download|Install)/,
    "download",
    "Starts installing or downloading the selected item.",
  ],
  [
    /^Export/,
    "download",
    "Downloads the saved settings to a file without credentials.",
  ],
  [
    /^Import or export/,
    "adjustments",
    "Opens the options to import or export settings.",
  ],
  [
    /^(Use current configuration|Copy configuration|Copy the running configuration)/,
    "copy",
    "Copies the running configuration into this model's profile.",
  ],
  [
    /^Use (file|an already downloaded file)/,
    "folder",
    "Selects a model file already downloaded on this server.",
  ],
  [
    /^Use this folder/,
    "check",
    "Confirms the selected folder for this configuration.",
  ],
  [
    /^Configure this file/,
    "adjustments",
    "Opens the profile of the selected model file.",
  ],
  [
    /^Start/,
    "player-play",
    "Starts the model using the profile saved on this server.",
  ],
  [
    /^Run operation/,
    "player-play",
    "Runs the selected operation and shows its result.",
  ],
  [
    /^Operations in progress/,
    "list",
    "Opens the progress and results of administrative operations.",
  ],
  [
    /^(Choose (models )?folder|Home folder|Parent folder)/,
    "folder",
    "Opens folder navigation on this server.",
  ],
  [
    /^(Model and hardware)/,
    "adjustments",
    "Shows the models and execution settings of the provider.",
  ],
  [
    /^Permissions/,
    "isolation",
    "Shows the access and permissions of the provider or model.",
  ],
  [
    /^(Plugins|Connectors|Connection)/,
    "plug",
    "Shows the integrations and connection options.",
  ],
  [
    /^(Settings|Appearance)/,
    "settings",
    "Opens the appearance and configuration preferences.",
  ],
];
function decoratePanelButtons() {
  for (const button of document.querySelectorAll("button")) {
    const label = (
      button.getAttribute("aria-label") || button.textContent
    ).trim();
    const action = buttonActions.find(([pattern]) => pattern.test(label));
    const folder = button.closest(
      "#folder-picker-breadcrumb,#folder-picker-list",
    );
    const theme = button.dataset.themeChoice;
    const provider = button.classList.contains("discovery-option");
    const name = folder ? "folder" : theme ? "palette" : action?.[1];
    const explanation = folder
      ? "Opens this folder on the server: " + (button.title || label)
      : theme
        ? "Applies the " +
          (button.querySelector(".theme-name")?.textContent || theme) +
          " theme to the panel."
        : provider
          ? "Opens this provider's configuration to choose account, models, and permissions."
          : action?.[2];
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
