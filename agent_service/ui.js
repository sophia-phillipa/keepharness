const MAX_ATTACHMENTS = 20;
const MAX_ATTACHMENT_BYTES = 100 * 1024 * 1024;
const AUDIO_ATTACHMENT_TIMEOUT_MS = 8200000;
("use strict");
const $ = (id) => document.getElementById(id);
let providers = {},
  models = [],
  files = [],
  job = "",
  last = 0,
  controller = null,
  active = null,
  busy = false,
  parent = null,
  conversation = "",
  loading = false,
  build = "",
  reloadPending = false;
let queuedTurns = [];
let executionMode = "native",
  executionModeChosen = false;
let policyProject = null,
  policyPending = false,
  policySequence = 0;
let uploadsAllowed = false,
  streamDisconnected = false,
  submitting = false,
  cancelling = false,
  pendingSubmission = null;
try {
  pendingSubmission = JSON.parse(
    sessionStorage.getItem("pending-submission") || "null",
  );
} catch {}
function submissionKey(data) {
  const payload = JSON.stringify(data);
  if (pendingSubmission?.payload !== payload) {
    pendingSubmission = {
      payload,
      key:
        crypto.randomUUID?.() ||
        Array.from(crypto.getRandomValues(new Uint8Array(16)), (n) =>
          n.toString(16).padStart(2, "0"),
        ).join(""),
    };
    try {
      sessionStorage.setItem(
        "pending-submission",
        JSON.stringify(pendingSubmission),
      );
    } catch {}
  }
  return pendingSubmission.key;
}
function clearSubmission() {
  pendingSubmission = null;
  try {
    sessionStorage.removeItem("pending-submission");
  } catch {}
}
let conversations = [],
  legacyHistory = false,
  historyRequest = 0,
  conversationLoad = 0,
  uploads = 0;
let conversationActivity = {};
try {
  const saved = JSON.parse(
    localStorage.getItem("conversation-activity") || "{}",
  );
  if (saved && typeof saved === "object" && !Array.isArray(saved))
    conversationActivity = saved;
} catch {}
function saveConversationActivity() {
  try {
    localStorage.setItem(
      "conversation-activity",
      JSON.stringify(conversationActivity),
    );
  } catch {}
}
function observeConversation(c) {
  const previous = conversationActivity[c.id],
    token = c.last_job_id || c.id;
  const unread =
    !!previous?.unread ||
    (c.state === "completed" &&
      !!previous &&
      (previous.token !== token || previous.state !== c.state));
  conversationActivity[c.id] = { token, state: c.state, unread };
}
function conversationIndicator(c) {
  const working = ["queued", "running"].includes(c.state),
    unread = conversationActivity[c.id]?.unread;
  if (!working && !unread) return null;
  const indicator = document.createElement("span");
  indicator.className =
    "conversation-indicator " + (working ? "working" : "unread");
  indicator.setAttribute("role", "img");
  indicator.setAttribute(
    "aria-label",
    working ? "In progress" : "Unread response",
  );
  indicator.title = indicator.getAttribute("aria-label");
  return indicator;
}
let fileTree = {
  project: "",
  roots: [],
  rootId: "",
  basePath: "",
  cache: new Map(),
  expanded: new Set(),
  selected: new Set(),
  anchor: "",
  request: 0,
  ready: false,
};
function setConversationTitle(value) {
  const full = String(value || "New Conversation").trim() || "New Conversation",
    el = $("conversation-title");
  el.textContent = full.length > 80 ? full.slice(0, 79).trimEnd() + "…" : full;
  el.title = full;
  syncActiveProjectBadge();
}
let composerProjectId = null,
  composerGitRequest = 0,
  resourceRequest = 0,
  resourceItems = [],
  resourceSelections = [],
  invalidResourceTokens = new Set();
const resourceToken = /(^|\s)(@@|\/\/|@|\/)([^\s@/]*)$/;
function resourceEngine() {
  const m = selected();
  return {
    backend: m?.backend || "",
    model: m?.id || "",
    execution_mode: executionMode,
  };
}
function triggerAtCaret() {
  const input = $("prompt"),
    before = input.value.slice(0, input.selectionStart || 0),
    match = resourceToken.exec(before);
  if (!match) return null;
  const start = match.index + match[1].length;
  return { prefix: match[2], query: match[3], start, end: before.length };
}
function syncResourceSelections() {
  const tokens = new Set($("prompt").value.split(/\s+/));
  resourceSelections = resourceSelections.filter((ref) =>
    tokens.has(ref.token),
  );
  invalidResourceTokens = new Set(
    [...invalidResourceTokens].filter((token) => tokens.has(token)),
  );
}
function renderPromptHighlights() {
  const input = $("prompt"),
    mirror = $("prompt-highlights"),
    tokens = new Set(resourceSelections.map((ref) => ref.token));
  mirror.replaceChildren();
  for (const part of input.value.split(/(\s+)/)) {
    if (tokens.has(part)) {
      const span = document.createElement("span");
      span.className = "prompt-resource";
      span.textContent = part;
      mirror.append(span);
    } else mirror.append(document.createTextNode(part));
  }
  // Preserve the last empty line and use text nodes so prompt content stays inert.
  mirror.append(document.createTextNode("\u200b"));
  input.classList.toggle("has-resource-highlights", tokens.size > 0);
  mirror.hidden = tokens.size === 0;
  syncPromptHighlightLayout();
}
function syncPromptHighlightLayout() {
  const input = $("prompt"),
    mirror = $("prompt-highlights"),
    style = getComputedStyle(input);
  for (const property of [
    "fontFamily",
    "fontSize",
    "fontWeight",
    "fontStyle",
    "fontStretch",
    "fontVariant",
    "lineHeight",
    "letterSpacing",
    "padding",
    "textIndent",
    "tabSize",
    "wordSpacing",
  ])
    mirror.style[property] = style[property];
  mirror.style.width = input.clientWidth + "px";
  mirror.style.height = input.clientHeight + "px";
  mirror.scrollTop = input.scrollTop;
  mirror.scrollLeft = input.scrollLeft;
}
$("prompt").addEventListener("scroll", syncPromptHighlightLayout, {
  passive: true,
});
new ResizeObserver(syncPromptHighlightLayout).observe($("prompt"));
function closeResourceMenu() {
  resourceRequest++;
  const menu = $("resource-menu");
  if (menu.matches(":popover-open")) menu.hidePopover();
  menu.replaceChildren();
}
function resourceKeydown(event) {
  const menu = $("resource-menu");
  if (
    !menu.matches(":popover-open") ||
    event.isComposing ||
    event.keyCode === 229
  )
    return false;
  const options = [...menu.querySelectorAll("[role=option]:not(:disabled)")],
    index = options.indexOf(document.activeElement);
  if (
    ["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key) &&
    options.length
  ) {
    event.preventDefault();
    event.stopPropagation();
    const next =
      event.key === "Home"
        ? 0
        : event.key === "End"
          ? options.length - 1
          : index < 0
            ? event.key === "ArrowUp"
              ? options.length - 1
              : 0
            : (index + (event.key === "ArrowDown" ? 1 : -1) + options.length) %
              options.length;
    options[next].focus();
    options[next].scrollIntoView({ block: "nearest" });
    return true;
  }
  if (event.key === "Enter" && !event.shiftKey && options.length) {
    event.preventDefault();
    event.stopPropagation();
    options[index < 0 ? 0 : index].click();
    return true;
  }
  if (event.key === "Escape") {
    event.preventDefault();
    event.stopPropagation();
    closeResourceMenu();
    $("prompt").focus();
    return true;
  }
  if (event.key === "Tab") closeResourceMenu();
  return false;
}
$("resource-menu").addEventListener("keydown", resourceKeydown);
function resourceIcon(item) {
  const origin = String(item.origin || "").toLowerCase(),
    symbol = origin.includes("claude")
      ? "brand-claude"
      : origin.includes("gemini")
        ? "brand-gemini"
        : origin.includes("codex")
          ? "brand-openai"
          : "";
  if (origin.includes("deepseek")) return document.createTextNode("🐋");
  if (!symbol) return document.createTextNode("◈");
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg"),
    use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  svg.classList.add("provider-logo-icon");
  use.setAttribute("href", "/assets/icons.svg#" + symbol);
  svg.append(use);
  return svg;
}
function renderResourceMenu(trigger, items, loading = false, warnings = []) {
  const menu = $("resource-menu"),
    groups = new Map();
  menu.replaceChildren();
  const heading = document.createElement("p");
  heading.className = "access-menu-heading";
  heading.textContent =
    trigger.prefix === "@@"
      ? "Tail Harness agents"
      : trigger.prefix === "//"
        ? "Tail Harness skills and commands"
        : trigger.prefix === "@"
          ? "Available agents"
          : "Available skills and commands";
  menu.append(heading);
  for (const warning of warnings) {
    const note = document.createElement("p");
    note.className = "resource-warning";
    note.setAttribute("role", "status");
    note.textContent = warning;
    menu.append(note);
  }
  if (trigger.prefix === "@@" || trigger.prefix === "//") {
    const empty = document.createElement("p");
    empty.className = "resource-empty";
    empty.textContent = "Tail Harness resources are not available yet.";
    menu.append(empty);
  } else if (loading) {
    const row = document.createElement("p");
    row.className = "resource-empty";
    row.textContent = "Refreshing resources…";
    menu.append(row);
  } else {
    for (const item of items) {
      const key = item.scope === "project" ? "Project" : "Global",
        groupKey = key + "\0" + item.origin;
      if (!groups.has(groupKey)) {
        const section = document.createElement("section"),
          title = document.createElement("h3");
        section.className = "resource-group";
        title.textContent = key + " · " + item.origin;
        section.append(title);
        groups.set(groupKey, section);
        menu.append(section);
      }
      const option = document.createElement("button");
      option.type = "button";
      option.setAttribute("role", "option");
      option.className = "resource-option";
      option.dataset.resourceId = item.id;
      option.dataset.resourceRevision = item.revision;
      option.dataset.resourceKind = item.kind;
      option.disabled = item.selectable === false;
      option.title = item.source || item.origin || item.name;
      const glyph = document.createElement("span");
      glyph.className = "resource-origin-icon";
      glyph.setAttribute("aria-hidden", "true");
      glyph.append(resourceIcon(item));
      const text = document.createElement("span"),
        name = document.createElement("strong"),
        description = document.createElement("small");
      name.textContent = item.name;
      description.textContent =
        (item.kind === "agent"
          ? "Agent"
          : item.kind === "skill"
            ? "Skill"
            : "Command") +
        (item.description ? " · " + item.description : "") +
        (item.unavailable_reason ? " · " + item.unavailable_reason : "");
      text.append(name, description);
      option.append(glyph, text);
      option.onclick = () => selectResource(item, trigger);
      groups.get(groupKey).append(option);
    }
  }
  if (
    (trigger.prefix[0] === "@" &&
      !items.some((item) => item.kind === "agent")) ||
    (trigger.prefix[0] === "/" &&
      !items.some((item) => item.kind === "skill" || item.kind === "command"))
  ) {
    const empty = document.createElement("p");
    empty.className = "resource-empty";
    empty.textContent = loading
      ? ""
      : "No resource compatible with this engine.";
    menu.append(empty);
  }
  menu.hidden = false;
  if (!menu.matches(":popover-open")) menu.showPopover();
  const rect = $("prompt").getBoundingClientRect();
  menu.style.left =
    Math.max(12, Math.min(rect.left, innerWidth - menu.offsetWidth - 12)) +
    "px";
  menu.style.top = Math.max(12, rect.top - menu.offsetHeight - 8) + "px";
}
function selectResource(item, trigger) {
  if (item.selectable === false) return;
  const marker = trigger.prefix[0] === "@" ? "@" : "/",
    token = marker + item.name,
    conflict = resourceSelections.find(
      (ref) => ref.token === token && ref.id !== item.id,
    );
  if (conflict) {
    status(
      "There is already another resource called " +
        token +
        " in this message. Remove it before choosing a different source.",
    );
    closeResourceMenu();
    return;
  }
  const input = $("prompt"),
    before = input.value.slice(0, trigger.start),
    after = input.value.slice(trigger.end);
  input.value = before + token + " " + after;
  const caret = (before + token + " ").length;
  input.setSelectionRange(caret, caret);
  resourceSelections = resourceSelections.filter((ref) => ref.token !== token);
  resourceSelections.push({ id: item.id, revision: item.revision, token });
  invalidResourceTokens.delete(token);
  closeResourceMenu();
  input.focus();
  updateComposer();
  saveView();
}
async function refreshResources(trigger) {
  const request = ++resourceRequest,
    m = resourceEngine(),
    project = $("project").value;
  if (!m.backend) {
    renderResourceMenu(trigger, [], false);
    return;
  }
  renderResourceMenu(trigger, [], true);
  try {
    const query = new URLSearchParams({
      project_id: project,
      backend: m.backend,
      model: m.model,
      execution_mode: m.execution_mode,
    });
    const data = await json("/v1/resources?" + query, {
      signal: AbortSignal.timeout(5000),
    });
    if (
      request !== resourceRequest ||
      project !== $("project").value ||
      m.backend !== resourceEngine().backend ||
      m.model !== resourceEngine().model ||
      m.execution_mode !== executionMode
    )
      return;
    resourceItems = Array.isArray(data.items) ? data.items : [];
    const filtered = resourceItems
      .filter((item) =>
        trigger.prefix === "@"
          ? item.kind === "agent"
          : item.kind === "skill" || item.kind === "command",
      )
      .filter((item) =>
        item.name.toLowerCase().includes(trigger.query.toLowerCase()),
      );
    renderResourceMenu(
      trigger,
      filtered,
      false,
      Array.isArray(data.warnings) ? data.warnings : [],
    );
  } catch {
    if (request !== resourceRequest) return;
    resourceItems = [];
    renderResourceMenu(trigger, [], false);
    const note = $("resource-menu").querySelector(".resource-empty");
    if (note) note.textContent = "Couldn't refresh resources.";
  }
}
function openResourceMenu() {
  const trigger = triggerAtCaret();
  if (!trigger) {
    closeResourceMenu();
    return;
  }
  if (trigger.prefix === "@@" || trigger.prefix === "//") {
    renderResourceMenu(trigger, [], false);
    return;
  }
  void refreshResources(trigger);
}
function invalidateResources() {
  for (const ref of resourceSelections) invalidResourceTokens.add(ref.token);
  resourceSelections = [];
  resourceItems = [];
  closeResourceMenu();
  renderPromptHighlights();
  saveView();
}
function syncComposerProject() {
  const option = $("project").selectedOptions[0],
    id = option?.value || "",
    name = id && id !== "sem-projeto" ? option.textContent : "";
  $("composer-project").hidden = !name;
  $("composer-project-name").textContent = name;
  $("composer-project-name").title = name;
  if (composerProjectId !== id) {
    composerProjectId = id;
    $("composer-git").hidden = true;
    void refreshComposerGit();
  }
}
async function refreshComposerGit() {
  const id = $("project").value,
    request = ++composerGitRequest;
  if (!id || id === "sem-projeto") return;
  try {
    const data = await json(
      "/v1/project-git?project_id=" + encodeURIComponent(id),
      { signal: AbortSignal.timeout(5000) },
    );
    if (request !== composerGitRequest || id !== $("project").value) return;
    $("composer-git").textContent = data.revision || "";
    $("composer-git").title = data.revision ? "Git: " + data.revision : "";
    $("composer-git").hidden = !data.revision;
  } catch {
    if (request === composerGitRequest && id === $("project").value)
      $("composer-git").hidden = true;
  }
}
function syncActiveProjectBadge() {
  syncComposerProject();
  const option = $("project").selectedOptions[0],
    badge = $("active-project-badge");
  if (!badge) return;
  const name =
    option?.value && option.value !== "sem-projeto" ? option.textContent : "";
  badge.hidden = !name;
  badge.lastElementChild.textContent = name || "";
  badge.title = name || "";
}
const welcomeTemplate = $("welcome").cloneNode(true);
const expandedProjects = new Map();
let preferredSelection = {};
try {
  preferredSelection =
    JSON.parse(localStorage.getItem("chat-selection") || "{}") || {};
} catch {}
const labels = {
  maestro_planning: "Maestro is planning",
  maestro_plan: "Agents selected",
  maestro_step: "Running Maestro step",
  answer_delta: "Responding",
  reasoning_delta: "Thinking",
  reasoning_summary: "Reasoning summary",
  interrupted: "Interrupted",
  queued: "Queued",
  running: "Running",
  thinking: "Thinking",
  planning: "Preparing the run",
  tool_start: "Using tool",
  tool_end: "Tool finished",
  session_resumed: "Conversation context resumed",
  context_compacting: "Optimizing conversation context…",
  context_compacted: "Context optimized; conversation preserved",
  validating_changes: "Validating changes",
  changes_applied: "Changes applied to the project",
  deployment_failed: "Changes not applied; check the error",
  reload_scheduled: "Refreshing the panel",
  completed: "Completed",
  cancelled: "Cancelled",
  failed: "Failed",
  loading: "Preparing model",
};
const status = (text) => {
  const target = $("status");
  target.hidden = false;
  target.className = "";
  target.textContent = text;
  if (/^Ready to chat/.test(text)) {
    target.className = "visually-hidden";
  } else if (
    /^(Couldn't|Connection lost|Failed|Error|Connect Tailscale|Incomplete response|Run interrupted|Run cancelled|Interrupted|Changes not applied)/.test(
      text,
    )
  ) {
    TailUI.notice(target, text, { error: true });
  } else if (
    Object.values(labels).includes(text) ||
    /^(Completed|Failed run|Cancelled|Running|Thinking|Reasoning|Receiving response|Preparing|Using tool|Tool finished|Plan updated|Run steps|Working|Checking quota|Sending request|Loading|Connected|Cancelling|Reconnecting|Ready to chat)/i.test(
      text,
    )
  ) {
    target.textContent = "";
  }
};
const names = {
  "qwen-local": "Qwen3.6 · local",
  "gpt-6-astra": "GPT-6 Astra",
  "gpt-5.6-sol": "GPT-5.6 Sol",
  "gpt-5.6-terra": "GPT-5.6 Terra",
  "gpt-5.6-luna": "GPT-5.6 Luna",
  "gpt-5.5": "GPT-5.5",
  "deepseek-flash": "DeepSeek V4.1 Flash",
  "deepseek-v4-pro": "DeepSeek V4 Pro",
};
const modelIcons = {
  "qwen-local": "✦",
  "gpt-6-astra": "🌟",
  "gpt-5.6-sol": "☀️",
  "gpt-5.6-terra": "🌍",
  "gpt-5.6-luna": "🌙",
  "gpt-5.5": "✳",
};
const modelIcon = (id) => {
  const name = (
    String(id || "") +
    " " +
    (models.find((m) => m.id === id)?.name || "")
  ).toLowerCase();
  if (modelIcons[id]) return modelIcons[id];
  if (name.includes("qwen")) return "✦";
  if (name.includes("claude")) return "✳";
  if (name.includes("deepseek")) return "🐋";
  if (name.includes("gemini")) return "✦";
  if (name.includes("gemma")) return "💎";
  if (name.includes("llama")) return "🦙";
  return "◈";
};
const providerNames = {
  local: "Local server",
  codex: "Codex",
  claude: "Claude Code",
  gemini: "Gemini CLI",
  deepseek: "DeepSeek",
  maestro: "Maestro",
};
const modelName = (id) =>
  names[id] || models.find((m) => m.id === id)?.name || id || "No model";
const selectedIdentity = () => {
  const m = selected();
  return m
    ? {
        backend: m.backend,
        provider: providerNames[m.backend] || m.backend || "Provider",
        model: modelName(m.id),
        modelIcon: modelIcon(m.id),
      }
    : null;
};
const efforts = {
  auto: "Maestro chooses per step",
  none: "No reasoning",
  configured: "Provider's default",
  low: "Low",
  medium: "Medium",
  high: "High",
  xhigh: "Very high",
  max: "Maximum",
  ultra: "Ultra",
};
const userErrors = {
  rate_limit:
    "Too many requests in a short time. The server has temporarily limited this access.",
  submission_rate_limit:
    "You sent new requests too quickly. This request wasn't queued.",
  queue_full:
    "The server queue is full. This request wasn't queued; wait for other runs to finish.",
  owner_queue_full:
    "You reached the queue limit for requests. Wait for one of your runs to finish before sending another.",
  stream_limit:
    "There are too many tracking connections open in this session. Close the extra tabs and try again.",
  login_rate_limit:
    "Too many sign-in attempts. Check your details before trying to log in again.",
  model_not_allowed: "This model is not enabled. Check the available models.",
  model_not_available:
    "The model is not available. Ask the administrator to check the local server.",
  uploads_denied: "Attachments are disabled for this service.",
  authentication_required: "Your connection needs authorization.",
  project_denied: "You don't have access to this project.",
  native_failed:
    "The AI service did not finish the run. Check the activity and try again.",
  cli_missing:
    "The provider's command-line tool is missing on the server. Reinstall it, refresh discovery in the admin panel and try again.",
  isolation_unavailable:
    "Isolated conversations need Linux with bubblewrap on the server. Turn isolation off or ask the administrator to install bubblewrap.",
  project_name_exists:
    "A project with that name already exists. Choose a different name.",
  invalid_project_name: "The name needs to be between 3 and 100 characters.",
  project_directory_required:
    "Add at least one existing folder to the project.",
  project_directory_forbidden:
    "One of the chosen folders is protected or not authorized.",
};
async function api(path, options = {}) {
  let r;
  try {
    r = await fetch(path, {
      ...options,
      signal: options.signal || AbortSignal.timeout(30000),
    });
  } catch (e) {
    if (e.name === "AbortError") throw e;
    throw Error(
      e.name === "TimeoutError"
        ? "The server took too long to respond. Check the activity before repeating the request."
        : "Couldn't connect to the server. Check your connection and try again.",
    );
  }
  if (!r.ok) {
    let e;
    try {
      e = await r.json();
    } catch {
      e = { code: "HTTP " + r.status };
    }
    let message =
      userErrors[e.code] ||
      (r.status === 429
        ? "The server applied a temporary limit to this request."
        : "The server did not complete the request (" +
          (e.code || r.status) +
          "). Check the data or try again.");
    if (r.status === 429) {
      const after = r.headers.get("Retry-After");
      const seconds =
        after === null
          ? NaN
          : /^\d+(?:\.\d+)?$/.test(after.trim())
            ? Number(after)
            : Math.max(0, (Date.parse(after) - Date.now()) / 1000);
      message += Number.isFinite(seconds)
        ? " Try again in " + Math.max(1, Math.ceil(seconds)) + " seconds."
        : " Wait a moment before trying again.";
    }
    const error = Error(message);
    error.code = e.code;
    error.status = r.status;
    if (r.status === 429) {
      const after = r.headers.get("Retry-After");
      error.retryAfter = /^\d+(?:\.\d+)?$/.test(after || "")
        ? Number(after) * 1000
        : Math.max(0, Date.parse(after) - Date.now()) || 5000;
    }
    throw error;
  }
  return r;
}
async function json(path, options) {
  const r = await api(path, options);
  try {
    return await r.json();
  } catch {
    throw Error(
      "The server returned invalid data. Try refreshing the connection.",
    );
  }
}

const post = (path, value) =>
  json(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(value),
  });
function selected() {
  return models.find((m) => m.id === $("model").value) || models[0];
}
function setBusy(value) {
  value = value || streamDisconnected;
  busy = value;
  $("add-project").disabled = value || loading;
  if (!value) paintMotion("");
  $("send").disabled =
    value || loading || uploads > 0 || !selected() || !$("prompt").value.trim();
  $("cancel").disabled = submitting || cancelling || !job;
  $("attach").disabled = value;
  $("project").disabled = value || loading || uploads > 0;
  $("model").disabled =
    submitting || loading || uploads > 0 || policyPending || !models.length;
  $("effort").disabled = $("model").disabled;
  $("access-mode").disabled = value;
  $("access-trigger").disabled = value;
  $("attach").disabled = value || uploads > 0 || !canUpload() || !selected();
  $("new").disabled = submitting || cancelling || loading || uploads > 0;
  updateComposer();
  renderFiles();
}
async function refreshProjectPermissions(timeout = 30000) {
  const project = $("project").value;
  if (project === policyProject) return true;
  const sequence = ++policySequence;
  policyPending = true;
  updateModelPermissions();
  updateComposer();
  try {
    const data = await json(
      "/v1/models?project_id=" + encodeURIComponent(project),
      { signal: AbortSignal.timeout(timeout) },
    );
    if (sequence !== policySequence) return;
    if (!Array.isArray(data.models)) throw Error("Invalid permissions catalog");
    const previous = $("model").value,
      effort = $("effort").value;
    models = data.models.filter((m) => TailUI.selectableModel(m.backend, m.id));
    uploadsAllowed = data.uploads_enabled === true;
    $("model").replaceChildren(
      ...models.map((m) => new Option(names[m.id] || m.name || m.id, m.id)),
    );
    if (models.some((m) => m.id === previous)) $("model").value = previous;
    policyProject = project;
    policyPending = false;
    updateEfforts();
    if ([...$("effort").options].some((o) => o.value === effort))
      $("effort").value = effort;
  } catch (e) {
    if (sequence !== policySequence) return;
    status(
      "Couldn't load this project's permissions. Select it again to retry: " +
        e.message,
    );
    return false;
  } finally {
    if (sequence === policySequence) {
      updateModelPermissions();
      updateComposer();
    }
  }
  return true;
}
function canUpload() {
  const m = selected();
  return (
    !policyPending &&
    uploadsAllowed &&
    !!m &&
    (m.permissions ? m.permissions.upload === true : true)
  );
}
function canAttachVideo() {
  const c = selected()?.capabilities;
  return (
    c?.video === true &&
    Array.isArray(c.video_execution_modes) &&
    c.video_execution_modes.includes(executionMode)
  );
}
function updateModelPermissions() {
  const m = selected(),
    allowed = canUpload();
  $("attach").disabled = busy || loading || uploads > 0 || !allowed;
  const textOnly =
    m?.backend === "deepseek" ||
    (executionMode !== "native" && m?.backend !== "local");
  const imageHelp = textOnly
    ? "Images unavailable in this integration"
    : "Images depending on model support";
  const videoHelp = canAttachVideo()
    ? "MP4: sampled frames" +
      (m.capabilities.video_transcription
        ? " and local speech transcription"
        : " · audio-only videos only: local transcription unavailable")
    : "MP4 unavailable for this model or mode";
  const attachmentHelp =
    "Up to 100 MiB per file · audio: up to 4h, transcribed locally · " +
    imageHelp +
    " · " +
    videoHelp;
  $("attach").title = allowed
    ? "Attach file. " + attachmentHelp
    : "Attachments not allowed for this model";
  $("attachment-help").textContent = allowed
    ? attachmentHelp
    : "Attachments disabled for this model. Review your permissions in the admin panel.";
  let panel = $("model-permissions");
  if (!panel) {
    panel = document.createElement("p");
    panel.id = "model-permissions";
    panel.className = "footnote";
    $("model-note").after(panel);
  }
  panel.hidden = !m?.permissions;
  if (m?.permissions)
    panel.textContent =
      (m.permissions.upload ? "Attachments allowed" : "No attachments") +
      " · " +
      (m.permissions.internet
        ? m.backend === "local"
          ? "Internet allowed for local tools"
          : "Internet allowed"
        : "Internet disabled") +
      " · " +
      (m.permissions.shell ? "Terminal allowed" : "Terminal disabled");
}
function updateEfforts() {
  updateModelPermissions();
  renderQuotaIdentity();
  const m = selected();
  if (!m) {
    $("effort").replaceChildren();
    return;
  }
  $("effort").replaceChildren(
    ...m.efforts.map((e) => {
      const o = document.createElement("option");
      o.value = e;
      o.textContent = efforts[e] || e;
      return o;
    }),
  );
  $("model-note").textContent =
    m.backend === "maestro"
      ? "Codex coordinates and chooses enabled local or cloud models for each step"
      : m.backend === "local"
        ? "Local model runs on the server · no OpenAI quota · check the sources"
        : m.backend === "deepseek"
          ? "DeepSeek API · uses your DeepSeek credits"
          : m.backend === "gemini"
            ? "Gemini CLI · Google account · subscription quota"
            : m.backend === "claude"
              ? "Claude Code on the server · Anthropic inference · quota not available"
              : "Codex CLI on the server · OpenAI inference · uses ChatGPT quota";
}
function quotaWindows(q, backend = selected()?.backend) {
  const buckets = q?.rateLimitsByLimitId || { codex: q?.rateLimits },
    entries = Object.entries(buckets || {}),
    windows = [];
  for (const [id, bucket] of entries) {
    if (!bucket) continue;
    const bucketNames = {
      five_hour: "Claude",
      seven_day: "Claude",
      seven_day_opus: "✳ Opus",
      seven_day_sonnet: "✳ Sonnet",
      overage: "Overage",
    };
    const bucketLabel =
      entries.length > 1
        ? backend === "claude"
          ? bucketNames[id] ||
            bucket.limitName ||
            bucket.name ||
            bucket.limitId ||
            id.replaceAll("_", " ")
          : bucket.limitName ||
            bucket.name ||
            bucket.limitId ||
            bucketNames[id] ||
            id.replaceAll("_", " ")
        : "";
    for (const window of [bucket.primary, bucket.secondary]) {
      const used = window?.usedPercent;
      if (!window || typeof used !== "number" || !Number.isFinite(used))
        continue;
      const duration = window.windowDurationMins,
        label =
          typeof duration === "number" && duration >= 10080
            ? "Weekly"
            : duration === 300
              ? "5 h"
              : typeof duration === "number" &&
                  Number.isFinite(duration) &&
                  duration > 0
                ? duration < 60
                  ? Math.round(duration) + " min"
                  : Math.round(duration / 60) + " h"
                : "Quota",
        remaining = Math.max(
          0,
          Math.min(100, Math.round((100 - used) * 10) / 10),
        ),
        percent = Number.isInteger(remaining)
          ? String(remaining)
          : remaining.toFixed(1);
      windows.push({
        label: bucketLabel ? `${label} · ${bucketLabel}` : label,
        remaining,
        percent,
        resetsAt: Number(window.resetsAt),
      });
    }
  }
  return windows;
}
function quotaText(q) {
  if (!q?.available) return "Quota unavailable";
  const windows = quotaWindows(q);
  return windows.length
    ? windows.map((w) => `${w.label}: ${w.percent}% remaining`).join(" · ")
    : "Percentage unavailable";
}
let quotaIdentityBackend = "";
function renderQuotaIdentity() {
  const model = selected(),
    backend = model?.backend || "",
    changed = backend !== quotaIdentityBackend;
  quotaIdentityBackend = backend;
  $("quota-model-icon").textContent = model ? modelIcon(model.id) : "◈";
  $("quota-model-name").textContent = model ? modelName(model.id) : "Model";
  $("quota-model-identity").title = model
    ? modelName(model.id) + " · " + (providerNames[backend] || backend)
    : "Selected model";
  $("quota-toggle").hidden = false;
  const states = {
    local: "No provider quota",
    claude: "Checking Claude quota…",
    deepseek: "DeepSeek credits",
    maestro: "Quota varies by step model",
  };
  if (backend === "codex" || backend === "claude") {
    if (changed) {
      $("quota-short").textContent =
        backend === "claude" ? "Checking Claude quota…" : "Checking quota…";
      $("quota-current").textContent = $("quota-short").textContent;
      $("quota-comparison").replaceChildren();
    }
  } else {
    $("quota-short").textContent = states[backend] || "Quota unavailable";
    $("quota-comparison").replaceChildren();
    $("quota-current").replaceChildren();
    const detail = document.createElement("p");
    detail.textContent =
      backend === "local"
        ? "This model runs locally. Context usage appears separately in the context indicator."
        : backend === "deepseek"
          ? "This model uses your own DeepSeek account credits."
          : backend === "maestro"
            ? "Maestro can route steps to different models; the quota depends on each step's executor."
            : "Select a model to check the provider quota.";
    $("quota-current").append(detail);
  }
  const description = model
    ? modelName(model.id) +
      " · " +
      (providerNames[backend] || backend) +
      " · " +
      $("quota-short").textContent
    : "Selected model · quota unavailable";
  $("quota-toggle").setAttribute("aria-label", description);
  $("quota-toggle").title = description;
  requestAnimationFrame(updateHeaderToastOffset);
}
function paintQuota(q, backend = selected()?.backend) {
  if (selected()?.backend !== backend || !["codex", "claude"].includes(backend))
    return;
  const available = q?.available === true,
    windows = available ? quotaWindows(q, backend) : [];
  const source =
    backend === "claude"
      ? "Latest information from Claude"
      : "Shared Codex account quota";
  $("quota-short").textContent = available
    ? windows.length
      ? windows.map((w) => `${w.label}: ${w.percent}% remaining`).join(" · ")
      : "Percentage unavailable"
    : backend === "claude"
      ? "Claude quota not reported"
      : "Quota unavailable";
  let checked = "";
  if (backend === "claude" && q?.checked_at) {
    const timestamp =
      typeof q.checked_at === "number"
        ? q.checked_at * 1000
        : Date.parse(q.checked_at);
    if (Number.isFinite(timestamp))
      checked = " · Updated on " + new Date(timestamp).toLocaleString();
  }
  $("quota-toggle").setAttribute(
    "aria-label",
    modelName(selected()?.id) +
      " · " +
      source +
      " · " +
      $("quota-short").textContent +
      checked,
  );
  $("quota-toggle").title =
    source + checked + " · " + $("quota-short").textContent;
  $("quota-current").replaceChildren();
  requestAnimationFrame(updateHeaderToastOffset);
  if (!available) {
    $("quota-current").textContent =
      backend === "claude"
        ? "No recent Claude quota observation is available."
        : "Couldn't check the quota right now. No percentage was estimated.";
    return;
  }
  if (!windows.length) {
    $("quota-current").textContent =
      "The provider did not report quota percentages for this account.";
    return;
  }
  if (backend === "claude") {
    const note = document.createElement("p");
    note.textContent = "Latest information from Claude" + checked;
    $("quota-current").append(note);
  }
  for (const window of windows) {
    const wrap = document.createElement("div");
    wrap.className = "quota-window";
    const label = document.createElement("div");
    label.textContent = `${window.label}: ${window.percent}% remaining`;
    const bar = document.createElement("progress");
    bar.max = 100;
    bar.value = window.remaining;
    const reset = document.createElement("small");
    reset.textContent =
      Number.isFinite(window.resetsAt) && window.resetsAt > 0
        ? "Renews on " + new Date(window.resetsAt * 1000).toLocaleString()
        : "Renewal time unavailable";
    wrap.append(label, bar, reset);
    $("quota-current").append(wrap);
  }
}
let quotaRequest = 0;
async function quota() {
  const request = ++quotaRequest,
    backend = selected()?.backend;
  renderQuotaIdentity();
  if (!["codex", "claude"].includes(backend)) {
    setQuotaOpen(false);
    return;
  }
  try {
    const value = await json(
      backend === "claude" ? "/v1/usage?backend=claude" : "/v1/usage",
    );
    if (request === quotaRequest && selected()?.backend === backend)
      paintQuota(value, backend);
  } catch {
    if (request === quotaRequest && selected()?.backend === backend)
      paintQuota(null, backend);
  }
}
function quotaSnapshot(kind, q) {
  if (selected()?.backend !== "codex") return;
  const id = "quota-" + kind;
  let p = $(id);
  if (!p) {
    p = document.createElement("p");
    p.id = id;
    $("quota-comparison").append(p);
  }
  p.textContent = (kind === "before" ? "Before: " : "After: ") + quotaText(q);
}
async function history(timeout = 30000) {
  const request = ++historyRequest;
  try {
    let data;
    try {
      data = await json(
        "/v1/conversations",
        timeout < 30000 ? { signal: AbortSignal.timeout(timeout) } : undefined,
      );
      legacyHistory = false;
    } catch (e) {
      if (e.status !== 404) throw e;
      const old = await json(
        "/v1/history",
        timeout < 30000 ? { signal: AbortSignal.timeout(timeout) } : undefined,
      );
      if (!Array.isArray(old.jobs)) throw Error("Invalid history");
      data = { conversations: old.jobs.map((r) => ({ ...r, legacy: true })) };
      legacyHistory = true;
    }
    if (!Array.isArray(data.conversations)) throw Error("Invalid history");
    if (request !== historyRequest) return false;
    conversations = data.conversations;
    conversations.forEach(observeConversation);
    saveConversationActivity();
    renderProjects();
    if ($("conversation-search-dialog").open) renderConversationSearch();
    if (conversation) {
      const current = conversations.find((item) => item.id === conversation);
      if (current) setConversationTitle(current.title);
    }
    $("history-note").textContent = legacyHistory
      ? "Compatible history: previous runs. Update the service to group turns."
      : "";
    return true;
  } catch (e) {
    if (request === historyRequest)
      status("Couldn't load conversations: " + e.message);
    return false;
  }
}
function conversationRow(c) {
  const row = document.createElement("div");
  row.className = "conversation-row";
  const open = document.createElement("button");
  open.setAttribute("aria-current", c.id === conversation ? "true" : "false");
  open.textContent = c.title || "Conversation";
  open.title = open.textContent;
  open.className = c.id === conversation ? "active" : "";
  open.onclick = () => load(c.id, c.legacy);
  const actions = document.createElement("details");
  actions.className = "conversation-actions";
  actions.hidden = !!c.legacy;
  const trigger = document.createElement("summary");
  trigger.textContent = "⋯";
  trigger.setAttribute("aria-label", "Actions for " + open.textContent);
  trigger.title = "Conversation actions";
  const menu = document.createElement("div");
  menu.className = "conversation-actions-menu";
  const rename = document.createElement("button");
  rename.type = "button";
  const renameIcon = document.createElementNS(
    "http://www.w3.org/2000/svg",
    "svg",
  );
  renameIcon.classList.add("th-icon", "menu-action-icon");
  renameIcon.setAttribute("viewBox", "0 0 24 24");
  renameIcon.setAttribute("aria-hidden", "true");
  const renameUse = document.createElementNS(
    "http://www.w3.org/2000/svg",
    "use",
  );
  renameUse.setAttribute("href", "/assets/icons.svg#pencil");
  renameIcon.append(renameUse);
  rename.append(renameIcon, document.createTextNode("Rename conversation"));
  rename.onclick = () => {
    actions.open = false;
    if (busy || loading || uploads) return;
    openRenameConversation(c, trigger);
  };
  const remove = document.createElement("button");
  remove.type = "button";
  const removeIcon = document.createElementNS(
    "http://www.w3.org/2000/svg",
    "svg",
  );
  removeIcon.classList.add("th-icon", "menu-action-icon");
  removeIcon.setAttribute("viewBox", "0 0 24 24");
  removeIcon.setAttribute("aria-hidden", "true");
  const removeUse = document.createElementNS(
    "http://www.w3.org/2000/svg",
    "use",
  );
  removeUse.setAttribute("href", "/assets/icons.svg#trash");
  removeIcon.append(removeUse);
  remove.append(removeIcon, document.createTextNode("Delete conversation"));
  remove.onclick = () => {
    actions.open = false;
    if (busy || loading || uploads) return;
    openDeleteConversation(c, trigger);
  };
  actions.addEventListener("toggle", () => {
    if (!actions.open) return;
    document
      .querySelectorAll(".conversation-actions[open]")
      .forEach((other) => {
        if (other !== actions) other.open = false;
      });
    const rect = trigger.getBoundingClientRect();
    const below = rect.bottom + 4,
      above = rect.top - menu.offsetHeight - 4;
    menu.style.left =
      Math.max(
        8,
        Math.min(
          rect.right - menu.offsetWidth,
          innerWidth - menu.offsetWidth - 8,
        ),
      ) + "px";
    menu.style.top =
      (below + menu.offsetHeight <= innerHeight - 8
        ? below
        : Math.max(8, above)) + "px";
  });
  actions.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      actions.open = false;
      trigger.focus();
    }
  });
  menu.append(rename, remove);
  actions.append(trigger, menu);
  const model = c.execution?.model;
  const icon = document.createElement("span");
  icon.className = "conversation-model-icon";
  icon.textContent = modelIcon(model);
  icon.setAttribute("aria-hidden", "true");
  const title = document.createElement("span");
  title.className = "conversation-title";
  title.textContent = c.title || "Conversation";
  open.replaceChildren(icon, title);
  const indicator = conversationIndicator(c);
  if (indicator) open.prepend(indicator);
  open.title = [
    title.textContent,
    model ? modelName(model).replace(/ · local$/, "") : "",
  ]
    .filter(Boolean)
    .join("\n");
  row.append(open, actions);
  return row;
}
function openRenameConversation(c, trigger) {
  const dialog = $("rename-conversation-dialog"),
    form = $("rename-conversation-form"),
    input = $("rename-conversation-name"),
    save = $("rename-conversation-save"),
    cancel = $("rename-conversation-cancel"),
    error = $("rename-conversation-error");
  let saving = false;
  input.value = c.title || "Conversation";
  input.disabled = false;
  cancel.disabled = false;
  save.textContent = "Save";
  const validate = () => {
    const length = Array.from(input.value.trim()).length;
    const message = !length
      ? "Enter a name; spaces only are not allowed."
      : length > 100
        ? "Use at most 100 characters."
        : "";
    error.textContent = message;
    input.setAttribute("aria-invalid", String(!!message));
    save.disabled = saving || !!message;
    $("rename-conversation-count").textContent = length + " / 100 characters";
    return !message;
  };
  input.oninput = validate;
  cancel.onclick = () => dialog.close();
  dialog.oncancel = (event) => {
    if (saving) event.preventDefault();
  };
  dialog.onclose = () => {
    if (trigger.isConnected) trigger.focus();
    else $("history").querySelector(".conversation-actions summary")?.focus();
  };
  form.onsubmit = async (event) => {
    event.preventDefault();
    if (saving || busy || loading || uploads || !validate()) return;
    saving = true;
    input.disabled = true;
    save.disabled = true;
    cancel.disabled = true;
    save.textContent = "Saving…";
    try {
      await json("/v1/conversations/" + encodeURIComponent(c.id), {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: input.value.trim() }),
      });
      await history();
      dialog.close();
    } catch (e) {
      error.textContent = "Couldn't rename: " + e.message;
    } finally {
      saving = false;
      input.disabled = false;
      save.disabled = false;
      cancel.disabled = false;
      save.textContent = "Save";
    }
  };
  validate();
  dialog.showModal();
  input.focus();
  input.select();
}
function openDeleteConversation(c, trigger) {
  const dialog = $("delete-conversation-dialog"),
    confirm = $("delete-conversation-confirm"),
    cancel = $("delete-conversation-cancel"),
    error = $("delete-conversation-error");
  $("delete-conversation-name").textContent = c.title || "Conversation";
  error.textContent = "";
  cancel.onclick = () => dialog.close();
  dialog.onclose = () => {
    if (trigger.isConnected) trigger.focus();
  };
  dialog.oncancel = (event) => {
    if (confirm.disabled) event.preventDefault();
  };
  confirm.onclick = async () => {
    if (confirm.disabled || busy || loading || uploads) return;
    confirm.disabled = true;
    cancel.disabled = true;
    confirm.textContent = "Deleting…";
    error.textContent = "";
    try {
      await json("/v1/conversations/" + encodeURIComponent(c.id), {
        method: "DELETE",
      });
      dialog.close();
      if (conversation === c.id) newConversation();
      await history();
    } catch (e) {
      error.textContent = "Couldn't delete: " + e.message;
    } finally {
      confirm.disabled = false;
      cancel.disabled = false;
      confirm.textContent = "Delete conversation";
    }
  };
  dialog.showModal();
  cancel.focus();
}
function confirmNewConversation() {
  return (
    !($("prompt").value.trim() || files.length) ||
    confirm("Discard the draft and attachments to start a new conversation?")
  );
}
function supportedExecutionModes() {
  return (
    selected()?.execution_modes ||
    (["codex", "claude"].includes(selected()?.backend)
      ? ["native", "scoped"]
      : ["native"])
  );
}
function syncExecutionMode() {
  const started = !!conversation || !!parent,
    modes = supportedExecutionModes();
  if (!started && !executionModeChosen)
    executionMode = modes.includes("native") ? "native" : "scoped";
  const isolated = executionMode === "scoped";
  const modeContract = Array.isArray(selected()?.execution_modes);
  // F-58: isolation is chosen before the first message, then only stated.
  $("execution-mode-choice").hidden = !modeContract;
  $("execution-mode-choice").classList.toggle("started", started);
  $("isolation-toggle").hidden = started;
  $("execution-mode-help").hidden = started;
  $("isolation-toggle").setAttribute("aria-checked", String(isolated));
  // F-94: a mode this model lacks can always be switched off.
  $("isolation-toggle").disabled =
    started ||
    busy ||
    loading ||
    submitting ||
    uploads > 0 ||
    (modes.length < 2 && modes.includes(executionMode));
  $("execution-mode-label").textContent = isolated
    ? "Isolated conversation"
    : "Native conversation";
  const warning = $("execution-mode-unavailable");
  warning.hidden =
    !selected() || (modes.includes(executionMode) && modes.length > 1);
  warning.textContent =
    modes.length === 1 && modes.includes(executionMode)
      ? isolated
        ? "This model requires isolation."
        : "This model only offers native mode."
      : "This model does not offer " +
        (isolated ? "isolated" : "native") +
        " mode. Choose a different model" +
        (started
          ? " or start a new conversation."
          : " or change the mode before sending.");
  const indicator = $("execution-mode-indicator");
  indicator.hidden = !started || !modeContract;
  indicator.dataset.isolated = String(isolated);
  const label = isolated
    ? "Isolated conversation · isolation on"
    : "Native conversation · isolation off";
  indicator.title = label;
  indicator.setAttribute("aria-label", label);
  $("dropzone").classList.toggle("has-execution-mode", started && modeContract);
}
$("isolation-toggle").onclick = () => {
  if (conversation || parent || busy || loading || submitting || uploads)
    return;
  executionMode = executionMode === "scoped" ? "native" : "scoped";
  executionModeChosen = true;
  invalidateResources();
  updateComposer();
  saveView();
};
function newConversation(title = "New Conversation") {
  resourceSelections = [];
  invalidResourceTokens.clear();
  if (submitting || cancelling || loading || uploads) {
    status(
      "Wait for the current send to finish before starting another conversation.",
    );
    return;
  }
  conversationLoad++;
  streamDisconnected = false;
  $("resume-execution").hidden = true;
  restoreSelection();
  clearSubmission();
  resetProjectFiles();
  resetActivity();
  active = null;
  setConversationTitle(title);
  if (controller) controller.abort();
  controller = null;
  queuedTurns = [];
  job = "";
  last = 0;
  parent = null;
  conversation = "";
  executionMode = "native";
  executionModeChosen = false;
  $("access-mode").value = "ask";
  syncAccessMode();
  files = [];
  renderFiles();
  $("messages").replaceChildren(welcomeTemplate.cloneNode(true));
  bindSuggestions();
  modelAvailability();
  $("prompt").value = "";
  updateComposer();
  saveView();
  $("context-meter").textContent = "New conversation · independent context";
  setBusy(false);
  status("");
  $("prompt").focus({ preventScroll: true });
  refreshProjectPermissions();
}
function chooseProject(id) {
  if (busy || loading || uploads) return;
  const draft = $("prompt").value;
  $("project").value = id;
  invalidateResources();
  const stale = [...invalidResourceTokens];
  newConversation();
  invalidResourceTokens = new Set(stale);
  $("prompt").value = draft;
  updateComposer();
  saveView();
  renderProjects();
  syncActiveProjectBadge();
}
let projectIcons = {},
  projectAliases = {},
  projectDetails = {};
function updateProjectMetadata(details = {}) {
  projectDetails = details;
  projectIcons = Object.fromEntries(
    Object.entries(details).map(([id, detail]) => [id, detail.icon]),
  );
  projectAliases = Object.fromEntries(
    Object.entries(details).map(([id, detail]) => [
      id,
      detail.canonical_id || id,
    ]),
  );
}
let projectPreferences = {};
try {
  const saved = JSON.parse(
    localStorage.getItem("project-list-preferences") || "{}",
  );
  if (saved && typeof saved === "object" && !Array.isArray(saved))
    projectPreferences = saved;
} catch {}
function setProjectPreference(id, key, value) {
  const next = {
    ...projectPreferences,
    [id]: { ...projectPreferences[id], [key]: value },
  };
  try {
    localStorage.setItem("project-list-preferences", JSON.stringify(next));
  } catch {
    status("Couldn't save the project list in this browser. Try again.");
    return;
  }
  projectPreferences = next;
  renderProjects();
  const group = [...$("projects").querySelectorAll("[data-project-id]")].find(
    (el) => el.dataset.projectId === id,
  );
  (
    group?.querySelector(".project-actions > button") ||
    $("removed-projects")?.querySelector("summary") ||
    $("add-project")
  ).focus();
  if (key === "hideIcon") return;
  status(
    key === "hidden"
      ? value
        ? "Project removed from the list. Folders and conversations preserved."
        : "Project restored to the list."
      : value
        ? "Project added to favorites."
        : "Project removed from favorites.",
  );
}
function openDeleteProjectFolder(project, label, trigger) {
  const dialog = $("delete-project-folder-dialog"),
    check = $("delete-project-folder-check"),
    confirm = $("delete-project-folder-confirm"),
    cancel = $("delete-project-folder-cancel"),
    error = $("delete-project-folder-error"),
    paths = $("delete-project-folder-paths");
  const url = "/v1/project-folder?project_id=" + encodeURIComponent(project);
  const request = new AbortController();
  const messages = {
    project_folder_busy:
      "There is a pending run or a deletion in progress for this project.",
    project_directory_shared:
      "This folder overlaps with another registered project's folder.",
    project_directory_forbidden:
      "This folder is protected and cannot be deleted.",
    project_directory_required: "This project has no main folder.",
    project_root_unavailable: "The main folder is not available.",
    project_folder_changed:
      "The folder changed since the confirmation. Close and reopen this window.",
    project_folder_delete_failed:
      "Couldn't finish the deletion. Part of the contents may have been deleted. Check the folder before trying again.",
  };
  let preview = null,
    deleting = false;
  $("delete-project-folder-name").textContent = label;
  paths.replaceChildren();
  error.textContent = "";
  check.checked = false;
  check.disabled = true;
  confirm.disabled = true;
  cancel.disabled = false;
  $("delete-project-folder-status").textContent = "Loading path…";
  check.onchange = () => {
    confirm.disabled = !preview || !check.checked || deleting;
  };
  cancel.onclick = () => {
    if (!deleting) dialog.close();
  };
  dialog.oncancel = (event) => {
    if (deleting) event.preventDefault();
  };
  dialog.onclose = () => {
    request.abort();
    preview = null;
    if (trigger.isConnected) trigger.focus();
  };
  confirm.onclick = async () => {
    if (!preview || !check.checked || deleting || busy || loading || uploads)
      return;
    deleting = true;
    check.disabled = true;
    confirm.disabled = true;
    cancel.disabled = true;
    error.textContent = "";
    $("delete-project-folder-status").textContent = "Deleting folder…";
    try {
      const result = await json(url, {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...preview, confirmed: true }),
      });
      const deleted = new Set(result.deleted_projects || [project]);
      for (const option of [...$("project").options])
        if (deleted.has(option.value)) option.remove();
      if (deleted.has(project) && !$("project").value)
        $("project").value = "sem-projeto";
      dialog.close();
      resetProjectFiles();
      renderProjects();
      $("delete-project-folder-result").showModal();
    } catch (e) {
      error.textContent =
        messages[e.code] || "Couldn't delete the folder: " + e.message;
      preview = null;
      check.checked = false;
      $("delete-project-folder-status").textContent =
        "Close and reopen to check the path before trying again.";
    } finally {
      deleting = false;
      cancel.disabled = false;
    }
  };
  dialog.showModal();
  cancel.focus();
  json(url, { signal: request.signal })
    .then((data) => {
      if (request.signal.aborted || !dialog.open) return;
      preview = data;
      paths.replaceChildren(
        ...data.paths.map((path) => {
          const li = document.createElement("li");
          li.textContent = path;
          return li;
        }),
      );
      $("delete-project-folder-status").textContent = data.missing
        ? "The folder no longer exists. Confirming will remove the entries from the list."
        : data.project_ids?.length > 1
          ? "This folder has " +
            data.project_ids.length +
            " duplicate entries. All of them will be removed from the list."
          : "";
      check.disabled = false;
    })
    .catch((e) => {
      if (!request.signal.aborted && dialog.open) {
        $("delete-project-folder-status").textContent = "";
        error.textContent =
          messages[e.code] ||
          (e.code === "HTTP 404"
            ? "The running service does not offer the folder deletion lookup. Restart the service from the admin panel and try again. This does not mean the project has no folder."
            : "Couldn't load the folder: " + e.message);
      }
    });
}
function renderProjects() {
  syncActiveProjectBadge();
  const matches = conversations;
  const removedOpen = $("removed-projects")?.open || false;
  const options = Array.from($("project").options).filter(
    (o) =>
      o.value !== "sem-projeto" &&
      (!projectAliases[o.value] || projectAliases[o.value] === o.value),
  );
  $("projects").replaceChildren(
    ...options
      .filter((o) => !projectPreferences[o.value]?.hidden)
      .sort(
        (a, b) =>
          Number(!!projectPreferences[b.value]?.favorite) -
          Number(!!projectPreferences[a.value]?.favorite),
      )
      .map((o) => {
        const favorite = !!projectPreferences[o.value]?.favorite;
        const group = document.createElement("details");
        group.className = "project-group";
        group.dataset.projectId = o.value;
        const selected =
          (projectAliases[$("project").value] || $("project").value) ===
          o.value;
        group.open = expandedProjects.get(o.value) ?? selected;
        group.ontoggle = () => expandedProjects.set(o.value, group.open);
        const heading = document.createElement("summary");
        heading.className = selected ? "active" : "";
        const button = document.createElement("button");
        button.textContent = o.textContent;
        button.title = o.textContent;
        if (favorite) {
          const star = document.createElement("span");
          star.textContent = "★ ";
          star.setAttribute("aria-hidden", "true");
          button.prepend(star);
          button.setAttribute("aria-label", o.textContent + " · Favorite");
        }
        const projectIcon = projectIcons[o.value],
          showIcon = !!projectIcon && !projectPreferences[o.value]?.hideIcon;
        if (showIcon) {
          const img = document.createElement("img");
          img.className = "project-logo";
          img.src = projectIcon.src;
          img.alt = "";
          img.title = "Detected icon: " + projectIcon.path;
          img.onerror = () => img.remove();
          button.prepend(img);
        }
        button.onclick = (e) => {
          e.preventDefault();
          group.open = !group.open;
          expandedProjects.set(o.value, group.open);
        };
        const actions = document.createElement("span");
        actions.className = "project-actions";
        const trigger = document.createElement("button");
        trigger.type = "button";
        trigger.textContent = "⋯";
        trigger.setAttribute(
          "aria-label",
          "Actions for project " + o.textContent,
        );
        trigger.setAttribute("aria-expanded", "false");
        const menu = document.createElement("div");
        menu.className = "conversation-actions-menu project-actions-menu";
        menu.setAttribute("popover", "auto");
        const navigate = document.createElement("button");
        navigate.type = "button";
        navigate.append(
          TailUI.icon("folder"),
          document.createTextNode("Go to project folder"),
        );
        navigate.onclick = () => {
          menu.hidePopover();
          void navigateProjectFolder(o.value);
        };
        const pin = document.createElement("button");
        pin.type = "button";
        pin.append(
          TailUI.icon("star"),
          document.createTextNode(
            favorite ? "Remove from favorites" : "Add to favorites",
          ),
        );
        pin.onclick = () => {
          menu.hidePopover();
          setProjectPreference(o.value, "favorite", !favorite);
        };
        const remove = document.createElement("button");
        remove.type = "button";
        remove.append(
          TailUI.icon("x"),
          document.createTextNode("Remove from list"),
        );
        remove.title = "Preserves the project's folders and conversations";
        remove.onclick = () => {
          menu.hidePopover();
          setProjectPreference(o.value, "hidden", true);
        };
        const deleteFolder = document.createElement("button");
        deleteFolder.type = "button";
        deleteFolder.className = "project-delete-folder";
        deleteFolder.append(
          TailUI.icon("trash"),
          document.createTextNode("Delete folder"),
        );
        deleteFolder.onclick = () => {
          menu.hidePopover();
          openDeleteProjectFolder(o.value, o.textContent, trigger);
        };
        trigger.onclick = (e) => {
          e.preventDefault();
          e.stopPropagation();
          if (menu.matches(":popover-open")) {
            menu.hidePopover();
            return;
          }
          menu.showPopover();
          const rect = trigger.getBoundingClientRect();
          menu.style.left =
            Math.max(
              8,
              Math.min(
                rect.right - menu.offsetWidth,
                innerWidth - menu.offsetWidth - 8,
              ),
            ) + "px";
          menu.style.top =
            Math.max(
              8,
              Math.min(rect.bottom + 4, innerHeight - menu.offsetHeight - 8),
            ) + "px";
          navigate.focus();
        };
        menu.addEventListener("toggle", (e) =>
          trigger.setAttribute("aria-expanded", String(e.newState === "open")),
        );
        menu.addEventListener("click", (e) => e.stopPropagation());
        const iconToggle = document.createElement("button");
        iconToggle.type = "button";
        iconToggle.setAttribute("aria-pressed", String(showIcon));
        iconToggle.disabled = !projectIcon;
        iconToggle.append(
          TailUI.icon("scan"),
          document.createTextNode(
            showIcon ? "Hide project icon" : "Show project icon",
          ),
        );
        iconToggle.title = projectIcon
          ? "Detected icon: " + projectIcon.path
          : "No icon found in the project";
        iconToggle.onclick = () => {
          menu.hidePopover();
          setProjectPreference(o.value, "hideIcon", showIcon);
        };
        const edit = document.createElement("button");
        edit.type = "button";
        edit.title = "Change the name, add folders, and choose the main folder";
        edit.append(
          TailUI.icon("pencil"),
          document.createTextNode("Edit project"),
        );
        edit.onclick = () => {
          menu.hidePopover();
          openProjectDialog(o.value);
        };
        navigate.title = "Open the main folder in the file explorer";
        pin.title = favorite
          ? "Remove this project from favorites"
          : "Show this project among favorites";
        deleteFolder.title =
          "Review deleting the project's main folder from the computer";
        trigger.title = "Open project actions";
        trigger.replaceChildren(TailUI.icon("settings"));
        menu.append(edit, navigate, pin, iconToggle, remove, deleteFolder);
        actions.append(trigger, menu);
        heading.append(button, actions);
        const children = document.createElement("div");
        children.className = "project-conversations";
        const items = matches.filter(
          (c) => (projectAliases[c.project] || c.project) === o.value,
        );
        children.replaceChildren(...items.map(conversationRow));
        const create = document.createElement("button");
        create.className = "project-new";
        create.append(
          TailUI.icon("folder-message"),
          document.createTextNode("New Conversation"),
        );
        create.setAttribute(
          "aria-label",
          "New Conversation in " + o.textContent,
        );
        create.onclick = () => {
          if (submitting || cancelling || loading || uploads) {
            status(
              "Wait for the current send to finish before starting another chat.",
            );
            return;
          }
          if (!confirmNewConversation()) return;
          $("project").value = o.value;
          newConversation("New Conversation in project " + o.textContent);
          expandedProjects.set(o.value, true);
          renderProjects();
          $("sidebar").classList.remove("open");
        };
        children.prepend(create);
        if (!items.length) {
          const empty = document.createElement("p");
          empty.className = "empty-history";
          empty.textContent = "No conversations";
          children.append(empty);
        }
        group.append(heading, children);
        return group;
      }),
  );
  const removed = options.filter((o) => projectPreferences[o.value]?.hidden);
  if (removed.length) {
    const section = document.createElement("details");
    section.id = "removed-projects";
    section.open = removedOpen;
    const heading = document.createElement("summary");
    heading.append(
      TailUI.icon("archive"),
      document.createTextNode("Removed projects (" + removed.length + ")"),
    );
    section.append(heading);
    for (const option of removed) {
      const restore = document.createElement("button");
      restore.type = "button";
      restore.append(
        TailUI.icon("refresh"),
        document.createTextNode("Restore " + option.textContent),
      );
      restore.onclick = () =>
        setProjectPreference(option.value, "hidden", false);
      section.append(restore);
    }
    $("projects").append(section);
  }
  $("history").replaceChildren(
    ...matches
      .filter(
        (c) =>
          c.project == null || c.project === "" || c.project === "sem-projeto",
      )
      .map(conversationRow),
  );
  if (!$("history").children.length) {
    const empty = document.createElement("p");
    empty.className = "empty-history";
    empty.textContent = "Your conversations will appear here.";
    $("history").append(empty);
  }
}
const answerMarkdown = window.markdownit({
  html: false,
  linkify: false,
  breaks: true,
});
answerMarkdown.renderer.rules.image = () => "";
answerMarkdown.renderer.rules.link_open = (
  tokens,
  index,
  options,
  env,
  self,
) => {
  tokens[index].attrSet("target", "_blank");
  tokens[index].attrSet("rel", "noopener noreferrer");
  return self.renderToken(tokens, index, options);
};
const answerFence = answerMarkdown.renderer.rules.fence;
answerMarkdown.renderer.rules.fence = (tokens, index, options, env, self) => {
  const token = tokens[index];
  if (token.info.trim().toLowerCase() === "json") {
    try {
      token.content = JSON.stringify(JSON.parse(token.content), null, 2) + "\n";
    } catch {}
  }
  return answerFence(tokens, index, options, env, self);
};
function renderAnswer(body, value) {
  const source =
    typeof value === "string"
      ? value
      : value == null
        ? ""
        : JSON.stringify(value, null, 2);
  let parsed;
  try {
    const candidate = JSON.parse(source);
    if (candidate && typeof candidate === "object") parsed = candidate;
  } catch {}
  if (parsed !== undefined) {
    const pre = document.createElement("pre"),
      code = document.createElement("code");
    code.className = "language-json";
    code.textContent = JSON.stringify(parsed, null, 2);
    pre.append(code);
    body.replaceChildren(pre);
    syncResponseMotion(body);
    return source;
  }
  body.innerHTML = answerMarkdown.render(source);
  syncResponseMotion(body);
  return source;
}
function syncResponseMotion(body) {
  body.querySelector(".response-motion")?.remove();
  if (body.dataset.motion !== "answer") return;
  const final = body.lastElementChild;
  if (!final || final.tagName !== "P") return;
  const marker = document.createElement("span");
  marker.className = "response-motion";
  marker.dataset.motion = "answer";
  marker.setAttribute("aria-hidden", "true");
  final.append(marker);
}
function setAnswer(answer, value) {
  answer.body.rawAnswer = renderAnswer(answer.body, value);
}
function messageAttachments(message, attachments = []) {
  const gallery = document.createElement("div");
  gallery.className = "message-images";
  for (const file of attachments) {
    const card = document.createElement("span");
    card.className = "attachment message-file";
    const icon = projectFileIcon(file.name);
    const label = document.createElement("span");
    label.textContent = file.name;
    card.append(icon, label);
    if (!file.preview_url) {
      gallery.append(card);
      continue;
    }
    const button = document.createElement("button");
    button.type = "button";
    button.className = "attachment message-image";
    button.setAttribute("aria-label", "Enlarge image " + file.name);
    const img = document.createElement("img");
    img.src = file.preview_url;
    img.alt = file.name;
    img.className = "attachment-preview";
    img.onerror = () => button.replaceWith(card);
    const name = document.createElement("span");
    name.className = "attachment-name";
    name.textContent = file.name;
    button.append(img, name);
    button.onclick = () => {
      const dialog = document.createElement("dialog");
      dialog.className = "image-modal";
      dialog.setAttribute("aria-label", file.name);
      const close = document.createElement("button");
      close.type = "button";
      close.className = "dialog-close";
      close.textContent = "×";
      close.setAttribute("aria-label", "Close image");
      close.autofocus = true;
      close.onclick = () => dialog.close();
      const full = document.createElement("img");
      full.src = file.preview_url;
      full.alt = file.name;
      dialog.append(close, full);
      dialog.onclick = (e) => {
        if (e.target === dialog) {
          const r = dialog.getBoundingClientRect();
          if (
            e.clientX < r.left ||
            e.clientX > r.right ||
            e.clientY < r.top ||
            e.clientY > r.bottom
          )
            dialog.close();
        }
      };
      dialog.onclose = () => {
        dialog.remove();
        button.focus();
      };
      document.body.append(dialog);
      dialog.showModal();
    };
    gallery.append(button);
  }
  if (gallery.childElementCount) message.body.prepend(gallery);
}
function bubble(role, text = "") {
  const el = document.createElement("article");
  el.className = "message chat-item " + role;
  const body = document.createElement("div");
  body.className = "text chat-bubble";
  if (role === "assistant") body.rawAnswer = renderAnswer(body, text);
  else body.textContent = text;
  el.append(body);
  $("messages").append(el);
  return { el, body };
}
function eventToolName(data = {}) {
  const command =
    typeof data.command_name === "string" ? data.command_name.trim() : "";
  if (command && /^[\w./+-]{1,128}$/.test(command))
    return command.split(/[\\/]/).pop().slice(0, 64);
  return (
    {
      Read: "file",
      Glob: "files",
      Grep: "text",
      webSearch: "web",
      Bash: "tool",
      commandExecution: "tool",
    }[data.tool] || ""
  );
}
function activityTitle(e) {
  const data = e.data || {},
    type = e.type,
    tool = eventToolName(data);
  const condition = executionCondition(data.condition || data.error);
  if (condition) return condition.title;
  if (type === "tool_start")
    return data.command_name && tool
      ? "Running command " + tool
      : {
          Read: "Reading file",
          Glob: "Searching files",
          Grep: "Searching text",
          webSearch: "Searching the web",
        }[data.tool] || "Running " + (tool || "tool");
  if (type === "tool_end")
    return data.status === "failed" ? "Tool failed" : "Tool finished";
  if (["thinking", "reasoning_delta", "reasoning_summary"].includes(type))
    return "Thinking";
  if (
    [
      "queued",
      "running",
      "loading",
      "planning",
      "completed",
      "failed",
      "error",
      "cancelled",
      "interrupted",
    ].includes(type)
  )
    return labels[type] || "Run interrupted";
  if (type === "plan_updated") return "Plan updated";
  if (type === "maestro_planning") return "Maestro planning";
  if (type === "maestro_plan") return "Execution plan set";
  if (type === "maestro_step") return "Specialist started working";
  return (
    {
      session_resumed: "Conversation context resumed",
      context_compacting: "Optimizing context",
      context_compacted: "Context optimized",
      validating_changes: "Validating changes",
      changes_applied: "Changes applied",
      deployment_failed: "Changes not applied",
      reload_scheduled: "Panel will refresh",
    }[type] || ""
  );
}
function appendActivityTitle(list, e) {
  list.eventIds ??= new Set();
  list.toolRows ??= new Map();
  const eventId = e.id == null ? "" : String(e.id);
  if (eventId && list.eventIds.has(eventId)) return;
  if (eventId) list.eventIds.add(eventId);
  const data = e.data || {},
    toolId = data.tool_id == null ? "" : String(data.tool_id),
    toolFailed =
      e.type === "tool_end" &&
      (data.status === "failed" || data.result?.isError === true);
  if (e.type === "tool_end" && toolId && list.toolRows.has(toolId)) {
    const row = list.toolRows.get(toolId);
    row.dataset.state = toolFailed ? "failed" : "completed";
    row.textContent = toolFailed
      ? "Failed: " + row.textContent
      : row.textContent
          .replace(/^Running/, "Ran")
          .replace(/^Reading/, "Read")
          .replace(/^Searching/, "Searched");
    return;
  }
  const title = activityTitle(e);
  if (!title) return;
  if (title === "Thinking" && list.lastElementChild?.textContent === title)
    return;
  const row = document.createElement("li");
  row.textContent = title;
  row.dataset.state = toolFailed ? "failed" : e.type;
  if (eventId) row.dataset.eventId = eventId;
  list.append(row);
  if (e.type === "tool_start" && toolId) list.toolRows.set(toolId, row);
}
function setActivitySummary(answer, text) {
  answer.activitySummary.textContent = text;
  answer.activity.hidden = false;
}
function assistant(id = "", model = $("model").value, replayTools = false) {
  const a = bubble("assistant"),
    title = document.createElement("h3"),
    badge = document.createElement("span");
  badge.className = "model-badge";
  badge.setAttribute("aria-hidden", "true");
  badge.textContent = modelIcon(model);
  title.append(
    badge,
    document.createTextNode(
      names[model] || models.find((m) => m.id === model)?.name || "Response",
    ),
  );
  a.el.prepend(title);
  const chip = document.createElement("p");
  chip.className = "run-highlight";
  chip.textContent = "⏳ Waiting to run";
  a.el.insertBefore(chip, a.body);
  const activity = document.createElement("details"),
    activitySummary = document.createElement("summary"),
    milestones = document.createElement("ol");
  activity.className = "message-activity";
  activitySummary.textContent = "Run steps";
  milestones.className = "activity-milestones";
  activity.append(activitySummary, milestones);
  activity.hidden = !replayTools;
  a.el.insertBefore(activity, a.body);
  if (replayTools)
    activity.addEventListener("toggle", () => {
      if (activity.open && !activity.dataset.loaded) {
        activity.dataset.loaded = "1";
        loadResponseTools(id, milestones).catch(() => {
          delete activity.dataset.loaded;
          const row = document.createElement("li");
          row.textContent = "Couldn't retrieve the step titles.";
          milestones.replaceChildren(row);
        });
      }
    });
  const meta = document.createElement("div");
  meta.className = "run-meta";
  a.el.append(meta);
  return { ...a, activity, activitySummary, milestones, meta, chip };
}
async function loadResponseTools(id, target) {
  target.replaceChildren();
  target.eventIds = new Set();
  target.toolRows = new Map();
  const response = await api("/v1/jobs/" + encodeURIComponent(id) + "/events", {
    signal: AbortSignal.timeout(15000),
  });
  const reader = response.body.getReader(),
    decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let end;
      while ((end = buffer.indexOf("\n\n")) >= 0) {
        const block = buffer.slice(0, end);
        buffer = buffer.slice(end + 2);
        const line = block.split("\n").find((l) => l.startsWith("data: "));
        if (!line) continue;
        const e = JSON.parse(line.slice(6));
        if (
          ![
            "answer_delta",
            "context_usage",
            "usage_metrics",
            "quota_before",
            "quota_after",
          ].includes(e.type)
        )
          appendActivityTitle(target, e);
      }
    }
  } finally {
    await reader.cancel();
  }
  if (!target.children.length) {
    const row = document.createElement("li");
    row.textContent = "No step was recorded for this run.";
    target.append(row);
  }
}
// Animation follows execution events, not token timing or locally inferred progress.
function paintMotion(mode) {
  $("status").removeAttribute("data-motion");
  for (const node of [$("activity-state"), active?.chip])
    if (node) node.removeAttribute("data-motion");
  if (active) {
    active.body.dataset.motion = mode ? "answer" : "";
    syncResponseMotion(active.body);
    active.el.setAttribute("aria-busy", String(!!mode));
  }
}
function updateMotion(type) {
  if (
    ["completed", "failed", "cancelled", "interrupted", "error"].includes(type)
  )
    paintMotion("");
  else if (type === "answer_delta") paintMotion("answer");
  else if (
    [
      "queued",
      "running",
      "loading",
      "planning",
      "thinking",
      "reasoning_delta",
      "reasoning_summary",
      "tool_start",
      "tool_end",
      "plan_updated",
      "session_resumed",
      "context_compacting",
      "context_compacted",
      "validating_changes",
      "changes_applied",
      "deployment_failed",
      "reload_scheduled",
    ].includes(type)
  )
    paintMotion("working");
}
function scroll() {
  const box = $("messages");
  if (box.scrollHeight - box.scrollTop - box.clientHeight < 250)
    box.scrollTop = box.scrollHeight;
  updateLatest();
}
function event(e) {
  if (e.id <= last) return;
  last = e.id;
  if (e.type === "session_turn_started") return;
  if (e.type === "approval_required") {
    showApproval(e.data);
    return;
  }
  if (e.type === "approval_resolved") {
    document.getElementById("approval-" + e.data.approval_id)?.remove();
    return;
  }
  if (active && !["context_usage", "usage_metrics"].includes(e.type)) {
    appendActivityTitle(active.milestones, e);
    if (active.milestones.children.length) {
      active.activity.hidden = false;
      setActivitySummary(
        active,
        ["completed", "failed", "cancelled", "interrupted"].includes(e.type)
          ? labels[e.type] || "Run steps"
          : "Working…",
      );
    }
  }
  recordActivity(e);
  updateMotion(e.type);
  if (active) active.chip.textContent = $("activity-state").textContent;
  if (e.type === "usage_metrics") {
    paintLocalUsage(e.data);
    return;
  }
  if (e.type === "context_usage") {
    paintContext(e.data, e.data.metrics);
    return;
  }
  if (e.type === "plan_updated") {
    status("Plan updated");
  } else if (e.type === "answer_delta") {
    active.body.rawAnswer = (active.body.rawAnswer || "") + e.data.text;
    renderAnswer(active.body, active.body.rawAnswer);
    status("Receiving response…");
  } else if (e.type === "reasoning_delta" || e.type === "reasoning_summary") {
    if (active) setActivitySummary(active, "Thinking…");
    status("Reasoning…");
  } else if (e.type === "quota_before" || e.type === "quota_after") {
    if (selected()?.backend === "codex")
      quotaSnapshot(e.type.endsWith("before") ? "before" : "after", e.data);
  } else if (e.type === "quota_update") {
    if (
      e.data?.provider === selected()?.backend &&
      ["codex", "claude"].includes(selected()?.backend)
    )
      void quota();
    return;
  } else {
    status(labels[e.type] || e.type);
  }
  scroll();
}
function executionCondition(code) {
  if (
    ["claude_authentication_required", "claude_authentication_failed"].includes(
      code,
    )
  )
    return {
      title: "Renew Claude access",
      message:
        "Your Claude access needs to be renewed. In the admin panel, find Claude Code and click “Renew access”. Complete the sign-in in the browser and send your message again.",
    };
  if (["claude_quota_exhausted", "claude_rate_limit"].includes(code))
    return {
      title: "Wait for quota renewal",
      message:
        "Your Claude quota is temporarily exhausted. Wait for it to renew or select a different provider to continue this conversation.",
    };
  return null;
}
function executionError(error) {
  const condition = executionCondition(error);
  if (condition) return condition.message;
  if (error === "cli_missing") return userErrors.cli_missing;
  if (
    /context_limit_exceeded|exceed_context_size|exceeds the available context|maximum context length|source_context_limit|conversation_context_limit|context_window_exceeded/i.test(
      String(error),
    )
  )
    return "Couldn't prepare or process the context for this attempt. The history and files were preserved. You can continue this conversation; if the limit persists, use a model with reading tools or reduce the content sent at once.";
  return "The run did not finish: " + error;
}
async function result(
  expectedJob = job,
  expectedController = controller,
  snapshot = null,
) {
  const r = snapshot || (await json("/v1/jobs/" + expectedJob));
  if (job !== expectedJob || controller !== expectedController) return r;
  const terminal = {
    completed: "Completed",
    failed: "Failed run",
    cancelled: "Run cancelled",
    interrupted: "Run interrupted",
  };
  const condition = executionCondition(r.result?.condition || r.result?.error);
  updateMotion(r.state);
  $("activity-state").textContent = condition
    ? "ℹ " + condition.title
    : (activityIcons[r.state] || "•") + " " + (labels[r.state] || r.state);
  if (active) {
    active.chip.textContent = $("activity-state").textContent;
    const data = r.result || {},
      seconds = Number(data.total_seconds);
    if (data.context_usage) paintContext(data.context_usage, data.metrics);
    else if (data.metrics) paintLocalUsage(data.metrics);
    if (data.answer !== undefined) setAnswer(active, data.answer);
    if (condition) setAnswer(active, condition.message);
    else if (data.error) setAnswer(active, executionError(data.error));
    if (r.state === "cancelled" && !active.body.rawAnswer)
      setAnswer(active, "Run cancelled.");
    const modelId = data.model || $("model").value,
      model = modelId
        ? modelIcon(modelId) +
          " " +
          (names[modelId] ||
            models.find((m) => m.id === modelId)?.name ||
            modelId)
        : "",
      duration =
        Number.isFinite(seconds) && seconds > 0
          ? seconds.toFixed(1) + " s"
          : "";
    active.meta.textContent = [model, duration].filter(Boolean).join(" · ");
    if (data.deployment)
      active.meta.textContent +=
        (active.meta.textContent ? " · " : "") +
        (data.deployment.applied ? "Changes applied" : "Changes not applied");
    setActivitySummary(
      active,
      condition
        ? condition.title
        : terminal[r.state]
          ? duration
            ? "Worked for " + duration
            : terminal[r.state]
          : "Working…",
    );
    if (data.incomplete)
      status("Incomplete response. Narrow the scope and try again.");
    else status(condition?.title || terminal[r.state] || r.state);
    if (data.deployment)
      status(
        data.deployment.applied
          ? "Changes applied to the project. Checking for a panel update…"
          : "Changes not applied.",
      );
    if (selected()?.backend === "codex") {
      if (data.quota_before) quotaSnapshot("before", data.quota_before);
      if (data.quota_after) quotaSnapshot("after", data.quota_after);
    }
  }
  if (["completed", "failed", "cancelled", "interrupted"].includes(r.state)) {
    parent = queuedTurns.at(-1)?.id || job;
    const current = conversations.find((c) => c.id === conversation);
    if (current) {
      current.state = r.state;
      current.last_job_id = r.id || expectedJob;
      observeConversation(current);
      if (r.state === "completed")
        conversationActivity[current.id].unread = document.hidden;
      saveConversationActivity();
      renderProjects();
      if ($("conversation-search-dialog").open) renderConversationSearch();
    }
  }
  saveView();
  return r;
}
async function watchQueuedTurn() {
  const next = queuedTurns.shift();
  job = next.id;
  active = next.response;
  last = 0;
  beginActivity(job);
  await watch();
}
async function watch(retries = 0) {
  streamDisconnected = false;
  $("resume-execution").hidden = true;
  if (controller) controller.abort();
  controller = new AbortController();
  const current = controller;
  setBusy(true);
  paintMotion("working");
  try {
    const r = await api("/v1/jobs/" + job + "/events", {
      headers: { "Last-Event-ID": String(last) },
      signal: controller.signal,
    });
    const reader = r.body.getReader(),
      decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let end;
      while ((end = buffer.indexOf("\n\n")) >= 0) {
        const block = buffer.slice(0, end);
        buffer = buffer.slice(end + 2);
        const data = block.split("\n").find((l) => l.startsWith("data: "));
        if (controller !== current) return;
        if (data) event(JSON.parse(data.slice(6)));
      }
    }
    if (controller !== current) return;
    const final = await result();
    if (controller !== current) return;
    if (["running", "queued"].includes(final.state)) {
      if (retries < 3) {
        status("Reconnecting to the running execution…");
        await new Promise((resolve) => setTimeout(resolve, 1000));
        if (controller !== current) return;
        return await watch(retries + 1);
      }
      throw Error("stream_closed");
    }
    if (controller !== current) return;
    await quota();
    await history();
    if (controller === current && queuedTurns.length)
      return await watchQueuedTurn();
  } catch (e) {
    if (controller === current && e.name !== "AbortError") {
      paintMotion("");
      $("activity-state").textContent = "⚠ Connection lost";
      streamDisconnected = true;
      $("resume-execution").hidden = false;
      status(
        "Connection lost. The run may continue on the server. Use Resume tracking or Cancel run.",
      );
      await history();
    }
  } finally {
    if (controller === current) {
      setBusy(false);
      checkVersion();
    }
  }
}
async function load(id, legacy = false, restoredView = null) {
  if (submitting || cancelling || uploads) return;
  const request = ++conversationLoad,
    priorDraft = $("prompt").value;
  loading = true;
  if (controller) {
    controller.abort();
    controller = null;
  }
  setBusy(true);
  try {
    let data;
    try {
      data = legacy
        ? { turns: [await json("/v1/jobs/" + encodeURIComponent(id))] }
        : await json("/v1/conversations/" + encodeURIComponent(id));
    } catch (e) {
      if (e.status !== 404 || !legacyHistory) throw e;
      data = { turns: [await json("/v1/jobs/" + encodeURIComponent(id))] };
    }
    if (request !== conversationLoad) return;
    if (!data.turns?.length) throw Error("Empty conversation");
    queuedTurns = [];
    parent = null;
    job = "";
    last = 0;
    resetActivity();
    setConversationTitle(
      data.title ||
        conversations.find((item) => item.id === id)?.title ||
        "New Conversation",
    );
    executionMode =
      data.execution_mode ||
      data.turns[0].request?.execution_mode ||
      conversations.find((c) => c.id === id)?.execution_mode ||
      conversations.find((c) => c.id === id)?.execution?.execution_mode ||
      "native";
    conversation = id;
    if (!restoredView) saveView();
    files = [];
    renderFiles();
    $("messages").replaceChildren();
    $("prompt").value = "";
    for (const r of data.turns) {
      $("project").value = r.project;
      syncActiveProjectBadge();
      const model =
        r.request?.backend === "qwen" ? "qwen-local" : r.request?.model;
      if (models.some((m) => m.id === model)) {
        $("model").value = model;
        updateEfforts();
        $("effort").value = r.request?.effort || $("effort").value;
      }
      messageAttachments(
        bubble("user", r.request?.prompt || "Previous run"),
        r.attachments,
      );
      active = assistant(r.id, model, !["queued", "running"].includes(r.state));
      job = r.id;
      if (["queued", "running"].includes(r.state))
        queuedTurns.push({ id: r.id, response: active });
      if (
        r !== data.turns[data.turns.length - 1] &&
        !["queued", "running"].includes(r.state)
      ) {
        const condition = executionCondition(
          r.result?.condition || r.result?.error,
        );
        setAnswer(
          active,
          condition?.message ??
            r.result?.answer ??
            (r.result?.error ? executionError(r.result.error) : r.state),
        );
        active.chip.textContent = condition
          ? "ℹ " + condition.title
          : (activityIcons[r.state] || "•") +
            " " +
            (labels[r.state] || r.state);
        const seconds = Number(r.result?.total_seconds),
          duration =
            Number.isFinite(seconds) && seconds > 0
              ? seconds.toFixed(1) + " s"
              : "";
        active.meta.textContent = [r.result?.model || model || "", duration]
          .filter(Boolean)
          .join(" · ");
        setActivitySummary(
          active,
          condition
            ? condition.title
            : duration
              ? "Worked for " + duration
              : labels[r.state] || "Run steps",
        );
      }
    }
    $("access-mode").value = data.turns.at(-1).request?.access_mode || "ask";
    syncAccessMode();
    void quota();
    parent = job;
    if (queuedTurns.length) {
      const next = queuedTurns.shift();
      job = next.id;
      active = next.response;
    }
    beginActivity(job);
    resetProjectFiles();
    await refreshProjectPermissions();
    if (request !== conversationLoad) return;
    expandedProjects.set($("project").value, true);
    renderProjects();
    $("sidebar")
      .querySelector('.conversation-row > button[aria-current="true"]')
      ?.scrollIntoView({ block: "nearest" });
    last = 0;
    $("sidebar").classList.remove("open");
    loading = false;
    if (restoredView) restoreView(restoredView);
    const latest = data.turns.find((turn) => turn.id === job);
    if (
      ["completed", "failed", "cancelled", "interrupted"].includes(latest.state)
    )
      await result(job, controller, latest);
    else if (!restoredView) await watch();
    return ["queued", "running"].includes(latest.state);
  } catch (e) {
    if (request !== conversationLoad) return;
    loading = false;
    setBusy(false);
    $("prompt").value = priorDraft;
    updateComposer();
    $("sidebar").classList.remove("open");
    status(
      "Couldn't open the conversation. Your draft was preserved: " + e.message,
    );
  } finally {
    if (request === conversationLoad) {
      loading = false;
      setBusy(false);
    }
  }
}
async function upload(list) {
  if (!canUpload()) {
    status("Couldn't attach: attachments are disabled in the admin panel.");
    return;
  }
  if (busy || loading || uploads) return;
  uploads++;
  setBusy(busy);
  $("project").disabled = true;
  renderFiles();
  try {
    for (const f of Array.from(list)) {
      if (files.length >= MAX_ATTACHMENTS) {
        status(
          "Limit of 20 attachments reached. Remove one before adding another.",
        );
        break;
      }
      const video = /\.mp4$/i.test(f.name) || f.type === "video/mp4",
        audio = /\.(wav|mp3|m4a|ogg|flac|webm|aac|opus)$/i.test(f.name);
      if (f.size > MAX_ATTACHMENT_BYTES) {
        status(f.name + ": The per-file limit is 100 MiB.");
        continue;
      }
      if (video && !canAttachVideo()) {
        status(
          f.name +
            ": MP4 unavailable for this model or mode. Select a model with detected video support.",
        );
        continue;
      }
      status(
        "Uploading and preparing " +
          f.name +
          (video
            ? "… Extracting frames and checking local speech transcription."
            : audio
              ? "… Transcribing speech locally."
              : "…"),
      );
      try {
        const r = await json(
          "/v1/files?project_id=" +
            encodeURIComponent($("project").value) +
            "&backend=" +
            encodeURIComponent(selected().backend) +
            "&model=" +
            encodeURIComponent(selected().id) +
            "&execution_mode=" +
            encodeURIComponent(executionMode),
          {
            method: "POST",
            headers: { "X-Filename": encodeURIComponent(f.name) },
            body: f,
            signal: AbortSignal.timeout(
              audio || video ? AUDIO_ATTACHMENT_TIMEOUT_MS : 600000,
            ),
          },
        );
        files.push({ id: r.file_id, name: f.name, preview_url: r.preview_url });
        renderFiles();
        saveView();
        status("File received.");
      } catch (e) {
        attachmentNotice(f.name, e.code);
        status("Couldn't upload: " + attachmentError(e.code || e.message));
      }
    }
  } finally {
    uploads--;
    setBusy(busy);
    renderFiles();
    updateComposer();
  }
}
$("files-retry").onclick = () =>
  fileTree.basePath
    ? loadProjectFileDirectory(fileTree.rootId, fileTree.basePath)
    : loadProjectFileRoots(true);
let sendCooldownUntil = 0,
  sendCooldownTimer = 0;
function sendCooldownSeconds() {
  const seconds = Math.ceil((sendCooldownUntil - Date.now()) / 1000);
  if (seconds <= 0 && sendCooldownTimer) {
    clearInterval(sendCooldownTimer);
    sendCooldownTimer = 0;
  }
  return Math.max(0, seconds);
}
function startSendCooldown(milliseconds) {
  sendCooldownUntil = Date.now() + milliseconds;
  clearInterval(sendCooldownTimer);
  sendCooldownTimer = setInterval(updateComposer, 1000);
  updateComposer();
}
async function send() {
  if (
    submitting ||
    cancelling ||
    loading ||
    uploads ||
    policyPending ||
    !selected() ||
    sendCooldownSeconds()
  )
    return;
  const following = busy && !!job;
  const draft = $("prompt").value,
    prompt = draft.trim();
  if (!prompt) return;
  const reserved = prompt.match(/(?:^|\s)(@@|\/\/)[^\s@/]+/);
  if (reserved) {
    status(
      reserved[1].startsWith("@@")
        ? "Tail Harness agents are not available yet."
        : "Tail Harness skills and commands are not available yet.",
    );
    return;
  }
  syncResourceSelections();
  const stale = [...invalidResourceTokens].find((token) =>
    prompt.split(/\s+/).includes(token),
  );
  if (stale) {
    status(
      "The resource " +
        stale +
        " was invalidated by the project or engine change. Select it again before sending.",
    );
    return;
  }
  const m = selected();
  if (!supportedExecutionModes().includes(executionMode)) {
    status(
      parent
        ? "This model doesn't offer this conversation's mode. Choose a different model or start a new conversation."
        : "This model doesn't offer the selected mode. Choose a different model or change the mode.",
    );
    return;
  }
  if (files.some((f) => /\.mp4$/i.test(f.name)) && !canAttachVideo()) {
    status(
      "MP4 attachments require a different model or mode. Change the selection or remove the videos before sending.",
    );
    return;
  }
  if (files.length && !canUpload()) {
    status(
      "This model doesn't allow attachments. Remove the files or choose a model with that permission.",
    );
    return;
  }
  syncResourceSelections();
  rememberSelection();
  submitting = true;
  setBusy(true);
  status("Sending request to the server…");
  let sentJob = null;
  const clearSentDraft = () => {
    if ($("prompt").value === draft) {
      $("prompt").value = "";
      resourceSelections = [];
      invalidResourceTokens.clear();
    }
  };
  try {
    const data = {
      project_id: $("project").value,
      prompt,
      file_ids: files.map((f) => f.id),
      backend: m.backend,
      model: m.id,
      effort: $("effort").value,
      access_mode: $("access-mode").value,
      resource_selections: resourceSelections.map(
        ({ id, revision, token }) => ({ id, revision, token }),
      ),
    };
    if (parent) data.parent_job_id = parent;
    else if (Array.isArray(m.execution_modes))
      data.execution_mode = executionMode;
    if (m.backend === "codex") {
      status("Checking quota before running…");
      quotaSnapshot("before", await json("/v1/usage"));
    }
    const r = await json("/v1/jobs", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": submissionKey(data),
      },
      body: JSON.stringify(data),
    });
    clearSubmission();
    $("welcome")?.remove();
    messageAttachments(bubble("user", prompt), files);
    if (following) {
      queuedTurns.push({ id: r.job_id, response: assistant(r.job_id, m.id) });
      parent = r.job_id;
      sentJob = r.job_id;
      submitting = false;
      setBusy(busy);
      files = [];
      renderFiles();
      clearSentDraft();
      updateComposer();
      saveView();
      status("Message sent. Waiting for the current response to finish.");
      await history();
      if (!busy && queuedTurns.length) void watchQueuedTurn();
      return;
    }
    active = assistant(r.job_id, m.id);
    beginActivity(r.job_id);
    job = r.job_id;
    sentJob = job;
    submitting = false;
    $("cancel").disabled = false;
    parent = job;
    if (!conversation) {
      conversation = job;
      setConversationTitle(prompt);
    }
    files = [];
    renderFiles();
    last = 0;
    clearSentDraft();
    updateComposer();
    saveView();
    $("messages").scrollTop = $("messages").scrollHeight;
    await history();
    await watch();
  } catch (e) {
    status(
      "Couldn't run: " +
        e.message +
        (e.status === 429 && submitting ? " Your draft was preserved." : ""),
    );
    if (e.status === 429) startSendCooldown(e.retryAfter || 5000);
  } finally {
    if (!sentJob) {
      submitting = false;
      setBusy(following ? busy : false);
    } else if (job === sentJob && !submitting && !following) setBusy(false);
  }
}
function resetProjectFiles() {
  const project = $("project").value;
  if (fileTree.project === project) return;
  fileTree.project = project;
  fileTree.selected.clear();
  fileTree.anchor = "";
  renderProjectFileTree();
  renderProjectFileSelection();
}
function renderProjectFileSelection() {
  const count = fileTree.selected.size,
    node = $("files-selection-count");
  node.textContent = count ? `${count} selected` : "";
  node.hidden = !count;
}
function renderProjectFileTree() {
  const entries =
    fileTree.cache.get(fileTree.rootId + "\0" + fileTree.basePath) || [];
  $("files-tree").replaceChildren();
  const group = document.createElement("ul");
  group.setAttribute("role", "group");
  renderProjectFileEntries(group, entries);
  $("files-tree").append(group);
  renderProjectFileSelection();
}
function renderProjectFileEntries(list, entries, tree = fileTree) {
  for (const entry of entries.filter(
    (entry) => !tree.foldersOnly || entry.type === "directory",
  )) {
    const item = document.createElement("li");
    item.setAttribute("role", "treeitem");
    item.setAttribute("aria-selected", String(tree.selected.has(entry.path)));
    item.tabIndex = 0;
    item.dataset.path = entry.path;
    const row = document.createElement("div");
    row.className = "project-file-row";
    if (entry.type === "directory") {
      const open = tree.expanded.has(entry.path),
        toggle = document.createElement("button");
      toggle.type = "button";
      toggle.className = "file-chevron";
      toggle.setAttribute(
        "aria-label",
        (open ? "Collapse " : "Expand ") + entry.name,
      );
      toggle.setAttribute("aria-expanded", String(open));
      toggle.title = (open ? "Collapse " : "Expand ") + entry.name;
      toggle.innerHTML =
        '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m9 18 6-6-6-6"/></svg>';
      toggle.onclick = () =>
        tree.foldersOnly
          ? toggleProjectFolder(entry)
          : toggleProjectDirectory(entry);
      const name = document.createElement("span");
      name.textContent = entry.name;
      name.title = entry.name;
      row.append(toggle, projectFileIcon(entry.name, true, open), name);
      item.append(row);
      if (open) {
        const childList = document.createElement("ul");
        childList.setAttribute("role", "group");
        childList.className = "project-file-children";
        const children = tree.cache.get(tree.rootId + "\0" + entry.path);
        if (children) renderProjectFileEntries(childList, children, tree);
        else {
          const loading = document.createElement("li");
          loading.textContent = "Loading…";
          loading.setAttribute("role", "status");
          childList.append(loading);
        }
        item.append(childList);
      }
    } else {
      const name = document.createElement("span");
      name.textContent = entry.name;
      name.title = entry.name;
      row.append(projectFileIcon(entry.name), name);
      item.append(row);
    }
    if (tree.foldersOnly) {
      item.onclick = (e) => {
        if (
          e.target.closest("button") ||
          e.target.closest("[role=treeitem]") !== item
        )
          return;
        selectProjectFolder(entry);
      };
      item.onkeydown = (e) => {
        if (e.target !== item) return;
        if ([" ", "Enter"].includes(e.key)) {
          e.preventDefault();
          selectProjectFolder(entry);
        } else if (
          (e.key === "ArrowRight" && !tree.expanded.has(entry.path)) ||
          (e.key === "ArrowLeft" && tree.expanded.has(entry.path))
        ) {
          e.preventDefault();
          void toggleProjectFolder(entry);
        }
      };
      list.append(item);
      continue;
    }
    item.onclick = (e) => {
      if (
        e.target.closest("button") ||
        e.target.closest('[role="treeitem"]') !== item
      )
        return;
      selectProjectFileEntry(item, entry, e);
    };
    item.onkeydown = (e) => {
      if (e.target !== item) return;
      if (e.key === " ") {
        e.preventDefault();
        selectProjectFileEntry(item, entry, {
          ctrlKey: true,
          shiftKey: e.shiftKey,
        });
      } else if (e.key === "Enter") {
        e.preventDefault();
        if (entry.type === "directory") void toggleProjectDirectory(entry);
        else {
          const paths = tree.selected.has(entry.path)
            ? Array.from(tree.selected)
            : [entry.path];
          void attachSelectedProjectFiles({ root_id: tree.rootId, paths });
        }
      }
    };
    if (entry.type !== "directory") {
      item.setAttribute("aria-keyshortcuts", "Enter Space");
      item.title = "Enter to attach; Space to select.";
    }
    item.draggable =
      canUpload() && !busy && !uploads && files.length < MAX_ATTACHMENTS;
    item.ondragstart = (e) => {
      if (e.target.closest('[role="treeitem"]') !== item) return;
      if (!canUpload() || busy || uploads || files.length >= MAX_ATTACHMENTS) {
        e.preventDefault();
        return;
      }
      if (!tree.selected.has(entry.path)) {
        tree.selected.clear();
        tree.selected.add(entry.path);
        tree.anchor = entry.path;
        for (const node of $("files-tree").querySelectorAll("[role=treeitem]"))
          node.setAttribute(
            "aria-selected",
            String(tree.selected.has(node.dataset.path)),
          );
        renderProjectFileSelection();
      }
      const paths = Array.from(tree.selected);
      e.dataTransfer.effectAllowed = "copy";
      e.dataTransfer.setData(
        "application/x-tail-authorized-project-files",
        JSON.stringify({ root_id: tree.rootId, paths }),
      );
    };
    list.append(item);
  }
}
function selectProjectFileEntry(item, entry, event = {}) {
  if (!canUpload() || uploads > 0) return;
  if (files.length >= MAX_ATTACHMENTS) {
    status(
      "Limit of 20 attachments reached. Remove one before adding another.",
    );
    return;
  }
  const multi = event.ctrlKey || event.metaKey;
  if (event.shiftKey && fileTree.anchor) {
    const visible = Array.from(
        $("files-tree").querySelectorAll('[role="treeitem"]'),
      ),
      start = visible.findIndex(
        (node) => node.dataset.path === fileTree.anchor,
      ),
      end = visible.indexOf(item);
    if (start >= 0 && end >= 0) {
      fileTree.selected.clear();
      for (const node of visible.slice(
        Math.min(start, end),
        Math.max(start, end) + 1,
      ))
        fileTree.selected.add(node.dataset.path);
    }
  } else if (multi) {
    if (fileTree.selected.has(entry.path)) fileTree.selected.delete(entry.path);
    else if (files.length + fileTree.selected.size < MAX_ATTACHMENTS)
      fileTree.selected.add(entry.path);
    fileTree.anchor = entry.path;
  } else {
    fileTree.selected.clear();
    fileTree.selected.add(entry.path);
    fileTree.anchor = entry.path;
  }
  if (fileTree.selected.size > MAX_ATTACHMENTS - files.length) {
    fileTree.selected = new Set(
      [...fileTree.selected].slice(0, MAX_ATTACHMENTS - files.length),
    );
    status(
      "Limit of 20 attachments per message. The selection was limited to the available slots.",
    );
  }
  for (const node of $("files-tree").querySelectorAll('[role="treeitem"]'))
    node.setAttribute(
      "aria-selected",
      String(fileTree.selected.has(node.dataset.path)),
    );
  renderProjectFileSelection();
}
function projectFileIcon(filename = "", folder = false, open = false) {
  const theme = window.TailFileIcons,
    name = filename.replaceAll("\\", "/").split("/").pop().toLowerCase();
  if (!theme) {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    svg.classList.add("project-file-icon");
    const path = document.createElementNS(svg.namespaceURI, "path");
    path.setAttribute("fill", "currentColor");
    path.setAttribute("d", folder ? "M3 5h7l2 2h9v13H3z" : "M5 2h9l5 5v15H5z");
    svg.append(path);
    return svg;
  }
  let id;
  if (folder) {
    const names = theme[open ? "folderNamesExpanded" : "folderNames"];
    id = Object.hasOwn(names, name)
      ? names[name]
      : theme[open ? "folderExpanded" : "folder"];
  } else {
    id = Object.hasOwn(theme.fileNames, name) ? theme.fileNames[name] : null;
    // Match compound extensions (e.g. spec.ts) before their shorter suffixes.
    const parts = name.split(".");
    for (let i = 1; !id && i < parts.length; i++) {
      const suffix = parts.slice(i).join(".");
      if (Object.hasOwn(theme.fileExtensions, suffix))
        id = theme.fileExtensions[suffix];
    }
    id = id || theme.file;
  }
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 16 16");
  svg.setAttribute("aria-hidden", "true");
  svg.classList.add("project-file-icon");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", "/assets/file-icons.svg#" + id);
  svg.append(use);
  return svg;
}
async function navigateProjectFolder(project) {
  try {
    const data = await json("/v1/projects"),
      folder = data.details?.[project]?.root;
    if (!folder) throw new Error("This project has no associated folder.");
    // Keep the active conversation and its draft independent of file navigation.
    setPanelView("files");
    setPanelOpen(true);
    await loadProjectFileRoots(true);
    const destination = await json(
      "/v1/project-files?view=tree&navigate_project=1&project_id=" +
        encodeURIComponent(project) +
        "&start=1&limit=100",
    );
    ++fileTree.request;
    fileTree.rootId = destination.root_id;
    fileTree.basePath = destination.path;
    fileTree.expanded.clear();
    fileTree.selected.clear();
    fileTree.anchor = "";
    $("project-folder-location")?.remove();
    const label = document.createElement("div");
    label.id = "project-folder-location";
    label.className = "file-tree-note";
    label.textContent = folder;
    label.title = folder;
    for (const button of $("files-roots").querySelectorAll(".file-root"))
      button.setAttribute("aria-expanded", "false");
    $("files-roots").append(label, $("files-tree"));
    $("files-tree").hidden = false;
    $("files-no-roots").hidden = true;
    $("sidebar").classList.remove("open");
    await loadProjectFileDirectory(fileTree.rootId, fileTree.basePath);
  } catch (error) {
    status("Couldn't navigate to the project folder: " + error.message);
  }
}
async function loadProjectFileRoots(force = false) {
  if (
    !interfaceReady ||
    $("activity-panel").hidden ||
    rightPanelView !== "files"
  )
    return;
  const project = $("project").value;
  if (fileTree.project !== project) resetProjectFiles();
  if (fileTree.ready && !force) return;
  if (force) {
    fileTree.cache.clear();
    fileTree.expanded.clear();
  }
  const request = ++fileTree.request;
  fileTree.ready = false;
  $("files-loading").hidden = false;
  $("files-error").hidden = true;
  $("files-retry").hidden = true;
  $("files-no-roots").hidden = true;
  try {
    const result = await json("/v1/project-files?view=tree&start=1&limit=100");
    if (request !== fileTree.request) return;
    fileTree.roots = (result.roots || [])
      .filter((root) => root.id === "home" || root.id === "media-user")
      .sort((a, b) => Number(b.id === "home") - Number(a.id === "home"));
    fileTree.ready = true;
    $("files-loading").hidden = true;
    $("files-roots").replaceChildren($("files-tree"));
    if (!fileTree.roots.length) {
      $("files-no-roots").hidden = false;
      $("files-tree").replaceChildren();
      return;
    }
    for (const root of fileTree.roots) {
      const section = document.createElement("div"),
        button = document.createElement("button");
      section.className = "file-root-section";
      button.type = "button";
      button.dataset.rootId = root.id;
      button.className = "file-root";
      button.setAttribute("aria-expanded", "false");
      button.setAttribute("aria-controls", "files-tree");
      button.innerHTML =
        '<svg class="root-chevron" viewBox="0 0 24 24" aria-hidden="true"><path d="m9 18 6-6-6-6"/></svg>';
      button.append(
        projectFileIcon(root.id, true),
        document.createTextNode(
          root.id === "home" ? "Local Folders" : "External Folders",
        ),
      );
      button.onclick = () => {
        if (root.id === fileTree.rootId && !$("files-tree").hidden) {
          $("files-tree").hidden = true;
          button.setAttribute("aria-expanded", "false");
        } else void selectProjectFileRoot(root);
      };
      section.append(button);
      $("files-roots").append(section);
    }
    const root =
      fileTree.roots.find((item) => item.id === fileTree.rootId) ||
      fileTree.roots.find((item) => item.id === "home") ||
      fileTree.roots[0];
    await selectProjectFileRoot(root);
  } catch (error) {
    if (request !== fileTree.request) return;
    $("files-loading").hidden = true;
    $("files-error").hidden = false;
    $("files-error").textContent =
      "Couldn't load the authorized folders: " + error.message;
    $("files-retry").hidden = false;
  }
}
async function selectProjectFileRoot(root) {
  if (!root) return;
  const changed = fileTree.rootId !== root.id || fileTree.basePath !== "";
  fileTree.basePath = "";
  $("project-folder-location")?.remove();
  fileTree.rootId = root.id;
  if (changed) {
    fileTree.selected.clear();
    fileTree.anchor = "";
    fileTree.expanded.clear();
    $("files-tree").replaceChildren();
  }
  for (const button of $("files-roots").querySelectorAll(".file-root")) {
    const active = button.dataset.rootId === root.id;
    button.setAttribute("aria-expanded", String(active));
    if (active) button.after($("files-tree"));
  }
  $("files-tree").hidden = false;
  renderProjectFileSelection();
  if (fileTree.cache.has(root.id + "\0")) renderProjectFileTree();
  else await loadProjectFileDirectory(root.id, "");
}

async function loadProjectFileDirectory(rootId, path) {
  const version = fileTree.request,
    key = rootId + "\0" + path;
  try {
    const data = await json(
      "/v1/project-files?view=tree&root_id=" +
        encodeURIComponent(rootId) +
        "&path=" +
        encodeURIComponent(path) +
        "&start=1&limit=100",
    );
    if (version !== fileTree.request || rootId !== fileTree.rootId) return;
    fileTree.cache.set(key, data.entries || []);
    $("files-error").hidden = true;
    $("files-retry").hidden = true;
    if (path === "") $("files-no-roots").hidden = true;
    renderProjectFileTree();
    if (data.limited) {
      $("files-error").hidden = false;
      $("files-error").textContent =
        "Showing the first 100 items in this folder.";
      $("files-error").className = "file-tree-note";
    }
  } catch (error) {
    if (version !== fileTree.request || rootId !== fileTree.rootId) return;
    fileTree.expanded.delete(path);
    renderProjectFileTree();
    $("files-error").hidden = false;
    $("files-error").className = "";
    $("files-error").textContent =
      "Couldn't open this folder: " + error.message;
    $("files-retry").hidden = false;
  }
}
async function toggleProjectDirectory(entry) {
  if (fileTree.expanded.has(entry.path)) {
    fileTree.expanded.delete(entry.path);
    renderProjectFileTree();
    return;
  }
  fileTree.expanded.add(entry.path);
  renderProjectFileTree();
  const key = fileTree.rootId + "\0" + entry.path;
  if (!fileTree.cache.has(key))
    await loadProjectFileDirectory(fileTree.rootId, entry.path);
}
async function attachSelectedProjectFiles(
  selection = {
    root_id: fileTree.rootId,
    paths: Array.from(fileTree.selected),
  },
) {
  if (busy || loading || uploads) return;
  if (!canUpload()) {
    status("This model doesn't allow attaching files.");
    return;
  }
  const maxFiles = Math.max(0, MAX_ATTACHMENTS - files.length),
    paths = [...new Set(selection.paths || [])];
  if (!selection.root_id || !paths.length || !maxFiles) {
    status(
      maxFiles
        ? "Select files to attach."
        : "The limit of 20 attachments has already been reached.",
    );
    return;
  }
  uploads++;
  setBusy(busy);
  renderProjectFileTree();
  status(
    "Attaching selected files… Audio is transcribed locally; please wait for it to finish.",
  );
  try {
    const url =
      "/v1/project-files/attach?project_id=" +
      encodeURIComponent($("project").value) +
      "&max_files=" +
      maxFiles;
    const result = await json(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        root_id: selection.root_id,
        paths,
        backend: selected().backend,
        model: selected().id,
        execution_mode: executionMode,
      }),
      signal: AbortSignal.timeout(maxFiles * AUDIO_ATTACHMENT_TIMEOUT_MS),
    });
    for (const attachment of result.attachments || [])
      files.push({
        id: attachment.file_id,
        name: attachment.name,
        preview_url: attachment.preview_url,
      });
    renderFiles();
    saveView();
    if (selection.root_id === fileTree.rootId) {
      for (const path of paths) fileTree.selected.delete(path);
      renderProjectFileTree();
    }
    for (const item of (result.skipped || []).slice(0, 10))
      attachmentNotice(item.path, item.reason);
    const skipped = (result.skipped || []).length;
    status(
      `${(result.attachments || []).length} file(s) attached${skipped ? `; ${skipped} item(s) unavailable or skipped` : ""}.${skipped > 10 ? " Showing the first 10 notices." : ""}`,
    );
  } catch (error) {
    status("Couldn't attach the selection: " + error.message);
  } finally {
    uploads--;
    setBusy(busy);
    renderProjectFileTree();
  }
}
function renderFiles() {
  $("attachments").replaceChildren(
    ...files.map((f, i) => {
      const el = document.createElement("span");
      el.className = "attachment";
      if (f.preview_url) {
        const img = document.createElement("img");
        img.src = f.preview_url;
        img.alt = f.name;
        img.className = "attachment-preview";
        img.onerror = () => img.remove();
        el.append(img);
        el.classList.add("image-attachment");
      }
      if (!f.preview_url) el.append(projectFileIcon(f.name));
      const name = document.createElement("span");
      name.textContent = f.name;
      name.className = "attachment-name";
      el.append(name);
      const b = document.createElement("button");
      b.type = "button";
      b.append(TailUI.icon("x"));
      b.title = "Remove from the next message";
      b.setAttribute("aria-label", "Remove attachment " + f.name);
      b.disabled = busy || uploads > 0;
      b.onclick = () => {
        files.splice(i, 1);
        renderFiles();
        saveView();
        renderProjectFileTree();
        updateComposer();
        const buttons = $("attachments").querySelectorAll("button");
        (buttons[Math.min(i, buttons.length - 1)] || $("attach")).focus();
      };
      el.append(b);
      return el;
    }),
  );
  let count = $("attachment-count");
  if (!count) {
    count = document.createElement("span");
    count.id = "attachment-count";
    count.setAttribute("role", "status");
    $("attachments").after(count);
  }
  count.hidden = files.length === 0;
  count.textContent =
    files.length + " / " + MAX_ATTACHMENTS + " files attached";
  renderProjectFileSelection();
}
$("send").onclick = send;
$("prompt").oninput = () => {
  syncResourceSelections();
  openResourceMenu();
};
$("prompt").onkeydown = (e) => {
  if (resourceKeydown(e)) return;
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing && e.keyCode !== 229) {
    e.preventDefault();
    send();
  }
};
$("cancel").onclick = async () => {
  if (submitting || cancelling || !job) return;
  cancelling = true;
  $("cancel").disabled = true;
  try {
    await post("/v1/jobs/" + job + "/cancel", {});
    status("Cancelling…");
    if (streamDisconnected) await watch();
  } catch (e) {
    status("Couldn't cancel: " + e.message);
  } finally {
    cancelling = false;
    setBusy(busy);
  }
};
$("new").onclick = () => {
  if (
    submitting ||
    cancelling ||
    loading ||
    uploads ||
    !confirmNewConversation()
  )
    return;
  const loose = Array.from($("project").options).some(
    (o) => o.value === "sem-projeto",
  );
  if (loose) $("project").value = "sem-projeto";
  newConversation(
    loose
      ? "New Conversation"
      : "New Conversation in project " +
          $("project").selectedOptions[0]?.textContent,
  );
  renderProjects();
  history();
  $("sidebar").classList.remove("open");
};
$("project").onchange = () => {
  const draft = $("prompt").value;
  invalidateResources();
  const stale = [...invalidResourceTokens];
  newConversation();
  invalidResourceTokens = new Set(stale);
  $("prompt").value = draft;
  resourceSelections = [];
  updateComposer();
  saveView();
  renderProjects();
  history();
};
$("model").onchange = () => {
  invalidateResources();
  updateEfforts();
  rememberSelection();
  updateComposer();
  saveView();
  quota();
};
$("quota-refresh").onclick = () => void quota();
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && ["codex", "claude"].includes(selected()?.backend))
    void quota();
});
setInterval(() => {
  if (!document.hidden && ["codex", "claude"].includes(selected()?.backend))
    void quota();
}, 60000);
$("effort").onchange = () => {
  rememberSelection();
  saveView();
};
$("attach").onclick = () => $("file").click();
$("file").onchange = () => {
  upload($("file").files);
  $("file").value = "";
};
$("dropzone").ondragover = (e) => {
  if (
    e.dataTransfer.types.includes(
      "application/x-tail-authorized-project-files",
    ) ||
    e.dataTransfer.files.length
  ) {
    e.preventDefault();
    $("dropzone").classList.add("drag");
  }
};
$("dropzone").ondragleave = () => $("dropzone").classList.remove("drag");
$("dropzone").ondrop = (e) => {
  e.preventDefault();
  $("dropzone").classList.remove("drag");
  const payload = e.dataTransfer.getData(
    "application/x-tail-authorized-project-files",
  );
  if (payload) {
    try {
      const selection = JSON.parse(payload);
      if (Array.isArray(selection.paths))
        void attachSelectedProjectFiles(selection);
      else status("The file selection isn't valid.");
    } catch {
      status("The file selection isn't valid.");
    }
  } else upload(e.dataTransfer.files);
};
$("quota-toggle").onclick = () => setQuotaOpen($("quota-panel").hidden);
$("quota-refresh").onclick = quota;
function toggleSidebar() {
  if (matchMedia("(max-width:620px)").matches) {
    $("sidebar").classList.toggle("open");
  } else {
    document.body.classList.toggle("sidebar-collapsed");
    try {
      localStorage.setItem(
        "sidebar-collapsed",
        document.body.classList.contains("sidebar-collapsed") ? "1" : "0",
      );
    } catch {}
  }
  $("menu").setAttribute(
    "aria-expanded",
    String(
      matchMedia("(max-width:620px)").matches
        ? $("sidebar").classList.contains("open")
        : !document.body.classList.contains("sidebar-collapsed"),
    ),
  );
}
$("menu").onclick = () => {
  toggleSidebar();
  fitPanels();
};
try {
  document.body.classList.toggle(
    "sidebar-collapsed",
    localStorage.getItem("sidebar-collapsed") === "1",
  );
} catch {}
$("menu").setAttribute(
  "aria-expanded",
  String(
    !matchMedia("(max-width:620px)").matches &&
      !document.body.classList.contains("sidebar-collapsed"),
  ),
);
$("setup").onclick = () => $("setup-dialog").showModal();
$("setup-close").onclick = () => $("setup-dialog").close();
$("setup-code").textContent = `cd ~/Downloads
chmod +x setup-mcp.sh
./setup-mcp.sh '${location.origin}'`;
function bindSuggestions() {
  document.querySelectorAll("[data-prompt]").forEach(
    (b) =>
      (b.onclick = () => {
        $("prompt").value = b.dataset.prompt;
        updateComposer();
        saveView();
        $("prompt").focus();
      }),
  );
}
bindSuggestions();
let startupTimer,
  readinessTimer,
  initializing = false,
  probing = false,
  interfaceReady = false,
  readinessRetryAt = 0;
function setReadiness(ready, message = "") {
  interfaceReady = ready;
  for (const node of [
    $("app-topbar"),
    $("sidebar"),
    document.querySelector("main"),
    $("activity-panel"),
    document.querySelector(".skip-link"),
  ])
    node.inert = !ready;
  $("startup-gate").hidden = ready;
  $("startup-gate").querySelector("span").textContent = ready
    ? ""
    : message ||
      "Waiting for the server… The connection will be checked automatically.";
  document.body.dataset.connectionReady = String(ready);
  if (!ready) {
    for (const menu of document.querySelectorAll(".composer-menu:popover-open"))
      menu.hidePopover();
    for (const dialog of document.querySelectorAll(
      "dialog[open]:not(#vpn-login)",
    ))
      dialog.close();
  } else if ($("vpn-login").open) $("vpn-login").close();
}
function modelAvailability(
  data = { admin_url: $("admin-link").getAttribute("href") },
  error = "",
) {
  const panel = $("model-availability");
  panel.hidden = models.length > 0 && !error;
  $("model-availability-title").textContent = error
    ? "Couldn't check the models"
    : "No model available";
  $("model-availability-detail").textContent =
    error ||
    "Add and enable a provider in this server's admin panel. Then start or restart the harness to apply it.";
  const link = $("admin-link");
  link.hidden = true;
  {
    const url = data?.admin_url;
    try {
      const parsed = new URL(url);
      if (
        ["127.0.0.1", "localhost"].includes(parsed.hostname) &&
        parsed.protocol === "http:"
      ) {
        link.href = parsed.href;
        link.hidden = false;
      }
    } catch {}
  }
  const shortcut = $("admin-shortcut"),
    topShortcut = $("admin-shortcut-top");
  shortcut.hidden = link.hidden;
  topShortcut.hidden = link.hidden;
  if (!link.hidden) {
    shortcut.href = link.href;
    topShortcut.href = link.href;
  }
  $("model").disabled =
    !models.length || submitting || loading || uploads > 0 || policyPending;
  $("effort").disabled = $("model").disabled;
  $("prompt").placeholder = models.length
    ? "Send a message… · Enter to send · Shift+Enter for a new line"
    : "Set up a model to send; your draft will be preserved.";
  $("model-note").hidden = !models.length;
  updateModelPermissions();
  if (!models.length) {
    $("model").replaceChildren(new Option("No model available", ""));
    $("effort").replaceChildren(new Option("Effort unavailable", ""));
    $("quota-short").textContent = "No model selected";
  }
  if ($("welcome")) $("welcome").hidden = !models.length;
  updateComposer();
}
async function initialize() {
  if (initializing) return;
  initializing = true;
  setReadiness(false);
  const retry = $("models-retry");
  retry.disabled = true;
  retry.textContent = "Checking…";
  try {
    const [p, m] = await Promise.all([
      json("/v1/projects", { signal: AbortSignal.timeout(5000) }),
      json("/v1/models", { signal: AbortSignal.timeout(5000) }),
    ]);
    if (
      !Array.isArray(p.projects) ||
      !p.projects.length ||
      !Array.isArray(m.models)
    )
      throw Error("The server returned an invalid catalog.");
    let saved = {};
    if (!startupTimer) {
      try {
        saved = JSON.parse(sessionStorage.getItem("remote-view") || "{}") || {};
      } catch {}
    }
    const previous = $("model").value,
      project = saved.project || $("project").value;
    updateProjectMetadata(p.details);
    $("project").replaceChildren(
      ...p.projects.map((id) => {
        const o = new Option(p.details?.[id]?.label || id, id);
        return o;
      }),
    );
    $("project").value = p.projects.includes(project)
      ? project
      : p.projects.includes("sem-projeto")
        ? "sem-projeto"
        : p.projects[0];
    renderProjects();
    syncActiveProjectBadge();
    providers = m.providers || {};
    models = m.models.filter((m) => TailUI.selectableModel(m.backend, m.id));
    uploadsAllowed = m.uploads_enabled === true;
    policyProject = null;
    policyPending = false;
    $("model").replaceChildren(
      ...models.map((m) => new Option(names[m.id] || m.name || m.id, m.id)),
    );
    if (models.some((m) => m.id === previous)) $("model").value = previous;
    updateEfforts();
    restoreSelection();
    if (!(await refreshProjectPermissions(5000)))
      throw Error("Couldn't load this project's permissions.");
    modelAvailability(m);
    if (!(await history(5000)))
      throw Error("Couldn't load the conversation history.");
    status(models.length ? "Ready to chat." : "Set up a model to get started.");
    let resumeWatch = false;
    if (!startupTimer) {
      try {
        if (saved.conversation)
          resumeWatch = await load(saved.conversation, false, saved);
        restoreView(saved);
      } catch {}
      startupTimer = setInterval(() => {
        if (!document.hidden && interfaceReady && !initializing) {
          checkVersion();
          history();
        }
      }, 10000);
    }
    setReadiness(true);
    if (!$("activity-panel").hidden && rightPanelView === "files")
      loadProjectFileRoots();
    void Promise.all([quota(), checkVersion()]);
    updateComposer();
    if (resumeWatch && job) void watch();
  } catch (e) {
    setReadiness(
      false,
      e.status === 401
        ? "Waiting for authorization. Enter your access key to connect."
        : "Waiting for the server… Checking the connection automatically.",
    );
    if (e.status === 401 && !$("vpn-login").open) $("vpn-login").showModal();
    modelAvailability(null, e.message);
    status("Couldn't connect: " + e.message);
  } finally {
    initializing = false;
    retry.disabled = false;
    retry.textContent = "Check again";
    if (!readinessTimer)
      readinessTimer = setInterval(() => {
        retryReadiness();
      }, 5000);
  }
}
function retryReadiness() {
  if (
    document.hidden ||
    initializing ||
    probing ||
    Date.now() < readinessRetryAt
  )
    return;
  return interfaceReady ? probeReadiness() : initialize();
}
document.addEventListener("visibilitychange", () => {
  void retryReadiness();
});
window.addEventListener("online", () => {
  void retryReadiness();
});
async function probeReadiness() {
  if (probing || Date.now() < readinessRetryAt) return;
  readinessRetryAt = Date.now() + 15000;
  probing = true;
  try {
    const [p, m] = await Promise.all([
      json("/v1/projects", { signal: AbortSignal.timeout(5000) }),
      json("/v1/models", { signal: AbortSignal.timeout(5000) }),
    ]);
    if (
      !Array.isArray(p.projects) ||
      !p.projects.length ||
      !Array.isArray(m.models)
    )
      throw Error("Invalid catalog");
    const project = $("project").value;
    if (!p.projects.includes(project)) throw Error("Project unavailable");
    const scoped = await json(
      "/v1/models?project_id=" + encodeURIComponent(project),
      { signal: AbortSignal.timeout(5000) },
    );
    if (!Array.isArray(scoped.models)) throw Error("Invalid permissions");
    if (
      !busy &&
      !loading &&
      project === $("project").value &&
      (JSON.stringify(scoped.models) !== JSON.stringify(models) ||
        (scoped.uploads_enabled === true) !== uploadsAllowed)
    ) {
      policyProject = null;
      if (await refreshProjectPermissions(5000)) {
        providers = m.providers || {};
        modelAvailability(m);
      }
    }
    if (!busy && !loading) {
      const icons = Object.fromEntries(
        Object.entries(p.details || {}).map(([id, detail]) => [
          id,
          detail.icon,
        ]),
      );
      const aliases = Object.fromEntries(
        Object.entries(p.details || {}).map(([id, detail]) => [
          id,
          detail.canonical_id || id,
        ]),
      );
      if (
        JSON.stringify(icons) !== JSON.stringify(projectIcons) ||
        JSON.stringify(aliases) !== JSON.stringify(projectAliases)
      ) {
        updateProjectMetadata(p.details);
        renderProjects();
      }
    }
    if (
      !busy &&
      !loading &&
      JSON.stringify(p.projects) !==
        JSON.stringify([...$("project").options].map((o) => o.value))
    ) {
      updateProjectMetadata(p.details);
      $("project").replaceChildren(
        ...p.projects.map((id) => new Option(p.details?.[id]?.label || id, id)),
      );
      $("project").value = project;
      renderProjects();
    }
    try {
      const h = await json("/v1/conversations", {
        signal: AbortSignal.timeout(5000),
      });
      if (!Array.isArray(h.conversations)) throw Error("Invalid history");
    } catch (e) {
      if (e.status !== 404) throw e;
      const h = await json("/v1/history", {
        signal: AbortSignal.timeout(5000),
      });
      if (!Array.isArray(h.jobs)) throw Error("Invalid history");
    }
  } catch (e) {
    if (e.status === 429) {
      readinessRetryAt = Date.now() + Math.max(5000, e.retryAfter || 5000);
      status(e.message);
      return;
    }
    readinessRetryAt = 0;
    if (e.status === 401 && !$("vpn-login").open) $("vpn-login").showModal();
    setReadiness(
      false,
      e.status === 401
        ? "Waiting for authorization. Enter your access key to connect."
        : "Waiting for the server… Checking the connection automatically.",
    );
    status("Connection to the server lost: " + e.message);
  } finally {
    probing = false;
  }
}
$("resume-execution").onclick = () => watch();
$("models-retry").onclick = () => {
  if (!busy) initialize();
};
initialize();

function restoreView(saved) {
  if (
    saved.project === $("project").value &&
    models.some((m) => m.id === saved.composer_selection?.model)
  ) {
    $("model").value = saved.composer_selection.model;
    updateEfforts();
    if (
      [...$("effort").options].some(
        (o) => o.value === saved.composer_selection.effort,
      )
    )
      $("effort").value = saved.composer_selection.effort;
  }
  if (!conversation && ["native", "scoped"].includes(saved.execution_mode)) {
    executionMode = saved.execution_mode;
    executionModeChosen = saved.execution_mode_chosen !== false;
  }
  if (typeof saved.draft === "string") $("prompt").value = saved.draft;
  invalidResourceTokens = new Set(
    Array.isArray(saved.invalid_resource_tokens)
      ? saved.invalid_resource_tokens.filter(
          (token) =>
            typeof token === "string" &&
            $("prompt").value.split(/\s+/).includes(token),
        )
      : [],
  );
  const engine = resourceEngine(),
    sameResourceContext =
      saved.resource_context?.project === $("project").value &&
      saved.resource_context?.backend === engine.backend &&
      saved.resource_context?.model === engine.model &&
      saved.resource_context?.execution_mode === engine.execution_mode;
  if (sameResourceContext && Array.isArray(saved.resource_selections))
    resourceSelections = saved.resource_selections.filter(
      (ref) =>
        ref &&
        typeof ref.id === "string" &&
        typeof ref.revision === "string" &&
        typeof ref.token === "string" &&
        $("prompt").value.split(/\s+/).includes(ref.token),
    );
  else {
    resourceSelections = [];
    invalidResourceTokens = new Set([
      ...invalidResourceTokens,
      ...(Array.isArray(saved.resource_selections)
        ? saved.resource_selections
            .map((ref) => ref?.token)
            .filter(
              (token) =>
                typeof token === "string" &&
                $("prompt").value.split(/\s+/).includes(token),
            )
        : []),
    ]);
  }
  if (saved.project === $("project").value && Array.isArray(saved.files)) {
    files = saved.files
      .filter(
        (f) => f && typeof f.id === "string" && typeof f.name === "string",
      )
      .slice(0, MAX_ATTACHMENTS);
    renderFiles();
  }
  saveView();
  updateComposer();
}
function saveView() {
  try {
    sessionStorage.setItem(
      "remote-view",
      JSON.stringify({
        conversation,
        composer_selection: {
          model: $("model").value,
          effort: $("effort").value,
        },
        execution_mode: executionMode,
        execution_mode_chosen: executionModeChosen,
        project: $("project").value,
        draft: $("prompt").value,
        files,
        resource_selections: resourceSelections,
        invalid_resource_tokens: [...invalidResourceTokens],
        resource_context: { project: $("project").value, ...resourceEngine() },
      }),
    );
    return true;
  } catch {
    return false;
  }
}
$("prompt").addEventListener("input", () => {
  saveView();
  updateComposer();
});

function throughputLabel(m) {
  const direct = m?.generated_tokens_per_second;
  if (Number.isFinite(direct) && direct >= 0)
    return (
      " · " +
      direct.toLocaleString("en-US", { maximumFractionDigits: 1 }) +
      " tk/s"
    );
  const tokens = m?.output_tokens,
    seconds = m?.inference_seconds;
  if (
    Number.isFinite(tokens) &&
    tokens >= 0 &&
    Number.isFinite(seconds) &&
    seconds > 0
  )
    return (
      " · " +
      (tokens / seconds).toLocaleString("en-US", { maximumFractionDigits: 1 }) +
      " tk/s"
    );
  return " · tk/s: —";
}
function paintContext(u, metrics) {
  const current = selected(),
    n = u.last?.totalTokens,
    w =
      current?.backend === "local"
        ? current.context_window
        : u.modelContextWindow;
  $("context-meter").textContent =
    (Number.isFinite(n)
      ? "Context: " +
        n.toLocaleString() +
        (w
          ? " / " +
            w.toLocaleString() +
            " tokens · " +
            Math.round((n / w) * 100) +
            "%"
          : " tokens")
      : "Context not reported") + throughputLabel(metrics);
}
function paintLocalUsage(m) {
  $("context-meter").textContent =
    "Last run: " +
    (m.input_tokens ?? "—") +
    " input tokens · " +
    (m.output_tokens ?? "—") +
    " output" +
    throughputLabel(m);
}
async function checkVersion() {
  void refreshComposerGit();
  try {
    const v = await json("/v1/version");
    $("version").textContent = "Release: " + v.version;
    if (v.config_reload_error)
      status(
        "Couldn't apply the configuration. The harness kept the last valid configuration. Review the admin panel.",
      );
    if (build && build !== v.build) reloadPending = true;
    build = v.build;
    if (reloadPending && !busy && !loading && !uploads && saveView()) {
      location.reload();
    } else if (reloadPending) {
      $("version").title =
        "An update will be applied once the run or attachment upload finishes, preserving your draft.";
    }
  } catch {}
}

const activityIcons = {
  queued: "⏳",
  running: "▶",
  loading: "⏳",
  thinking: "💭",
  planning: "📋",
  plan_updated: "📋",
  tool_start: "🛠",
  tool_end: "✓",
  completed: "✅",
  failed: "❌",
  error: "❌",
  cancelled: "■",
  interrupted: "⚠",
  validating_changes: "🔎",
  changes_applied: "💾",
  deployment_failed: "⚠",
  session_resumed: "↩",
  context_compacting: "⟳",
  context_compacted: "✓",
};
function setPanelView(view, persist = true) {
  rightPanelView = view;
  $("files-view").hidden = view !== "files";
  $("activity-view").hidden = view !== "activity";
  $("files-title").textContent = "Files";
  $("activity-title").textContent = "Activity";
  $("files-toggle").setAttribute(
    "aria-expanded",
    String(!$("activity-panel").hidden && view === "files"),
  );
  $("activity-toggle").setAttribute(
    "aria-expanded",
    String(!$("activity-panel").hidden && view === "activity"),
  );
  if (persist)
    try {
      localStorage.setItem("right-panel-view", view);
    } catch {}
}
const quotaHome = document.createComment("quota-indicator-home");
$("quota-toggle").before(quotaHome);
function syncQuotaDock() {
  const dock = innerWidth < 1200 && !$("activity-panel").hidden;
  if (dock) {
    $(
      document.body.classList.contains("panel-order-reversed")
        ? "menu"
        : "panel-toggle",
    ).before($("quota-toggle"));
  } else if (
    quotaHome.parentNode &&
    $("quota-toggle").parentNode !== quotaHome.parentNode
  ) {
    quotaHome.before($("quota-toggle"));
  }
  updateHeaderToastOffset();
}
function setPanelOpen(open, persist = true) {
  $("activity-panel").hidden = !open;
  syncQuotaDock();
  $("panel-toggle").setAttribute("aria-expanded", String(open));
  if (!open && $("activity-panel").contains(document.activeElement))
    $("panel-toggle").focus();
  $("files-toggle").setAttribute(
    "aria-expanded",
    String(open && rightPanelView === "files"),
  );
  $("activity-toggle").setAttribute(
    "aria-expanded",
    String(open && rightPanelView === "activity"),
  );
  if (open && rightPanelView === "files" && interfaceReady)
    loadProjectFileRoots();
  if (persist)
    try {
      localStorage.setItem("activity-open", open ? "1" : "0");
    } catch {}
}
function togglePanelView(view) {
  if ($("activity-panel").hidden) {
    setPanelView(view);
    setPanelOpen(true);
  } else if (rightPanelView === view) setPanelOpen(false);
  else {
    setPanelView(view);
    if (view === "files") loadProjectFileRoots();
  }
}
let rightPanelView = "files";
function resetActivity(clearHistory = true) {
  paintMotion("");
  if (clearHistory) {
    $("response-details")?.replaceChildren();
    $("activity-previous").replaceChildren();
    $("activity-current").dataset.job = "";
    $("activity-run-title").textContent = "Waiting for execution";
  }
  if ($("activity-reasoning")) $("activity-reasoning").textContent = "";
  const list = $("activity-events");
  list.replaceChildren();
  list.eventIds = new Set();
  delete list.dataset.omitted;
  $("activity-truncation").hidden = true;
  $("activity-failures").hidden = true;
  $("activity-state").textContent = "Waiting for execution";
}
function recordActivity(e) {
  const type = e.type,
    data = e.data || {},
    failed =
      type === "tool_end" &&
      (data.status === "failed" || data.result?.isError === true);
  if (
    ["context_usage", "usage_metrics", "quota_before", "quota_after"].includes(
      type,
    )
  )
    return;
  if (type === "answer_delta") {
    $("activity-state").textContent = "Receiving response";
    return;
  }
  if (["reasoning_delta", "reasoning_summary", "thinking"].includes(type)) {
    $("activity-state").textContent = "Thinking";
    return;
  }
  if (type === "tool_start" || type === "tool_end") {
    $("activity-state").textContent = failed
      ? "A step failed"
      : "Run in progress";
    if (failed) {
      $("activity-failures").hidden = false;
      $("activity-failures").textContent = "A step in this run failed.";
    }
    return;
  }
  const title = activityTitle(e);
  if (!title) return;
  const list = $("activity-events");
  list.eventIds ??= new Set();
  if (e.id != null) {
    if (list.eventIds.has(e.id)) return;
    list.eventIds.add(e.id);
  }
  const row = document.createElement("li"),
    label = document.createElement("strong");
  label.textContent = title;
  row.append(label);
  row.dataset.state = type;
  if (type === "plan_updated") {
    const plan = document.createElement("ul");
    for (const step of (data.plan || []).slice(0, 40)) {
      const item = document.createElement("li");
      item.textContent = String(step.step || "").slice(0, 200);
      plan.append(item);
    }
    row.append(plan);
  }
  list.append(row);
  if (list.children.length > 80) {
    list.firstElementChild.remove();
    const count = Number(list.dataset.omitted || 0) + 1;
    list.dataset.omitted = count;
    $("activity-truncation").hidden = false;
    $("activity-truncation").textContent =
      count + " earlier milestones remain in the run log.";
  }
  $("activity-state").textContent = title;
}
$("panel-toggle").onclick = () => {
  setPanelOpen($("activity-panel").hidden);
  fitPanels();
};
$("files-toggle").onclick = () => togglePanelView("files");
$("activity-toggle").onclick = () => togglePanelView("activity");
try {
  const preference = localStorage.getItem("activity-open"),
    savedView = localStorage.getItem("right-panel-view");
  rightPanelView = savedView || (preference === "1" ? "activity" : "files");
  setPanelView(rightPanelView, false);
  setPanelOpen(
    preference === null
      ? matchMedia("(min-width:1200px)").matches
      : preference === "1",
    false,
  );
} catch {
  rightPanelView = "files";
  setPanelView("files", false);
  setPanelOpen(matchMedia("(min-width:1200px)").matches, false);
}

function boundedText(text, limit) {
  return text.length > limit
    ? "[… earlier content omitted …]\n" + text.slice(-limit)
    : text;
}
function beginActivity(id, model = $("model").value) {
  const current = $("activity-current");
  if (current.dataset.job === id) return;
  if (current.dataset.job) {
    const archived = current.cloneNode(true);
    archived.removeAttribute("id");
    archived.querySelectorAll("[id]").forEach((el) => el.removeAttribute("id"));
    archived.querySelectorAll("details").forEach((el) => (el.open = false));
    $("activity-previous").replaceChildren(archived);
  }
  resetActivity(false);
  current.dataset.job = id;
  $("activity-run-title").textContent =
    modelIcon(model) +
    " " +
    (names[model] || model || "Model") +
    " · Run " +
    id;
}
async function replayActivity(id) {
  const response = await api("/v1/jobs/" + encodeURIComponent(id) + "/events", {
    signal: AbortSignal.timeout(15000),
  });
  const reader = response.body.getReader(),
    decoder = new TextDecoder();
  let buffer = "",
    seen = 0;
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let end;
      while ((end = buffer.indexOf("\n\n")) >= 0) {
        const block = buffer.slice(0, end);
        buffer = buffer.slice(end + 2);
        const line = block.split("\n").find((l) => l.startsWith("data: "));
        if (line) {
          const e = JSON.parse(line.slice(6));
          if (e.id > seen) {
            seen = e.id;
            recordActivity(e);
          }
        }
      }
    }
  } finally {
    await reader.cancel();
  }
}
let panelOrder = "conversations-left";
try {
  if (localStorage.getItem("panel-order") === "conversations-right")
    panelOrder = "conversations-right";
} catch {}
const panelWidths = { sidebar: 280, "activity-panel": 400 };
const panelIsLeft = (id) =>
  id === "sidebar"
    ? panelOrder === "conversations-left"
    : panelOrder === "conversations-right";
function panelLimits(id) {
  const mobile = innerWidth <= 620,
    docked = innerWidth >= 1200;
  const other = id === "sidebar" ? $("activity-panel") : $("sidebar");
  const otherWidth =
    (id === "sidebar" ? docked : !mobile) && other.getClientRects().length
      ? other.getBoundingClientRect().width
      : 0;
  const max = Math.max(
    220,
    Math.min(
      720,
      (id === "activity-panel" && !docked) || mobile
        ? innerWidth - 24
        : innerWidth - otherWidth - 480,
    ),
  );
  return { min: 220, max };
}
function sizePanel(id, width, persist = true) {
  const { min, max } = panelLimits(id),
    value = Math.round(Math.max(min, Math.min(max, width)));
  $(id).style.width = value + "px";
  if (
    id === "sidebar" &&
    !document.body.classList.contains("sidebar-collapsed")
  )
    document.body.style.setProperty("--th-sidebar-width", value + "px");
  const handle = $(id + "-resize");
  handle.setAttribute("aria-valuemin", min);
  handle.setAttribute("aria-valuemax", Math.floor(max));
  handle.setAttribute("aria-valuenow", value);
  handle.setAttribute("aria-valuetext", value + " pixels");
  if (persist) {
    panelWidths[id] = value;
    try {
      localStorage.setItem(id + "-width", String(value));
    } catch {}
  }
}
for (const id of Object.keys(panelWidths)) {
  try {
    const saved = Number(localStorage.getItem(id + "-width"));
    if (saved >= 220 && saved <= 720) panelWidths[id] = saved;
  } catch {}
  const handle = $(id + "-resize");
  let drag = null;
  handle.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    e.preventDefault();
    handle.focus();
    drag = { x: e.clientX, width: $(id).getBoundingClientRect().width };
    handle.setPointerCapture(e.pointerId);
    document.body.classList.add("resizing-panels");
  });
  handle.addEventListener("pointermove", (e) => {
    if (drag)
      sizePanel(
        id,
        drag.width + (e.clientX - drag.x) * (panelIsLeft(id) ? 1 : -1),
      );
  });
  const stop = () => {
    drag = null;
    document.body.classList.remove("resizing-panels");
  };
  handle.addEventListener("pointerup", stop);
  handle.addEventListener("pointercancel", stop);
  handle.addEventListener("lostpointercapture", stop);
  handle.addEventListener("keydown", (e) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) return;
    e.preventDefault();
    const { min, max } = panelLimits(id),
      width = $(id).getBoundingClientRect().width;
    sizePanel(
      id,
      e.key === "Home"
        ? min
        : e.key === "End"
          ? max
          : width +
            (e.key === "ArrowRight" ? 24 : -24) * (panelIsLeft(id) ? 1 : -1),
    );
  });
}
function fitPanels() {
  sizePanel("sidebar", panelWidths.sidebar, false);
  sizePanel("activity-panel", panelWidths["activity-panel"], false);
}
window.addEventListener("resize", fitPanels);
function applyPanelOrder(value, persist = true) {
  panelOrder =
    value === "conversations-right"
      ? "conversations-right"
      : "conversations-left";
  document.body.classList.toggle(
    "panel-order-reversed",
    panelOrder === "conversations-right",
  );
  const reversed = panelOrder === "conversations-right";
  $("app-brand").after($(reversed ? "panel-toggle" : "menu"));
  $("app-topbar").append($(reversed ? "menu" : "panel-toggle"));
  for (const button of document.querySelectorAll("[data-panel-order]"))
    button.setAttribute(
      "aria-pressed",
      String(button.dataset.panelOrder === panelOrder),
    );
  if (persist)
    try {
      localStorage.setItem("panel-order", panelOrder);
    } catch {}
  fitPanels();
}
applyPanelOrder(panelOrder, false);
for (const button of document.querySelectorAll("[data-panel-order]"))
  button.onclick = () => applyPanelOrder(button.dataset.panelOrder);
$("panel-order-reset").onclick = () => {
  panelWidths.sidebar = 280;
  panelWidths["activity-panel"] = 400;
  try {
    localStorage.removeItem("sidebar-width");
    localStorage.removeItem("activity-panel-width");
  } catch {}
  applyPanelOrder("conversations-left");
  fitPanels();
};
fitPanels();

$("activity-toggle").onclick = () => {
  togglePanelView("activity");
  fitPanels();
};

function rememberSelection() {
  preferredSelection = { model: $("model").value, effort: $("effort").value };
  try {
    localStorage.setItem("chat-selection", JSON.stringify(preferredSelection));
  } catch {}
}
function restoreSelection() {
  if (!models.length) return;
  const model = models.find((m) => m.id === preferredSelection.model);
  if (!model) return;
  $("model").value = model.id;
  updateEfforts();
  if (model.efforts.includes(preferredSelection.effort))
    $("effort").value = preferredSelection.effort;
}
document.querySelectorAll("[data-settings]").forEach(
  (button) =>
    (button.onclick = () => {
      for (const name of ["appearance", "agents", "skills"])
        $("settings-" + name).hidden = name !== button.dataset.settings;
      document
        .querySelectorAll("[data-settings]")
        .forEach((b) => b.setAttribute("aria-pressed", String(b === button)));
    }),
);
let catalogRequest = 0;
function catalogCard(item) {
  const card = document.createElement("article");
  card.className = "catalog-card";
  const title = document.createElement("h4");
  title.textContent = item.name;
  const description = document.createElement("p");
  description.textContent = item.description;
  const meta = document.createElement("small");
  meta.textContent = [item.status, item.source].filter(Boolean).join(" · ");
  card.append(title, description, meta);
  return card;
}
async function refreshCatalog() {
  const request = ++catalogRequest,
    project = $("project").value;
  $("catalog-status").textContent = "Checking catalog for " + project + "…";
  $("catalog-models").replaceChildren(
    ...models.map((m) =>
      catalogCard({
        name: modelIcon(m.id) + " " + (names[m.id] || m.name || m.id),
        description:
          (m.backend === "local"
            ? "Local model. "
            : m.backend === "gemini"
              ? "Model via Gemini CLI. "
              : m.backend === "claude"
                ? "Model via Claude Code. "
                : m.backend === "deepseek"
                  ? "Model via DeepSeek. "
                  : "Model via Codex. ") +
          "Efforts: " +
          m.efforts.map((e) => efforts[e] || e).join(", "),
        status: "Configured model",
        source: "/v1/models",
      }),
    ),
  );
  for (const provider of ["codex", "claude", "gemini"]) {
    if (!providers[provider])
      $("catalog-models").append(
        catalogCard({
          name:
            provider === "gemini"
              ? "Gemini CLI"
              : provider === "claude"
                ? "Claude Code"
                : "Codex",
          description:
            "Configure the adapter in the service to enable this provider.",
          status: "Not configured",
          source: "/v1/models",
        }),
      );
  }
  $("catalog-agents").replaceChildren();
  $("catalog-skills").replaceChildren();
  try {
    const data = await json(
      "/v1/catalog?project_id=" + encodeURIComponent(project),
    );
    if (request !== catalogRequest) return;
    for (const kind of ["agents", "skills"]) {
      $("catalog-" + kind).replaceChildren(...data[kind].map(catalogCard));
      if (!data[kind].length)
        $("catalog-" + kind).textContent =
          "No items found in the selected project.";
    }
    $("catalog-status").textContent =
      "Project: " +
      project +
      ". " +
      data.scope +
      (data.warnings.length ? " " + data.warnings.join(" ") : "");
  } catch (e) {
    if (request !== catalogRequest) return;
    $("catalog-status").textContent =
      "Catalog unavailable in this service. " + e.message;
    $("catalog-skills").textContent = "Couldn't check the available skills.";
  }
}
$("settings").onclick = () => {
  $("settings-dialog").showModal();
  refreshCatalog();
};
$("settings-close").onclick = () => $("settings-dialog").close();
for (const id of ["settings-dialog", "conversation-search-dialog"]) {
  const dialog = $(id);
  dialog.addEventListener("click", (event) => {
    if (event.target !== dialog) return;
    const r = dialog.getBoundingClientRect();
    if (
      event.clientX < r.left ||
      event.clientX > r.right ||
      event.clientY < r.top ||
      event.clientY > r.bottom
    )
      dialog.close();
  });
}

$("catalog-refresh").onclick = refreshCatalog;

/* Conversation navigation and composition enhancements. */
function normalizeSearch(value) {
  return value
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLocaleLowerCase();
}
function renderConversationSearch() {
  const query = normalizeSearch($("conversation-search").value.trim());
  const matches = conversations.filter((c) =>
    normalizeSearch(c.title || "Conversation").includes(query),
  );
  $("search-clear").hidden = !query;
  $("search-results").textContent = matches.length
    ? matches.length + " conversation(s) found"
    : query
      ? "No conversation found. Try a different title."
      : "No conversation available.";
  $("conversation-search-list").replaceChildren(
    ...matches.map((c) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "conversation-search-result";
      const title = document.createElement("strong"),
        detail = document.createElement("small");
      title.textContent = c.title || "Conversation";
      const project =
        [...$("project").options].find((o) => o.value === c.project)
          ?.textContent || "No project";
      detail.append(document.createTextNode(project));
      if (c.execution?.model) {
        const icon = document.createElement("span");
        icon.className = "model-logo-icon";
        icon.textContent = modelIcon(c.execution.model);
        icon.setAttribute("aria-hidden", "true");
        detail.append(
          document.createTextNode(" · "),
          icon,
          document.createTextNode(" " + modelName(c.execution.model)),
        );
      }
      const indicator = conversationIndicator(c);
      if (indicator) title.prepend(indicator);
      button.append(title, detail);
      button.disabled = submitting || cancelling || uploads > 0;
      button.onclick = () => {
        if (submitting || cancelling || uploads) return;
        $("conversation-search-dialog").close();
        void load(c.id, c.legacy);
      };
      return button;
    }),
  );
}
function openConversationSearch() {
  renderConversationSearch();
  $("conversation-search-dialog").showModal();
  $("conversation-search").focus();
}
$("search-conversations").onclick = openConversationSearch;
$("conversation-search-close").onclick = () =>
  $("conversation-search-dialog").close();
$("conversation-search-dialog").addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    event.preventDefault();
    $("conversation-search-dialog").close();
  }
});
$("conversation-search").addEventListener("input", renderConversationSearch);
$("search-clear").onclick = () => {
  $("conversation-search").value = "";
  renderConversationSearch();
  $("conversation-search").focus();
};
function updateComposer() {
  syncComposerPickers();
  syncExecutionMode();
  updateModelPermissions();
  const prompt = $("prompt");
  prompt.style.height = "auto";
  prompt.style.height = Math.min(prompt.scrollHeight, 170) + "px";
  renderPromptHighlights();
  const count = Array.from(prompt.value).length;
  $("character-count").textContent =
    count.toLocaleString("en-US") +
    (count === 1 ? " character" : " characters");
  const hasPrompt = !!prompt.value.trim();
  $("send").hidden = busy && !hasPrompt;
  $("cancel").hidden = !busy || hasPrompt;
  $("cancel").disabled = submitting || cancelling || !job;
  const cooldown = sendCooldownSeconds();
  if (cooldown) $("send").dataset.countdown = cooldown;
  else delete $("send").dataset.countdown;
  $("send").setAttribute(
    "aria-label",
    cooldown
      ? "Send message (available in " + cooldown + " seconds)"
      : "Send message",
  );
  $("send").disabled =
    submitting ||
    cancelling ||
    loading ||
    uploads > 0 ||
    policyPending ||
    !selected() ||
    !prompt.value.trim() ||
    !supportedExecutionModes().includes(executionMode) ||
    cooldown > 0;
}
function updateLatest() {
  const box = $("messages");
  $("latest-message").hidden =
    box.scrollHeight - box.scrollTop - box.clientHeight < 150;
}
$("messages").addEventListener("scroll", updateLatest, { passive: true });
new MutationObserver(updateLatest).observe($("messages"), {
  childList: true,
  subtree: true,
  characterData: true,
});
function jumpToLatest() {
  const box = $("messages");
  // Focus must not undo the jump; instant scrolling cannot be interrupted by streaming updates.
  box.focus({ preventScroll: true });
  box.scrollTo({ top: box.scrollHeight, behavior: "instant" });
  updateLatest();
}
$("latest-message").onclick = jumpToLatest;
new ResizeObserver(updateLatest).observe($("messages"));
function setQuotaOpen(open) {
  $("quota-panel").hidden = !open;
  $("quota-toggle").setAttribute("aria-expanded", String(open));
}
document.addEventListener("pointerdown", (e) => {
  if (!e.target.closest("#quota-panel, #quota-toggle")) setQuotaOpen(false);
});
document.addEventListener("keydown", (e) => {
  if (
    e.defaultPrevented ||
    !interfaceReady ||
    document.querySelector("dialog[open]") ||
    document.querySelector(".composer-menu:popover-open")
  )
    return;
  if (
    (e.ctrlKey || e.metaKey) &&
    !e.altKey &&
    (e.key === "/" || e.key.toLowerCase() === "k")
  ) {
    e.preventDefault();
    if (e.key === "/") $("prompt").focus();
    else openConversationSearch();
  }
  if (e.key === "Escape") {
    if (!$("quota-panel").hidden) {
      setQuotaOpen(false);
      $("quota-toggle").focus();
    } else if (!$("activity-panel").hidden) {
      setPanelOpen(false);
      $("panel-toggle").focus();
    } else if ($("sidebar").classList.contains("open")) {
      $("sidebar").classList.remove("open");
      $("menu").setAttribute("aria-expanded", "false");
      $("menu").focus();
    }
  }
});
// A modal dialog lets Tab leave the page after its last control; wrap focus inside it.
document.addEventListener("keydown", (e) => {
  const dialog =
    e.key === "Tab" && document.activeElement?.closest("dialog:modal");
  if (!dialog) return;
  const controls = [
    ...dialog.querySelectorAll(
      "a[href],button,input,select,textarea,summary,[tabindex]",
    ),
  ].filter((el) => el.tabIndex >= 0 && !el.disabled && el.checkVisibility());
  if (document.activeElement !== (e.shiftKey ? controls[0] : controls.at(-1)))
    return;
  e.preventDefault();
  (e.shiftKey ? controls.at(-1) : controls[0]).focus();
});

function applyReadingSize(value) {
  const size = ["15", "17", "19"].includes(value) ? value : "15";
  document.documentElement.style.setProperty("--th-reading-size", size + "px");
  $("reading-size").value = size;
  try {
    localStorage.setItem("reading-size", size);
  } catch {}
}
let readingSize = "15";
try {
  readingSize = localStorage.getItem("reading-size") || "15";
} catch {}
applyReadingSize(readingSize);
$("reading-size").onchange = () => applyReadingSize($("reading-size").value);
function updateHeaderToastOffset() {
  const header = $("conversation-title").closest("header");
  if (header)
    document.documentElement.style.setProperty(
      "--th-conversation-header-bottom",
      Math.ceil(header.getBoundingClientRect().bottom) + "px",
    );
}
new ResizeObserver(updateHeaderToastOffset).observe(
  $("conversation-title").closest("header"),
);
window.addEventListener("resize", () => {
  updateComposer();
  updateLatest();
  updateHeaderToastOffset();
  syncQuotaDock();
});
updateHeaderToastOffset();
updateComposer();

$("vpn-login-form").onsubmit = async (e) => {
  e.preventDefault();
  try {
    await post("/v1/login", { token: $("vpn-login-token").value });
    location.reload();
  } catch (error) {
    $("vpn-login-error").textContent = error.message;
  }
};
function showApproval(data) {
  if (document.getElementById("approval-" + data.approval_id)) return;
  const box = document.createElement("section");
  box.id = "approval-" + data.approval_id;
  box.className = "approval-card";
  const title = document.createElement("h3");
  title.textContent = "Authorize this action?";
  // Codex file changes carry the started patch (path and diff) in `changes`.
  const changes = Array.isArray(data.request.changes)
    ? data.request.changes
    : [];
  const reason = document.createElement("p");
  reason.textContent =
    data.request.reason ||
    (changes.length
      ? "The executor wants to change these files."
      : "The executor requested additional authorization.");
  const explanation = document.createElement("p");
  explanation.textContent =
    "The administrator's permissions still apply." +
    (data.can_remember
      ? " Always allow only remembers this command and this folder, for this conversation."
      : "");
  const command = document.createElement("pre");
  const toolInput = data.request.input || {};
  command.textContent =
    changes.map((change) => change.path).join("\n") ||
    data.request.command ||
    [data.request.tool_name, toolInput.file_path || toolInput.command]
      .filter(Boolean)
      .join(" ") ||
    data.kind;
  const diff = document.createElement("pre");
  diff.className = "approval-diff";
  diff.textContent = changes
    .map((change) => change.diff)
    .filter(Boolean)
    .join("\n");
  diff.hidden = !diff.textContent;
  const details = document.createElement("details");
  const summary = document.createElement("summary");
  summary.textContent = "Technical details";
  const pre = document.createElement("pre");
  pre.textContent = JSON.stringify(data.request, null, 2);
  details.append(summary, pre);
  box.append(title, reason, command, diff, explanation, details);
  const fields = [];
  for (const q of data.request.questions || []) {
    const label = document.createElement("label");
    label.textContent = q.question;
    const input = document.createElement("input");
    input.placeholder = (q.options || []).map((o) => o.label).join(" / ");
    label.append(input);
    box.append(label);
    fields.push([q.id, input]);
  }
  let deciding = false;
  const progress = document.createElement("p");
  progress.setAttribute("role", "status");
  progress.hidden = true;
  box.append(progress);
  for (const [text, approved, scope] of [
    ["Allow once", true, "once"],
    ...(data.can_remember
      ? [["Always allow for this conversation", true, "conversation"]]
      : []),
    ["Deny", false, "once"],
  ]) {
    const button = document.createElement("button");
    button.className = "btn";
    button.textContent = text;
    button.onclick = async () => {
      if (deciding) return;
      deciding = true;
      // F-60: disabling the buttons drops focus to <body>; restore it afterwards.
      const hadFocus = box.contains(document.activeElement);
      box
        .querySelectorAll("button,input")
        .forEach((node) => (node.disabled = true));
      progress.hidden = false;
      progress.textContent = "Sending your decision…";
      try {
        const answers = Object.fromEntries(
          fields.map(([id, input]) => [id, { answers: [input.value] }]),
        );
        await post("/v1/approvals/" + data.approval_id, {
          approved,
          answers,
          scope,
        });
        if (hadFocus) $("prompt").focus({ preventScroll: true });
        box.remove();
      } catch (e) {
        progress.textContent = "Couldn't confirm your decision. " + e.message;
      } finally {
        deciding = false;
        box
          .querySelectorAll("button,input")
          .forEach((node) => (node.disabled = false));
        if (hadFocus && box.isConnected) button.focus();
      }
    };
    box.append(button);
  }
  $("messages").append(box);
  status("Waiting for your approval");
  box.scrollIntoView({ block: "nearest" });
}

for (const [id, name] of [
  ["add-project", "folder-plus"],
  ["new", "message-plus"],
  ["menu", "layout-sidebar"],
  ["attach", "plus"],
  ["send", "arrow-up"],
  ["cancel", "player-stop"],
]) {
  const b = $(id);
  if (!b) continue;
  b.classList.add("btn");
  if (["new", "send"].includes(id)) b.classList.add("btn-primary");
  if (["menu", "attach", "send", "cancel"].includes(id))
    b.classList.add("btn-icon");
  const label = {
    "add-project": "Add project",
    new: "New Conversation",
    reload: "Reload screen",
  }[id];
  b.replaceChildren(TailUI.icon(name));
  if (label) b.append(document.createTextNode(label));
}

for (const [id, name, label] of [
  ["settings-close", "x", ""],
  ["setup-close", "x", ""],
  ["search-clear", "x", ""],
  ["catalog-refresh", "refresh", "Refresh project catalog"],
]) {
  const button = $(id);
  button.classList.add("btn");
  if (!label) button.classList.add("btn-icon");
  button.replaceChildren(TailUI.icon(name));
  if (label) button.append(document.createTextNode(label));
}
for (const node of document.querySelectorAll(".brandmark,.welcome-icon"))
  node.replaceChildren(TailUI.icon("stack-2"));
for (const button of document.querySelectorAll("[data-settings]")) {
  button.textContent = button.textContent.replace(/^[^A-Za-zÀ-ÿ]+/, "");
  button.prepend(
    TailUI.icon(
      button.dataset.settings === "appearance"
        ? "adjustments"
        : button.dataset.settings === "agents"
          ? "stack-2"
          : "message",
    ),
  );
}

function attachmentNotice(filename, code) {
  const reasons = {
    model_video_unavailable:
      "the selected model or mode does not support detected MP4",
    video_processing_unavailable: "local video processing is unavailable",
    invalid_video: "the video is invalid or contains no decodable frames",
    video_frame_failed: "could not extract frames from the video",
    video_duration_limit: "the video exceeds four hours",
    audio_transcription_unavailable:
      "local speech transcription is not installed",
    audio_transcription_failed: "speech transcription failed",
    attachment_source_unavailable:
      "the file changed, disappeared, or stopped being a regular file after selection",
    file_limit: "the limit of 20 attachments per message was reached",
    upload_limit: "the file size or the project's storage exceeded the limit",
    sensitive_file: "folder import excludes hidden or sensitive files",
    symlink_denied: "symbolic links are not imported as attachments",
    invalid_image: "the image is incomplete or corrupted",
    image_validation_unavailable: "the local image validator is unavailable",
    image_validation_timeout: "image validation exceeded the allowed time",
    invalid_document:
      "the document could not be parsed by the available reader",
    unsafe_document_xml: "the XML contains declarations that are not allowed",
    document_expansion_limit:
      "the decompressed document exceeds the safety limits",
    model_images_unavailable:
      "the selected model does not support reading images",
    local_vision_not_enabled:
      "the local model has no image support enabled on this server",
    images_require_native_service:
      "the selected execution mode does not support reading images",
    unsupported_binary_format: "this format has no reader available in the app",
    binary_denied: "this binary format has no reader available in the app",
  };
  if (!Object.hasOwn(reasons, code)) return;
  $("welcome")?.remove();
  const notice = assistant("", selected()?.id);
  notice.chip.textContent = "File skipped";
  notice.body.textContent = `File “${filename}” was skipped because ${reasons[code]}.`;
  $("messages").scrollTop = $("messages").scrollHeight;
}
function attachmentError(code) {
  return (
    {
      video_capability_unavailable:
        "Couldn't check the model's MP4 support. Try again once the integration is available.",
      model_video_unavailable:
        "MP4 unavailable for this model or mode. Choose a model with detected support.",
      video_processing_unavailable:
        "Local video processing is unavailable on this server.",
      invalid_video: "The video is invalid or contains no decodable frames.",
      video_duration_limit: "Upload a video of up to 4 hours.",
      video_decode_failed: "Couldn't decode the video frames.",
      video_frame_failed: "Couldn't extract the video frames.",
      video_processing_timeout: "Video processing exceeded the allowed time.",
      invalid_image:
        "The image is incomplete or corrupted. It was not attached.",
      image_validation_unavailable:
        "The local image validator is unavailable. The image was not attached.",
      image_validation_timeout:
        "Image validation took too long. Try a smaller image.",
      invalid_filename:
        "The filename contains a path, control characters, or exceeds 160 characters.",
      upload_limit:
        "The file or the project's storage exceeded the allowed limit.",
      audio_transcription_unavailable:
        "Local audio transcription is not installed on this server.",
      audio_duration_limit: "Upload audio of up to 4 hours.",
      invalid_audio:
        "Couldn't recognize the audio. Try WAV, MP3, M4A, OGG, or FLAC.",
      audio_transcription_failed:
        "Local transcription failed; the audio was not attached.",
      local_vision_not_enabled:
        "This local server doesn't have vision enabled. You need to configure the model's visual projector (mmproj) and restart the server. The file was not attached.",
      image_capability_unavailable:
        "Couldn't check this server's vision support. Try again once it's available.",
      model_images_unavailable:
        "The selected service does not offer image reading.",
      images_require_native_service:
        "Reading images requires the native execution of the service.",
      select_model_for_image:
        "Select a model with attachment permission before sending the image.",
      image_size_limit: "Images can be up to 100 MiB.",
      unsupported_binary_format:
        "This binary format doesn't have a reader available yet. Upload a compatible image, a PDF with text, an Office/OpenDocument document, or a text file.",
      binary_denied: "This file contains binary data with no reader available.",
      invalid_document: "The document is invalid or corrupted.",
      document_expansion_limit:
        "The document exceeds the safe decompression limit.",
    }[code] || code
  );
}

document.addEventListener("click", (event) => {
  document
    .querySelectorAll(".conversation-actions[open]")
    .forEach((actions) => {
      if (!actions.contains(event.target)) actions.open = false;
    });
});

// Shared native popovers for the three concrete composer controls.
function syncAccessMode() {
  const mode = $("access-mode").value;
  $("access-label").textContent =
    $("access-mode").selectedOptions[0]?.textContent || "Ask for approval";
  $("access-mode-notice").textContent =
    "Access: " + $("access-label").textContent;
  $("access-trigger").dataset.mode = mode;
  const option = $("access-menu").querySelector('[data-access="' + mode + '"]'),
    optionIcon = option?.querySelector(".access-option-icon"),
    description = option?.querySelector("small")?.textContent || "";
  if (optionIcon) {
    const icon = optionIcon.cloneNode(true);
    icon.id = "access-trigger-icon";
    icon.setAttribute("aria-hidden", "true");
    $("access-trigger-icon").replaceWith(icon);
  }
  $("access-trigger").title = description;
  for (const item of $("access-menu").querySelectorAll("[data-access]")) {
    item.setAttribute("aria-selected", String(item.dataset.access === mode));
    const help = item.querySelector("small");
    if (help) item.title = help.textContent;
  }
}
function syncComposerPickers() {
  for (const id of ["model", "effort"]) {
    const select = $(id),
      trigger = $(id + "-trigger");
    $(id + "-label").textContent =
      select.selectedOptions[0]?.textContent ||
      (id === "model" ? "No model" : "Effort unavailable");
    if (id === "model")
      $("model-trigger-icon").textContent = modelIcon(select.value);
    if (id === "model") {
      const identity = selectedIdentity();
      $("model-trigger-icon").title = identity?.model || "";
      trigger.title = identity
        ? "Choose model · " + identity.provider + " · " + identity.model
        : "Choose model";
    }
    if (id !== "model")
      trigger.title = "Choose effort · " + $(id + "-label").textContent;
    select.disabled =
      !models.length || submitting || loading || uploads > 0 || policyPending;
    trigger.disabled = select.disabled || !select.options.length;
    if (busy)
      trigger.title +=
        " · Applies to the next message; the current response keeps its model and effort.";
    if (trigger.disabled && $(id + "-menu").matches(":popover-open"))
      $(id + "-menu").hidePopover();
  }
}
function renderPicker(id) {
  if (id === "access") {
    syncAccessMode();
    return;
  }
  const descriptions = {
    none: "No reasoning step.",
    configured: "Use the provider's configured default.",
    auto: "The coordinator chooses the effort for each step.",
    low: "Brief reasoning for simple tasks.",
    medium: "Intermediate reasoning effort.",
    high: "More reasoning for complex tasks.",
    xhigh: "Very high reasoning effort.",
    max: "Maximum effort offered by the model.",
    ultra: "The most intense reasoning level offered by the model.",
  };
  const providers = {
    local: "Local model on the server",
    qwen: "Local model on the server",
    codex: "Codex",
    claude: "Claude Code",
    gemini: "Gemini CLI",
    deepseek: "DeepSeek",
    maestro: "Model coordinator",
  };
  const groups = new Map();
  const options = [...$(id).options];
  // Sort Claude families together, newest numeric version first within each family.
  const claude = options
    .filter((o) => models.find((m) => m.id === o.value)?.backend === "claude")
    .sort(
      (a, b) =>
        a.value.split("-")[1].localeCompare(b.value.split("-")[1]) ||
        b.value.localeCompare(a.value, undefined, { numeric: true }),
    );
  let claudeIndex = 0;
  const buttons = options
    .map((o) =>
      id === "model" &&
      models.find((m) => m.id === o.value)?.backend === "claude"
        ? claude[claudeIndex++]
        : o,
    )
    .map((option) => {
      const button = document.createElement("button");
      button.type = "button";
      button.setAttribute("role", "option");
      button.dataset.value = option.value;
      button.disabled = option.disabled;
      button.setAttribute("aria-selected", String(option.selected));
      const model = models.find((m) => m.id === option.value);
      const icon = document.createElement("span");
      icon.className = "access-option-icon";
      icon.setAttribute("aria-hidden", "true");
      if (id === "model") {
        const modelGlyph = document.createElement("span");
        modelGlyph.className = "model-logo-icon";
        modelGlyph.textContent = modelIcon(option.value);
        modelGlyph.title = option.textContent;
        icon.append(modelGlyph);
      } else icon.textContent = "◷";
      const text = document.createElement("span"),
        title = document.createElement("strong"),
        detail = document.createElement("small");
      title.textContent = option.textContent;
      detail.textContent =
        id === "model"
          ? providers[model?.backend] ||
            model?.backend ||
            "Model configured on the server"
          : descriptions[option.value] ||
            "Level offered by the selected model.";
      button.title =
        "Select " +
        option.textContent +
        " · " +
        detail.textContent +
        (busy ? " · Applies to the next message." : "");
      text.append(title);
      if (id !== "model") text.append(detail);
      const check = document.createElement("span");
      check.className = "access-check";
      check.setAttribute("aria-hidden", "true");
      check.textContent = "✓";
      button.append(icon, text, check);
      if (id === "model") {
        const backend =
          model?.backend === "qwen" ? "local" : model?.backend || "other";
        if (!groups.has(backend)) {
          const group = document.createElement("details"),
            heading = document.createElement("summary"),
            list = document.createElement("div");
          group.name = "model-providers";
          group.dataset.provider = backend;
          list.className = "model-provider-options";
          list.id = "model-provider-" + backend;
          list.setAttribute("role", "listbox");
          heading.setAttribute("aria-controls", list.id);
          heading.append(
            TailUI.icon(
              {
                codex: "brand-openai",
                claude: "brand-claude",
                gemini: "brand-gemini",
                local: "stack-2",
                deepseek: "stack-2",
                maestro: "tail-harness",
              }[backend] || "stack-2",
            ),
          );
          const label =
            {
              codex: "Codex",
              claude: "Claude",
              local: "Local model",
              deepseek: "DeepSeek",
              gemini: "Google",
              maestro: "Maestro",
            }[backend] ||
            model?.backend ||
            "Others";
          group.setAttribute("role", "group");
          group.setAttribute("aria-label", label);
          heading.className = "model-provider-heading";
          heading.append(document.createTextNode(label));
          const chevron = TailUI.icon("chevron-left");
          chevron.classList.add("model-provider-chevron");
          heading.append(chevron);
          list.setAttribute("aria-label", label);
          group.append(heading, list);
          groups.set(backend, group);
          group.addEventListener("toggle", () => {
            heading.setAttribute("aria-expanded", String(group.open));
            if ($("model-menu").matches(":popover-open"))
              positionComposerPicker("model");
          });
          heading.setAttribute("aria-expanded", "false");
        }
        groups
          .get(backend)
          .querySelector(".model-provider-options")
          .append(button);
        if (option.selected) {
          groups.get(backend).open = true;
          groups
            .get(backend)
            .querySelector("summary")
            .setAttribute("aria-expanded", "true");
        }
      }
      return button;
    });
  $(id + "-menu")
    .querySelector(".picker-options")
    .replaceChildren(...(id === "model" ? groups.values() : buttons));
}
function positionComposerPicker(id) {
  const trigger = $(id + "-trigger"),
    menu = $(id + "-menu"),
    rect = trigger.getBoundingClientRect();
  menu.style.left =
    Math.max(12, Math.min(rect.left, innerWidth - menu.offsetWidth - 12)) +
    "px";
  menu.style.top = Math.max(12, rect.top - menu.offsetHeight - 10) + "px";
}
function openComposerPicker(id) {
  const trigger = $(id + "-trigger"),
    menu = $(id + "-menu");
  if (trigger.disabled) return;
  renderPicker(id);
  menu.showPopover();
  positionComposerPicker(id);
  (
    menu.querySelector('[aria-selected="true"]:not(:disabled)') ||
    menu.querySelector("[role=option]:not(:disabled)")
  )?.focus();
}
for (const id of ["access", "model", "effort"]) {
  const trigger = $(id + "-trigger"),
    menu = $(id + "-menu"),
    select = $(id === "access" ? "access-mode" : id);
  select.addEventListener("change", () => {
    syncAccessMode();
    syncComposerPickers();
  });
  trigger.onclick = () => {
    menu.matches(":popover-open") ? menu.hidePopover() : openComposerPicker(id);
  };
  menu.addEventListener("toggle", (event) =>
    trigger.setAttribute("aria-expanded", String(event.newState === "open")),
  );
  menu.addEventListener("click", (event) => {
    const option = event.target.closest("[role=option]");
    if (!option || option.disabled) return;
    select.value = option.dataset.value ?? option.dataset.access;
    select.dispatchEvent(new Event("change", { bubbles: true }));
    menu.hidePopover();
    trigger.focus();
  });
  trigger.addEventListener("keydown", (event) => {
    if (["ArrowDown", "ArrowUp"].includes(event.key)) {
      event.preventDefault();
      openComposerPicker(id);
    }
  });
  menu.addEventListener("keydown", (event) => {
    const options = [
        ...menu.querySelectorAll("summary,[role=option]:not(:disabled)"),
      ].filter(
        (el) =>
          !el.closest("details") ||
          el.tagName === "SUMMARY" ||
          el.closest("details").open,
      ),
      index = options.indexOf(document.activeElement);
    if (id === "model" && ["ArrowLeft", "ArrowRight"].includes(event.key)) {
      const group = document.activeElement.closest("details");
      if (group) {
        event.preventDefault();
        group.open = event.key === "ArrowRight";
        if (!group.open) group.querySelector("summary").focus();
      }
      return;
    }
    if (
      options.length &&
      ["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)
    ) {
      event.preventDefault();
      const next =
        event.key === "Home"
          ? 0
          : event.key === "End"
            ? options.length - 1
            : (index + (event.key === "ArrowDown" ? 1 : -1) + options.length) %
              options.length;
      options[next].focus();
    }
    if (event.key === "Tab") menu.hidePopover();
  });
}
window.addEventListener("resize", () => {
  for (const menu of document.querySelectorAll(".composer-menu:popover-open"))
    menu.hidePopover();
});
syncAccessMode();
syncComposerPickers();

// Project registration and folder selection.
const projectDirectory = {
  editing: null,
  saving: false,
  rootId: "home",
  request: 0,
  selected: new Map(),
  candidate: null,
  roots: [],
  cache: new Map(),
  expanded: new Set(),
};
const projectFolderTree = {
  foldersOnly: true,
  rootId: "home",
  cache: projectDirectory.cache,
  expanded: projectDirectory.expanded,
  selected: new Set(),
};
function renderProjectFolders() {
  const list = $("project-directory-list");
  list.replaceChildren();
  renderProjectFileEntries(list, projectDirectory.roots, projectFolderTree);
  $("project-directory-add-current").disabled =
    !projectDirectory.candidate ||
    projectDirectory.selected.has(projectDirectory.candidate.absolute_path);
}
function selectProjectFolder(entry) {
  projectDirectory.candidate = entry;
  projectFolderTree.selected.clear();
  projectFolderTree.selected.add(entry.path);
  for (const item of $("project-directory-list").querySelectorAll(
    "[role=treeitem]",
  ))
    item.setAttribute(
      "aria-selected",
      String(item.dataset.path === entry.path),
    );
  $("project-directory-selection").textContent = entry.absolute_path;
  $("project-directory-add-current").disabled = projectDirectory.selected.has(
    entry.absolute_path,
  );
}
async function loadProjectDirectories(
  path = "",
  rootId = projectDirectory.rootId,
) {
  const request = projectDirectory.request;
  let start = 1,
    entries = [],
    data;
  try {
    do {
      data = await json(
        "/v1/project-directories?root_id=" +
          encodeURIComponent(rootId) +
          "&path=" +
          encodeURIComponent(path) +
          "&start=" +
          start +
          "&limit=100",
      );
      if (request !== projectDirectory.request) return;
      entries.push(...(data.entries || []));
      start = entries.length + 1;
    } while (data.limited && (data.entries || []).length);
    if (!projectDirectory.roots.length) {
      projectDirectory.rootId = data.root_id;
      projectFolderTree.rootId = data.root_id;
      projectDirectory.roots = [
        {
          name:
            (data.roots || []).find((root) => root.id === data.root_id)
              ?.label || "Personal folder",
          path: "",
          absolute_path: data.absolute_path,
          type: "directory",
        },
      ];
      const roots = $("project-directory-roots");
      roots.replaceChildren(
        ...(data.roots || []).map((root) => {
          const button = document.createElement("button");
          button.type = "button";
          button.append(
            TailUI.icon("folder"),
            document.createTextNode(root.label),
          );
          button.title = "Browse " + root.label;
          button.setAttribute("aria-pressed", String(root.id === data.root_id));
          button.onclick = () => {
            resetProjectFolderBrowser(root.id);
            void loadProjectDirectories("", root.id);
          };
          return button;
        }),
      );
      projectDirectory.expanded.add("");
    }
    projectDirectory.cache.set(rootId + "\0" + path, entries);
    $("project-directory-error").textContent = "";
    renderProjectFolders();
  } catch (error) {
    if (request === projectDirectory.request) {
      projectDirectory.expanded.delete(path);
      renderProjectFolders();
      $("project-directory-error").textContent =
        "Couldn't list folders: " + error.message;
    }
  }
}
function resetProjectFolderBrowser(rootId = "home") {
  ++projectDirectory.request;
  projectDirectory.rootId = rootId;
  projectFolderTree.rootId = rootId;
  projectDirectory.roots = [];
  projectDirectory.cache.clear();
  projectDirectory.expanded.clear();
  projectFolderTree.selected.clear();
  projectDirectory.candidate = null;
  $("project-directory-selection").textContent = "Select a folder";
  $("project-directory-error").textContent = "";
  $("project-directory-add-current").disabled = true;
  $("project-directory-list").textContent = "Loading folders…";
}
async function toggleProjectFolder(entry) {
  if (projectDirectory.expanded.has(entry.path)) {
    projectDirectory.expanded.delete(entry.path);
    renderProjectFolders();
    return;
  }
  projectDirectory.expanded.add(entry.path);
  renderProjectFolders();
  if (!projectDirectory.cache.has(projectDirectory.rootId + "\0" + entry.path))
    await loadProjectDirectories(entry.path);
}
function renderSelectedProjectDirectories() {
  const list = $("project-selected-paths");
  list.hidden = !projectDirectory.selected.size;
  $("project-folders-empty").hidden = !!projectDirectory.selected.size;
  list.replaceChildren();
  for (const [path, name] of projectDirectory.selected) {
    const li = document.createElement("li"),
      label = document.createElement("span"),
      remove = document.createElement("button"),
      primary = document.createElement("button");
    label.textContent = name + " · " + path;
    label.title = path;
    const isPrimary = path === projectDirectory.selected.keys().next().value;
    primary.type = "button";
    primary.append(
      TailUI.icon("home"),
      document.createTextNode(isPrimary ? "Main" : "Make main"),
    );
    primary.title = isPrimary
      ? "This project's task starting directory"
      : "Use this folder as the task starting directory";
    primary.setAttribute("aria-pressed", String(isPrimary));
    primary.setAttribute(
      "aria-label",
      (isPrimary ? "Main folder: " : "Make main: ") + name,
    );
    primary.onclick = () => {
      projectDirectory.selected = new Map([
        [path, name],
        ...Array.from(projectDirectory.selected).filter(
          ([key]) => key !== path,
        ),
      ]);
      renderSelectedProjectDirectories();
    };
    remove.type = "button";
    remove.append(TailUI.icon("x"), document.createTextNode("Remove"));
    remove.title =
      "Remove the folder from the project without deleting its files";
    remove.setAttribute("aria-label", "Remove folder " + name);
    remove.onclick = () => {
      projectDirectory.selected.delete(path);
      renderSelectedProjectDirectories();
      renderProjectFolders();
    };
    li.append(label, primary, remove);
    list.append(li);
  }
}
function openProjectDialog(projectId = null) {
  if (projectDirectory.saving) return;
  projectDirectory.editing = projectId;
  projectDirectory.selected.clear();
  $("project-form").reset();
  const detail = projectDetails[projectId];
  if (detail) {
    $("project-name").value = detail.label || projectId;
    for (const path of [detail.root, ...(detail.additional_roots || [])].filter(
      Boolean,
    ))
      projectDirectory.selected.set(
        path,
        path.split("/").filter(Boolean).pop() || path,
      );
  }
  $("project-dialog-title").textContent = projectId
    ? "Edit project"
    : "Create project";
  $("project-create").replaceChildren(
    TailUI.icon(projectId ? "pencil" : "folder-plus"),
    document.createTextNode(projectId ? "Save changes" : "Create project"),
  );
  $("project-create").title = projectId
    ? "Save the name and folders while keeping the conversations"
    : "Create the project with the selected folders";
  renderSelectedProjectDirectories();
  resetProjectFolderBrowser();
  $("project-create-note").textContent = "";
  $("project-dialog").showModal();
  void loadProjectDirectories();
}
for (const [id, icon, title] of [
  [
    "project-directory-add-current",
    "folder-plus",
    "Add the selected folder to the project",
  ],
  ["project-dialog-cancel", "x", "Discard changes and close"],
  ["project-dialog-close", "x", "Close without saving changes"],
]) {
  const button = $(id);
  button.title = title;
  if (!button.querySelector("svg")) button.prepend(TailUI.icon(icon));
}
$("add-project").onclick = () => {
  if (!busy && !loading) openProjectDialog();
};
$("project-dialog-close").onclick = $("project-dialog-cancel").onclick = () => {
  if (!projectDirectory.saving) $("project-dialog").close();
};
$("project-dialog").addEventListener("cancel", (event) => {
  if (projectDirectory.saving) event.preventDefault();
});
$("project-directory-add-current").onclick = () => {
  const entry = projectDirectory.candidate;
  if (!entry || projectDirectory.selected.has(entry.absolute_path)) return;
  if (projectDirectory.selected.size >= 20) {
    $("project-create-note").textContent = "Add up to 20 folders per project.";
    return;
  }
  projectDirectory.selected.set(entry.absolute_path, entry.name);
  renderSelectedProjectDirectories();
  $("project-directory-add-current").disabled = true;
  $("project-create-note").textContent = "";
};
$("project-form").onsubmit = async (event) => {
  event.preventDefault();
  if (projectDirectory.saving) return;
  const name = $("project-name").value.trim(),
    note = $("project-create-note"),
    editing = projectDirectory.editing,
    current = $("project").value;
  if (Array.from(name.matchAll(/\p{L}/gu)).length < 3) {
    note.textContent = "The name needs at least 3 letters.";
    return;
  }
  if (!projectDirectory.selected.size) {
    note.textContent = "Add at least one folder.";
    return;
  }
  if (projectDirectory.selected.size > 20) {
    note.textContent = "Add up to 20 folders per project.";
    return;
  }
  const payload = {
    name,
    paths: Array.from(projectDirectory.selected.keys()),
    ...(editing ? { project_id: editing } : {}),
  };
  projectDirectory.saving = true;
  const controls = Array.from(
    $("project-form").querySelectorAll("button,input"),
  ).map((node) => [node, node.disabled]);
  controls.forEach(([node]) => (node.disabled = true));
  note.textContent = "Saving project…";
  try {
    const result = await json("/v1/projects", {
      method: editing ? "PATCH" : "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const p = await json("/v1/projects");
    updateProjectMetadata(p.details);
    $("project").replaceChildren(
      ...p.projects.map((id) => new Option(p.details?.[id]?.label || id, id)),
    );
    if (editing) {
      $("project").value = current;
      syncActiveProjectBadge();
      if (current === editing) {
        policyProject = null;
        invalidateResources();
        await refreshProjectPermissions();
      }
      saveView();
    } else {
      policyProject = null;
      chooseProject(result.project_id);
    }
    renderProjects();
    $("project-dialog").close();
    $("project-form").reset();
    projectDirectory.selected.clear();
    renderSelectedProjectDirectories();
    note.textContent = "";
  } catch (e) {
    const reason =
      {
        project_busy: "Wait for this project's tasks to finish before editing.",
        project_name_exists: "A project with that name already exists.",
        project_directory_required:
          "One of the folders no longer exists. Choose an available folder.",
        project_directory_forbidden:
          "That folder is protected and cannot be added.",
      }[e.code] || e.message;
    note.textContent = "Couldn't save: " + reason;
  } finally {
    projectDirectory.saving = false;
    controls.forEach(([node, disabled]) => (node.disabled = disabled));
  }
};
