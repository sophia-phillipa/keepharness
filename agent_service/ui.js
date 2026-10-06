let workspaceResourceRequest = 0;
const MAX_ATTACHMENTS = 20;
const MAX_ATTACHMENT_BYTES = 100 * 1024 * 1024;
const AUDIO_ATTACHMENT_TIMEOUT_MS = 8200000;
("use strict");
const $ = (id) => document.getElementById(id);
const prefs = window.HarnessPrefs;
let providers = {},
  models = [],
  files = [],
  observedActivityJobs = [],
  job = "",
  last = 0,
  controller = null,
  active = null,
  busy = false,
  parent = null,
  conversation = "",
  loading = false,
  uiBuild = "",
  reloadPending = false;
let queuedTurns = [];
let executionMode = "native",
  executionModeChosen = false;
let policyProject = null,
  policyPending = false,
  policyError = "",
  policySequence = 0;
let uploadsAllowed = false,
  fullAccessOffered = false,
  localOwner = false,
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
{
  const saved = prefs.get("conversation_activity", {});
  if (saved && typeof saved === "object" && !Array.isArray(saved))
    conversationActivity = saved;
}
function saveConversationActivity() {
  // The store keeps the last entries of the map it is given: put the most recently updated conversations
  // last and drop the ones the list no longer has, so the saved set follows recency, not insertion order.
  const recent = {};
  for (const c of [...conversations].sort((a, b) => conversationUpdated(a) - conversationUpdated(b)))
    if (conversationActivity[c.id]) recent[c.id] = conversationActivity[c.id];
  conversationActivity = recent;
  prefs.set("conversation_activity", conversationActivity);
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
const STATUS_DOTS = {
  "needs-you": "Needs your answer",
  running: "In progress",
  queued: "Queued",
  failed: "Failed",
  unread: "Unread response",
};
function conversationStatusKind(c = {}) {
  const state = conversationState(c);
  if (state !== "done") return state;
  if (c.state === "failed") return "failed";
  return conversationActivity[c.id]?.unread ? "unread" : "";
}
function statusDot(kind, label = STATUS_DOTS[kind], base = "conversation-indicator") {
  const indicator = document.createElement("span");
  // "working" keeps the historical class for running and queued rows.
  indicator.className =
    base + " status-" + kind +
    (base !== "conversation-indicator" ? "" : kind === "running" || kind === "queued" ? " working" : kind === "unread" ? " unread" : "");
  indicator.setAttribute("role", "img");
  indicator.setAttribute("aria-label", label);
  indicator.title = label;
  return indicator;
}
function conversationIndicator(c) {
  const kind = conversationStatusKind(c);
  return kind ? statusDot(kind) : null;
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
  el.textContent = full.length > 80 ? truncateTitle(full, 79) + "…" : full;
  el.title = full;
  el.dir = "auto";
  conversationWindowTitle = full + " — KeepHarness";
  updateWindowTitle();
  syncActiveProjectBadge();
}
// Cuts at up to `units` UTF-16 code units, then backs off one unit if that
// landed inside a surrogate pair, so the result never ends in a lone
// (unpaired) surrogate that would render as a replacement-character box.
function truncateTitle(full, units) {
  const cut = full.slice(0, units),
    last = cut.charCodeAt(cut.length - 1);
  return last >= 0xd800 && last <= 0xdbff
    ? cut.slice(0, -1).trimEnd()
    : cut.trimEnd();
}
let composerProjectId = null,
  composerGitRequest = 0,
  resourceRequest = 0,
  resourceItems = [],
  resourceSelections = [],
  activePersona = null,
  releasePersonaPending = false,
  lastSentRoute = null,
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
function unfencedPrompt(text) {
  // Match resources.unfenced while preserving every original character offset.
  let marker = null, markerDepth = 0, markerIndent = 0, listIndent = 0;
  let previousBlank = true, indented = false, quoteInList = false;
  return text.split(/(?<=\n)/).map(source => {
    let column = 0;
    let line = Array.from(source, character => {
      const expanded = character === "\t" ? " ".repeat(4 - column % 4) : character;
      column += expanded.length;
      return expanded;
    }).join("");
    if (quoteInList) {
      if (line.startsWith(" ".repeat(listIndent))) line = line.slice(listIndent);
      else if (line.trim()) { quoteInList = false; listIndent = 0; }
    }
    const prefix = /^(?: {0,3}>[ \t]?)+/.exec(line);
    let depth = prefix ? (prefix[0].match(/>/g) || []).length : 0;
    let content = prefix ? line.slice(prefix[0].length) : line;
    const blank = !content.trim(), indentation = /^ */.exec(content)[0].length;
    if (marker && depth < markerDepth) marker = null;
    if (!blank && indentation < listIndent && !quoteInList) { listIndent = 0; if (markerIndent) marker = null; }
    if (!marker) {
      let item = /^ {0,3}(?:[-+*]|[0-9]+[.)]) +/.exec(content);
      if (item) {
        listIndent = 0;
        while (item) {
          listIndent += item[0].length;
          content = content.slice(item[0].length);
          item = /^ {0,3}(?:[-+*]|[0-9]+[.)]) +/.exec(content);
        }
      }
      else if (listIndent && !quoteInList) content = content.slice(listIndent);
    } else if (markerIndent && !quoteInList) content = content.slice(markerIndent);
    const nestedQuote = /^(?: {0,3}>[ \t]?)+/.exec(content);
    if (nestedQuote) {
      quoteInList = !!listIndent; depth += (nestedQuote[0].match(/>/g) || []).length;
      content = content.slice(nestedQuote[0].length);
    }
    const codeIndent = /^(?: {4}|\t)/.test(content);
    indented = !marker && ((codeIndent && (previousBlank || indented)) || (blank && indented));
    let hidden = !!marker || indented;
    const fence = /^ {0,3}(`{3,}|~{3,})(.*)/.exec(content);
    if (fence && !indented) {
      if (!marker && (fence[1][0] !== "`" || !fence[2].includes("`"))) {
        marker = fence[1]; markerDepth = depth; markerIndent = listIndent; hidden = true;
      } else if (marker && depth === markerDepth && fence[1][0] === marker[0] && fence[1].length >= marker.length && !fence[2].trim()) { marker = null; hidden = true; }
    }
    previousBlank = blank;
    return hidden ? " ".repeat(source.length) : source;
  }).join("");
}

function selectedOccurrences() {
  const prose = unfencedPrompt($("prompt").value);
  const used = new Set();
  return resourceSelections.flatMap(ref => {
    const escaped = ref.token.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const match = [...prose.matchAll(new RegExp("(^|\\s)" + escaped + "(?=\\s|$)", "g"))].find(m => !used.has(m.index + m[1].length));
    if (!match) return [];
    const start = match.index + match[1].length;
    used.add(start);
    return [{ ref, start }];
  }).sort((a, b) => a.start - b.start);
}
function syncResourceSelections() {
  resourceSelections = selectedOccurrences().map(item => item.ref);
  const tokens = new Set($("prompt").value.split(/\s+/));
  invalidResourceTokens = new Set([...invalidResourceTokens].filter(token => tokens.has(token)));
}
function renderResourceChips() {
  let chips = $("resource-chips");
  if (!chips) {
    chips = document.createElement("div");
    chips.id = "resource-chips";
    chips.className = "resource-chips";
    chips.setAttribute("aria-label", "Selected resources");
    $("prompt").closest(".prompt-editor").before(chips);
  }
  chips.replaceChildren();
  const ordered = selectedOccurrences();
  for (const { ref: selection, start } of ordered) {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "resource-chip";
    chip.setAttribute("aria-label", "Remove " + selection.token);
    chip.textContent = selection.token + " ×";
    chip.onclick = () => {
      const input = $("prompt"), tokenEnd = start + selection.token.length,
        end = tokenEnd + (input.value[tokenEnd] === " " ? 1 : 0);
      input.value = input.value.slice(0, start) + input.value.slice(end);
      resourceSelections = resourceSelections.filter(value => value !== selection);
      invalidResourceTokens.delete(selection.token);
      updateComposer();
      input.focus();
      saveView();
    };
    chips.append(chip);
  }
  if (ordered.length > 1) {
    const chain = document.createElement("span");
    chain.className = "resource-chain-preview";
    chain.textContent =
      "Runs in order: " +
      ordered.map(({ ref }, index) => index + 1 + " " + ref.token).join(" → ");
    chips.append(chain);
  }
  chips.hidden = resourceSelections.length === 0;
}
function renderPersonaControl() {
  let control = $("persona-control");
  if (!control) {
    control = document.createElement("div");
    control.id = "persona-control";
    control.className = "persona-control";
    const label = document.createElement("span"),
      end = document.createElement("button");
    label.className = "persona-label";
    end.type = "button";
    end.className = "persona-end";
    end.textContent = "End agent conversation";
    end.onclick = () => {
      if (!activePersona) return;
      releasePersonaPending = true;
      renderPersonaControl();
      $("prompt").focus();
      saveView();
    };
    control.append(label, end);
    $("prompt").closest(".prompt-editor").before(control);
  }
  control.hidden = !activePersona;
  if (!activePersona) return;
  control.querySelector(".persona-label").textContent =
    (releasePersonaPending ? "Ending after your next message: " : "Agent conversation: ") +
    activePersona.name;
  const end = control.querySelector(".persona-end");
  end.disabled = releasePersonaPending;
  end.textContent = releasePersonaPending ? "Ending…" : "End agent conversation";
}
function setActivePersona(value, preservePending = false) {
  const keepPending =
    preservePending &&
    releasePersonaPending &&
    activePersona?.resource_id === value?.resource_id;
  activePersona = value;
  releasePersonaPending = keepPending;
  renderPersonaControl();
}
function renderPromptHighlights() {
  const input = $("prompt"),
    mirror = $("prompt-highlights"),
    tokens = new Set(resourceSelections.map((ref) => ref.token));
  mirror.replaceChildren();
  // QA-R4-5: without a highlighted token the mirror stays empty, so a long draft is never split per keystroke.
  if (!tokens.size) {
    input.classList.remove("has-resource-highlights");
    mirror.hidden = true;
    renderResourceChips();
    return;
  }
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
  renderResourceChips();
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
function setActiveResourceOption(option = null) {
  const menu = $("resource-menu");
  for (const candidate of menu.querySelectorAll("[role=option]"))
    candidate.setAttribute("aria-selected", String(candidate === option));
  $("prompt").setAttribute("aria-expanded", String(menu.matches(":popover-open")));
  if (option) $("prompt").setAttribute("aria-activedescendant", option.id);
  else $("prompt").removeAttribute("aria-activedescendant");
}
function closeResourceMenu() {
  resourceRequest++;
  const menu = $("resource-menu");
  if (menu.matches(":popover-open")) menu.hidePopover();
  menu.replaceChildren();
  setActiveResourceOption();
  $("resource-status").textContent = "";
}
function resourceKeydown(event) {
  const menu = $("resource-menu");
  if (
    !menu.matches(":popover-open") ||
    event.isComposing ||
    event.keyCode === 229
  )
    return false;
  const options = [...menu.querySelectorAll("[role=option]")],
    index = options.indexOf(document.activeElement),
    chosen = options[index] || options.find(option => option.getAttribute("aria-selected") === "true") || options[0];
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
    chosen.click();
    return true;
  }
  if (event.key === "Escape") {
    event.preventDefault();
    event.stopPropagation();
    closeResourceMenu();
    $("prompt").focus();
    return true;
  }
  if (event.key === "Tab" && options.length) {
    if (event.shiftKey || chosen.getAttribute("aria-disabled") === "true") {
      closeResourceMenu();
      $("prompt").focus();
      return false;
    }
    event.preventDefault();
    event.stopPropagation();
    chosen.click();
    return true;
  }
  if (index >= 0 && (event.key.length === 1 || ["Backspace", "Delete", "ArrowLeft", "ArrowRight"].includes(event.key))) {
    $("prompt").focus();
  }
  return false;
}
$("resource-menu").addEventListener("keydown", resourceKeydown);
$("resource-menu").addEventListener("toggle", event => {
  if (event.newState === "closed") setActiveResourceOption();
});
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
function catalogResourceMeta(item) {
  if (item.scope !== "catalog") return null;
  const commit = String(item.catalog_commit || "").trim(),
    revision = commit ? commit.slice(0, 12) : "",
    state = item.catalog_pinned ? "Pinned" : "Source",
    short = [state, revision].filter(Boolean).join(" · "),
    full = commit
      ? (item.catalog_pinned ? "pinned commit " : "source commit ") + commit
      : item.catalog_pinned
        ? "pinned commit"
        : "source catalog";
  return {
    short: short + (item.catalog_dirty ? " · Modified" : ""),
    preview:
      (item.catalog_pinned ? "Pinned commit " : "Source commit ") +
      (revision || "unknown") +
      (item.catalog_dirty ? " · modified working tree" : ""),
    title:
      "Catalog " +
      (item.origin || "resource") +
      " · " +
      full +
      (item.catalog_dirty ? " · modified working tree" : ""),
  };
}
function builtinResources() {
  return [
    {
      id: "builtin/model",
      revision: "ui",
      kind: "builtin",
      name: "model",
      description: "Choose the model for this conversation",
      scope: "builtin",
      origin: "Harness",
      group: "Built-ins",
      selectable: true,
      action: "model-trigger",
    },
    {
      id: "builtin/effort",
      revision: "ui",
      kind: "builtin",
      name: "effort",
      description: "Choose the reasoning effort",
      scope: "builtin",
      origin: "Harness",
      group: "Built-ins",
      selectable: true,
      action: "effort-trigger",
    },
    {
      id: "builtin/access",
      revision: "ui",
      kind: "builtin",
      name: "access",
      description: "Choose the access mode",
      scope: "builtin",
      origin: "Harness",
      group: "Built-ins",
      selectable: true,
      action: "access-trigger",
    },
  ];
}
function renderResourceMenu(trigger, items, loading = false, warnings = []) {
  const menu = $("resource-menu"),
    groups = new Map();
  menu.replaceChildren();
  const options = document.createElement("div");
  options.className = "resource-options";
  menu.append(options);
  const heading = document.createElement("p");
  heading.className = "access-menu-heading";
  heading.textContent =
    trigger.prefix === "@@"
      ? "KeepHarness agents"
      : trigger.prefix === "//"
        ? "KeepHarness skills and commands"
        : trigger.prefix === "@"
          ? "Available agents"
          : "Agents, skills and commands";
  options.append(heading);
  for (const warning of warnings) {
    const note = document.createElement("p");
    note.className = "resource-warning";
    note.setAttribute("role", "status");
    note.textContent = warning;
    options.append(note);
  }
  if (trigger.prefix === "//") {
    const empty = document.createElement("p");
    empty.className = "resource-empty";
    empty.textContent = "KeepHarness resources are not available yet.";
    options.append(empty);
  } else if (loading) {
    const row = document.createElement("p");
    row.className = "resource-empty";
    row.textContent = "Refreshing resources…";
    options.append(row);
  } else {
    let optionIndex = 0;
    for (const item of items) {
      const scope =
          item.scope === "project"
            ? "Project"
            : item.scope === "harness"
              ? "Yours"
            : item.scope === "catalog"
              ? "Catalog"
              : item.scope === "builtin"
                ? "Built-in"
              : "User",
        category = item.group || (item.kind === "agent" ? "Agents" : item.kind === "skill" ? "Skills" : item.kind === "workflow" ? "Workflows" : item.kind === "builtin" ? "Built-ins" : "Commands"),
        groupKey = category + "\0" + scope + "\0" + item.origin;
      if (!groups.has(groupKey)) {
        const section = document.createElement("section"),
          title = document.createElement("h3");
        section.className = "resource-group";
        title.textContent =
          item.scope === "harness" ? category : category + " · " + scope + " · " + item.origin;
        section.append(title);
        groups.set(groupKey, section);
        options.append(section);
      }
      const option = document.createElement("button");
      option.type = "button";
      option.setAttribute("role", "option");
      option.id = "resource-option-" + optionIndex++;
      option.setAttribute("aria-selected", "false");
      option.className = "resource-option";
      option.dataset.resourceId = item.id;
      option.dataset.resourceRevision = item.revision;
      option.dataset.resourceKind = item.kind;
      option.setAttribute("aria-disabled", String(item.selectable === false));
      option.title = item.source || item.origin || item.name;
      option.setAttribute("aria-describedby", "resource-preview");
      const glyph = document.createElement("span");
      glyph.className = "resource-origin-icon";
      glyph.setAttribute("aria-hidden", "true");
      glyph.append(
        item.scope === "harness" ? providerModelIcon(item.backend, item.model) : resourceIcon(item),
      );
      const text = document.createElement("span"),
        name = document.createElement("strong"),
        description = document.createElement("small"),
        catalog = catalogResourceMeta(item);
      name.textContent = item.name;
      description.textContent =
        (item.kind === "agent"
          ? "Agent"
          : item.kind === "skill"
            ? "Skill"
            : item.kind === "workflow"
              ? "Workflow"
            : item.kind === "builtin"
              ? "Built-in"
            : "Command") +
        (item.description ? " · " + item.description : "") +
        (catalog?.short ? " · " + catalog.short : "") +
        (item.unavailable_reason ? " · " + item.unavailable_reason : "");
      text.append(name, description);
      option.append(glyph, text);
      option.onclick = () => { if (item.selectable !== false) selectResource(item, trigger); };
      option.onfocus = () => {
        setActiveResourceOption(option);
        renderResourcePreview(item);
      };
      option.onpointerenter = () => renderResourcePreview(item);
      groups.get(groupKey).append(option);
    }
  }
  if (trigger.prefix[0] === "@" && !loading) {
    const create = document.createElement("button");
    create.type = "button";
    create.className = "resource-create";
    create.append(HarnessUI.icon("plus"), document.createTextNode("Create agent…"));
    create.onclick = () => {
      closeResourceMenu();
      openAgentDialog();
    };
    options.append(create);
  }
  if (
    (trigger.prefix[0] === "@" &&
      !items.some((item) => item.kind === "agent")) ||
    (trigger.prefix[0] === "/" && !items.length)
  ) {
    const empty = document.createElement("p");
    empty.className = "resource-empty";
    empty.textContent = loading
      ? ""
      : trigger.prefix[0] === "@"
        ? "No agents for this model yet."
        : "No skills or commands for this model yet.";
    options.append(empty);
  }
  const preview = document.createElement("div");
  preview.id = "resource-preview";
  preview.className = "resource-preview";
  preview.setAttribute("aria-live", "polite");
  menu.append(preview);
  const first = items.find((item) => item.selectable !== false) || items[0];
  if (first) renderResourcePreview(first);
  menu.hidden = false;
  if (!menu.matches(":popover-open")) menu.showPopover();
  setActiveResourceOption(
    menu.querySelector('[role=option]:not([aria-disabled="true"])') ||
      menu.querySelector("[role=option]"),
  );
  $("resource-status").textContent = loading ? "Refreshing resources…" :
    (menu.querySelector(".resource-empty")?.textContent || "");
  const rect = $("prompt").getBoundingClientRect();
  const availableHeight = Math.max(24, rect.top - 20);
  menu.style.maxHeight = availableHeight + "px";
  // Reserve the majority of the visible menu for selectable rows, even at native zoom.
  menu.style.setProperty("--resource-preview-height", Math.max(0, Math.min(160, (availableHeight - 24) * 0.4)) + "px");
  menu.style.left =
    Math.max(12, Math.min(rect.left, innerWidth - menu.offsetWidth - 12)) +
    "px";
  menu.style.top = Math.max(12, rect.top - menu.offsetHeight - 8) + "px";
}
function renderResourcePreview(item) {
  const preview = $("resource-preview");
  if (!preview || !item) return;
  const title = document.createElement("strong"),
    description = document.createElement("p"),
    details = document.createElement("small");
  title.textContent = (item.kind || "Resource") + " · " + item.name;
  description.textContent = item.description || "No description provided.";
  const catalog = catalogResourceMeta(item);
  details.textContent = [
    item.argument_hint ? "Arguments " + item.argument_hint : "",
    catalog?.preview || "",
    item.source || "",
    item.preflight_hint || item.unavailable_reason || "",
  ]
    .filter(Boolean)
    .join(" · ");
  preview.title = catalog?.title || "";
  preview.replaceChildren(title, description, details);
}
function selectResource(item, trigger) {
  if (item.selectable === false) return;
  if (item.kind === "builtin") {
    const input = $("prompt"),
      before = input.value.slice(0, trigger.start),
      after = input.value.slice(trigger.end);
    input.value = before + after;
    closeResourceMenu();
    updateComposer();
    saveView();
    const action = $(item.action);
    action?.focus();
    action?.click();
    return;
  }
  const harnessAgent = item.scope === "harness" && item.kind === "agent",
    marker = harnessAgent ? "@@" : trigger.prefix[0] === "@" ? "@" : "/",
    token = marker + item.name;
  if (harnessAgent) applyAgentRoute(item);
  const input = $("prompt"),
    before = input.value.slice(0, trigger.start),
    after = input.value.slice(trigger.end);
  // A token chosen from the Agents chip may follow a word directly.
  const gap = before && !/\s$/.test(before) ? " " : "";
  input.value = before + gap + token + " " + after;
  const caret = (before + gap + token + " ").length;
  input.setSelectionRange(caret, caret);
  resourceSelections.push({ id: item.id, revision: item.revision, token });
  invalidResourceTokens.delete(token);
  closeResourceMenu();
  input.focus();
  updateComposer();
  saveView();
}
// QA-R2-4: the resource list is fetched once per project and engine, then filtered locally while typing.
const RESOURCE_CACHE_MS = 30000;
const resourceCache = { key: "", at: 0, warnings: [] };
function clearResourceItems() {
  resourceItems = [];
  resourceCache.at = 0;
}
function showResources(trigger, warnings = []) {
  let filtered = [...resourceItems, ...builtinResources()]
    .filter((item) =>
      trigger.prefix === "@@"
        ? item.kind === "agent" && item.scope === "harness"
        : trigger.prefix === "@"
          ? item.kind === "agent"
          : ["agent", "skill", "command", "workflow", "rule", "context", "builtin"].includes(item.kind),
    )
    .map((item) => ({ item, score: resourceMatchScore(item, trigger.query) }))
    .filter((entry) => entry.score >= 0)
    .sort((left, right) => right.score - left.score)
    .map((entry) => entry.item);
  const exact = filtered.filter(
    (item) => item.name.toLowerCase() === trigger.query.toLowerCase(),
  );
  if (exact.length) filtered = exact;
  renderResourceMenu(trigger, filtered, false, warnings);
}
async function refreshResources(trigger) {
  const request = ++resourceRequest,
    m = resourceEngine(),
    project = $("project").value;
  if (!m.backend) {
    renderResourceMenu(trigger, [], false);
    return;
  }
  const key = [project, m.backend, m.model, m.execution_mode, $("access-mode").value].join("|");
  if (resourceCache.key === key && Date.now() - resourceCache.at < RESOURCE_CACHE_MS) {
    showResources(trigger, resourceCache.warnings);
    return;
  }
  renderResourceMenu(trigger, [], true);
  try {
    const query = new URLSearchParams({
      project_id: project,
      backend: m.backend,
      model: m.model,
      execution_mode: m.execution_mode,
      access_mode: $("access-mode").value,
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
    Object.assign(resourceCache, { key, at: Date.now(), warnings: Array.isArray(data.warnings) ? data.warnings : [] });
    // The menu follows what is typed now, not what was typed when the request started.
    showResources(triggerAtCaret() || trigger, resourceCache.warnings);
  } catch {
    if (request !== resourceRequest) return;
    clearResourceItems();
    renderResourceMenu(trigger, [], false);
    const note = $("resource-menu").querySelector(".resource-empty");
    if (note) note.textContent = "Couldn't refresh resources.";
  }
}
function resourceMatchScore(item, query) {
  const needle = query.toLowerCase();
  if (!needle) return 0;
  const text = (item.name + " " + (item.description || "")).toLowerCase();
  if (text.includes(needle)) return Math.max(1, 100 - text.indexOf(needle));
  let offset = 0,
    score = 0;
  for (const character of needle) {
    const found = text.indexOf(character, offset);
    if (found < 0) return -1;
    score += found === offset ? 3 : 1;
    offset = found + 1;
  }
  return score;
}
function openResourceMenu() {
  const trigger = triggerAtCaret();
  if (!trigger) {
    closeResourceMenu();
    return;
  }
  if (trigger.prefix === "//") {
    renderResourceMenu(trigger, [], false);
    return;
  }
  void refreshResources(trigger);
}
function invalidateResources() {
  for (const ref of resourceSelections) invalidResourceTokens.add(ref.token);
  resourceSelections = [];
  clearResourceItems();
  void refreshWorkspaceResources();
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
// Project folders start expanded, like the Codex sidebar; a folder the user
// collapses stays collapsed across reloads.
const expandedProjects = new Map();
for (const [id, open] of Object.entries(prefs.get("project_expanded", {})))
  expandedProjects.set(id, open === true);
const rememberExpandedProjects = () =>
  prefs.set("project_expanded", Object.fromEntries(expandedProjects));
let preferredSelection = prefs.get("chat_selection", {});
const labels = {
  maestro_planning: "Planning",
  maestro_planning_completed: "Plan ready",
  maestro_plan: "Agents selected",
  maestro_step: "Running workflow step",
  answer_delta: "Responding",
  reasoning_delta: "Thinking",
  reasoning_summary: "Reasoning summary",
  interrupted: "Interrupted",
  queued: "Queued",
  queue_wait: "Waiting in the queue",
  queue_released: "Queued message released",
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
  if (policyPending && policyError &&
      (Object.values(labels).includes(text) || ["Failed run", "Run cancelled", "Run interrupted"].includes(text))) text = policyError;
  const target = $("status");
  // Repeated progress (one per streamed delta) is announced once.
  if (target.textContent === text && target.className === "visually-hidden")
    return;
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
    HarnessUI.notice(target, text, { error: true });
  } else if (
    Object.values(labels).includes(text) ||
    /^(Completed|Copied|Failed run|Cancelled|Running|Thinking|Reasoning|Receiving response|Preparing|Using tool|Tool finished|Plan updated|Run steps|Working|Checking quota|Sending request|Loading|Connected|Cancelling|Reconnecting|Ready to chat)/i.test(
      text,
    )
  ) {
    // F-62: progress stays off screen but announced by this live region.
    target.className = "visually-hidden";
  }
};
const names = {
  "qwen-local": "Qwen3.6 · local",
  "gpt-5.6-sol": "GPT-5.6 Sol",
  "gpt-5.6-terra": "GPT-5.6 Terra",
  "gpt-5.6-luna": "GPT-5.6 Luna",
  "gpt-5.5": "GPT-5.5",
  "deepseek-flash": "DeepSeek V4.1 Flash",
  "deepseek-v4-pro": "DeepSeek V4 Pro",
};
const modelIcons = {
  "qwen-local": "✦",
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
const providerNames = HarnessUI.providerNames; // D42: shared with the admin
function providerModelIcon(backend, model) {
  backend ||= models.find(item => item.id === model)?.backend;
  return HarnessUI.icon({codex: "brand-openai", claude: "brand-claude", gemini: "brand-gemini", deepseek: "brand-deepseek"}[backend] || "stack-2");
}
let composerCondition = null, modelAvailabilityError = "";
function syncComposerAvailability() {
  const condition = composerCondition?.backend === selected()?.backend ? composerCondition : null;
  const blocked = !models.length || !!modelAvailabilityError || !!condition;
  $("model-availability").hidden = !blocked;
  $("model-availability-title").textContent = condition?.title || (modelAvailabilityError ? "Couldn't check the models" : "No model available");
  $("model-availability-detail").textContent = condition?.message || modelAvailabilityError || "Add and enable a provider in the admin panel.";
  $("prompt").disabled = blocked;
  return blocked;
}
// D42: people read "Claude Opus 4.7" and "GPT-6 Astra", never the raw identifier.
const capitalized = (word) => word[0].toUpperCase() + word.slice(1);
function friendlyModelName(id) {
  const claude = /^claude-(opus|sonnet|haiku)-(\d+)(?:-(\d{1,2}))?$/.exec(id);
  if (claude) return "Claude " + capitalized(claude[1]) + " " + claude[2] + (claude[3] ? "." + claude[3] : "");
  const gpt = /^gpt-(\d+(?:\.\d+)?)(?:-([a-z]+))?$/.exec(id);
  if (gpt) return "GPT-" + gpt[1] + (gpt[2] ? " " + capitalized(gpt[2]) : "");
  return id;
}
const modelLabel = (model) =>
  names[model.id] || (model.name && model.name !== model.id ? model.name : friendlyModelName(model.id));
const modelName = (id) => (id ? modelLabel(models.find((m) => m.id === id) || { id }) : "No model");
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
  none: "No reasoning",
  configured: "Default",
  low: "Low",
  medium: "Medium",
  high: "High",
  xhigh: "Very high",
  max: "Maximum",
  ultra: "Ultra",
};
const userErrors = {
  retry_source_not_failed: "This turn is not failed anymore, so it can't be retried. The conversation was refreshed.",
  retry_source_superseded: "A newer turn exists in this conversation, so this one can't be retried. The conversation was refreshed.",
  retry_not_supported: "Retry isn't available for workflow or scheduled turns. Use Resume workflow or run it again.",
  catalog_cwd_conflict: "Selected catalogs require different working folders. Run them separately.",
  catalog_environment_conflict: "Selected catalogs require incompatible environments. Run them separately.",
  catalog_hook_filter_unsupported: "This execution mode cannot enforce the catalog hook list. Choose a supported native provider.",
  catalog_runtime_mode_unsupported: "This isolated execution mode cannot provide the catalog runtime. Choose a supported native provider.",
  catalog_runtime_unavailable: "The catalog runtime is unavailable. Check its prerequisites in Admin.",
  catalog_preflight_failed: "Catalog prerequisites are missing. Check the catalog in Admin before trying again.",
  catalog_hook_failed: "A catalog hook failed. Check its run event before trying again.",
  catalog_hook_timeout: "A catalog hook exceeded its time limit and was stopped.",
  catalog_hook_unavailable: "A catalog hook could not start. Check its executable path in the manifest.",
  catalog_hook_output_limit: "A catalog hook exceeded the output limit and was stopped.",
  effect_integration_scope_denied: "This integration is not bound to the selected project and catalog. Update its binding in Admin.",
  integration_contract_ambiguous: "Integration contracts conflict. Keep one consistent contract in Admin and the catalog.",
  integration_contract_invalid: "The integration contract is invalid. Check its declared providers and credential fields in Admin.",
  integration_contract_unavailable: "The integration contract is missing. Configure it in Admin or the catalog manifest.",
  integration_credential_field_missing: "A required credential field is missing. Update the write-only binding in Admin.",
  integration_environment_ambiguous: "Two integrations assign conflicting values to the same environment variable.",
  integration_environment_unsupported: "This execution mode cannot inject integration credentials. Choose a supported native provider.",
  secret_binding_invalid: "The credential binding name is invalid. Update it in Admin.",
  secret_value_invalid: "A credential field is invalid. Enter a nonempty single-line value in Admin.",
  work_item_locked: "This work item is owned by a running job. Wait until its write access is released.",
  hooks_not_trusted: "A catalog hook changed or was never trusted, so it was skipped and the turn went on without it. Re-trust the catalog in Admin.",
  hooks_not_granted: "Catalog hooks were skipped because hook permission was not granted.",

  workflow_source_path_denied: "A workflow input moved outside its authorized folder. Restore it or choose a new input.",
  workflow_source_size_limit: "A workflow input exceeds the supported size. Reduce it before resuming.",
  workflow_requirement_denied: "The selected executor does not support this workflow requirement. Check permissions, integrations, operations and mode.",
  invalid_workflow_inputs: "Workflow inputs must be a JSON object.",
  invalid_workflow_recovery: "Use resume or re-run from a valid step with optional workflow inputs.",
  invalid_workflow_step: "Choose a step number from this workflow.",
  invocation_model_or_effort_mismatch: "This resource requires a different model or effort. Select its execution settings before submitting.",
  invocation_backend_mismatch: "This resource requires a different provider. Select its provider before submitting.",
  local_project_hardlink_denied: "A folder contains hardlinks that cannot be safely isolated. Remove the aliases or choose another folder.",
  workflow_already_exists: "A workflow with this name already exists. Choose another name.",
  workflow_backend_mismatch: "The workflow backend must match its invocation.",
  workflow_binding_changed: "Workflow inputs or revisions changed. Resume to validate again and request fresh approval.",
  workflow_catalog_read_only: "Save workflows in the project collection. Catalogs are read only.",
  workflow_checkpoint_missing: "This run has no recoverable workflow plan.",
  workflow_effect_not_completed: "Publication is not confirmed. Inspect its gate and effect record before retrying.",
  workflow_effect_outcome_unknown: "Publication may have happened. Reconcile its outcome before resuming or rerunning.",
  workflow_inputs_invalid: "The workflow inputs do not match the step schema.",
  workflow_invalid_condition: "Use a condition that references a prior step with from and is or equals.",
  workflow_invalid_document: "The workflow document is invalid. Check its JSON or YAML.",
  workflow_unknown_field: "The workflow contains an unsupported field. Check its field names and remove unrecognized entries.",
  workflow_unknown_step_field: "A workflow step contains an unsupported field. Check that step's field names and remove unrecognized entries.",
  workflow_invalid_effect: "Publication requires a valid effect request and publish enabled.",
  workflow_invalid_from_step: "Choose a valid starting step for this workflow.",
  workflow_invalid_gate: "The workflow gate needs a question and distinct choices.",
  workflow_invalid_id: "Use a short workflow identifier containing letters, numbers, underscores or hyphens.",
  workflow_invalid_name: "Choose a valid name for the saved workflow.",
  workflow_invalid_publish: "The workflow publish field must be true or false.",
  workflow_invalid_requirements: "This workflow requires capabilities the selected executor does not provide.",
  workflow_invalid_save_target: "Choose a project with a writable workflows collection.",
  workflow_invalid_schema: "Use the supported JSON schema fields for workflow inputs and outputs.",
  workflow_invalid_step: "The workflow contains an invalid sequential step.",
  workflow_invalid_step_id: "Each workflow step needs a unique valid identifier.",
  workflow_invalid_steps: "A workflow must contain one to twelve sequential steps.",
  workflow_invalid_version: "This server supports workflow version 1.",
  workflow_model_or_effort_denied: "Choose a model and effort enabled for this project.",
  workflow_must_be_standalone: "Select one workflow at a time.",
  workflow_output_not_approved: "The step output was not approved. Review the evidence before continuing.",
  workflow_save_local_only: "Workflows can only be saved from the computer that runs KeepHarness.",
  workflow_published_step_requires_explicit_rerun: "This changed step already published. Use an explicit re-run with fresh approval.",
  workflow_requires_successful_chain: "Only a completed, successful chain can be saved as a workflow.",
  workflow_resource_unavailable: "A required workflow resource is missing or unavailable. Refresh the catalog.",
  workflow_sequential_only: "This release supports sequential workflows without parallel or repeat steps.",
  cancellation_retry_required: "Cancellation was not saved because storage is busy. Try Cancel again.",
  job_not_held: "This message is no longer waiting for your choice.",
  workflow_source_busy: "Wait for the original run to finish or cancel it before recovery.",
  workflow_step_not_approved: "The workflow step was not approved. No further steps ran.",
  workflow_too_large: "The workflow exceeds the supported document size.",
  workflow_yaml_unavailable_use_json: "Use JSON, or install PyYAML to read YAML workflows.",

  rate_limit:
    "Too many requests in a short time. The server has temporarily limited this access.",
  submission_rate_limit:
    "You sent new requests too quickly. This request wasn't queued.",
  search_query_too_short: "Type at least two characters to search conversations.",
  search_rate_limit: "Too many searches in a short time. Wait a moment and search again.",
  queue_full:
    "The server queue is full. This request wasn't queued; wait for other runs to finish.",
  work_item_check_busy:
    "The server is busy checking work-item patterns. This request wasn't queued.",
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
    "One of the chosen folders no longer exists. Choose an available folder.",
  project_directory_forbidden:
    "One of the chosen folders is protected or not authorized.",
  // Interface preferences (ui-prefs.js).
  ui_state_local_only:
    "Interface preferences are saved only on the computer that runs the harness.",
  ui_state_unknown_key:
    "A saved interface preference was not recognized and was skipped.",
  ui_state_invalid_value:
    "An interface preference had an invalid value and was not saved.",
  ui_state_read_only:
    "Interface preferences can't be saved right now, so changes last until you reload. Check the harness state folder.",
  // Requests (F-70): every code the server can return has a sentence.
  payload_limit:
    "This message is too large to send. Shorten it or attach it as a file.",
  request_timeout: "The request took too long to arrive. Try again.",
  invalid_json:
    "The request was not understood. Refresh the page and try again.",
  object_required:
    "The request was not understood. Refresh the page and try again.",
  internal_error:
    "Something went wrong on the server. Try again; if it persists, check the harness log.",
  origin_denied:
    "This page is not allowed to reach the harness. Open it from the harness address.",
  invalid_token: "The access key is invalid. Check it and try again.",
  idempotency_conflict:
    "This request was already sent with different content. Refresh the page before sending again.",
  invalid_idempotency_key:
    "The request was not understood. Refresh the page and try again.",
  invalid_internal_field:
    "The request was not understood. Refresh the page and try again.",
  invalid_prompt: "The message could not be read. Edit it and try again.",
  prompt_required: "Write a message before sending.",
  invalid_task_label: "The task label is invalid. Shorten it and try again.",
  invalid_max_tokens:
    "The requested answer length is invalid. Refresh the page and try again.",
  invalid_access_mode:
    "That access mode is not available. Choose another one and try again.",
  access_mode_owner_only:
    "Automatic and Full access are only for the owner on the computer running KeepHarness. Choose Ask for approval or Read only.",
  full_access_disabled:
    "Full access is turned off. The owner can turn it on in Settings › System › Providers (Allow Full access), or choose another access mode.",
  invalid_parent_job:
    "The earlier message this reply continues is unavailable. Start a new conversation.",
  invalid_event_id: "Tracking could not resume. Refresh the page.",
  job_not_found: "This run no longer exists. Refresh the conversation list.",
  result_not_ready: "The run has not finished yet. Wait for it to finish.",
  job_storage_limit:
    "This project's run storage is full. Use Delete permanently on conversations you no longer need (archived ones are in Settings › Archived chats), then try again.",
  // Conversations.
  conversation_busy:
    "This conversation is still running. Wait for it to finish or cancel it first.",
  conversation_has_newer_turn:
    "This conversation has a newer message from another tab. Open it again from the list to continue.",
  conversation_not_found:
    "This conversation no longer exists; it may have been deleted in another tab.",
  conversation_context_limit:
    "This conversation is too long for the model's context. Start a new conversation or use a model with a larger context.",
  conversation_execution_mode_locked:
    "This conversation's isolation mode can't change. Start a new conversation to use another mode.",
  conversation_workspace_changed:
    "This conversation's workspace changed. Start a new conversation.",
  execution_mode_unsupported:
    "This conversation uses an execution mode this model or server no longer offers. Choose another model or start a new conversation.",
  invalid_conversation_title: "Use a title between 1 and 100 characters.",
  invalid_archived: "Archiving needs a yes or no answer. Refresh the page and try again.",
  invalid_continuation_target: "Choose Claude or ChatGPT as the app to continue in.",
  invalid_include_paths: "The include paths option must be yes or no. Refresh the page and try again.",
  service_restarted:
    "The harness restarted during this run. Send your message again.",
  model_removed:
    "The administrator removed this model during the run. Choose another model and send your message again.",
  configuration_changed:
    "The server configuration changed during the run. Send your message again.",
  context_limit_exceeded:
    "The content is too large for the model's context. Reduce the content sent at once or use a model with a larger context.",
  source_context_limit:
    "The attached content is too large for the model's context. Attach less at once.",
  // Models and providers.
  model_denied: "This model is not enabled for you. Choose another model.",
  model_or_effort_unavailable:
    "This model or reasoning level is no longer available. Choose another one.",
  project_file_forbidden: "This file belongs to private server storage and cannot be attached. Choose a document outside the server state folder.",
  backend_unavailable:
    "This provider is not available right now. Choose another model.",
  capability_unavailable:
    "This model can't do what the request needs. Choose another model.",
  unsupported:
    "This model can't run this request. Choose another model or mode.",
  no_enabled_executor_for_task:
    "No enabled model can run this task. Ask the administrator to enable one.",
  use_scoped_inference_tools:
    "This model can only work through the isolated tools. Turn isolation on or choose another model.",
  service_project_denied:
    "This provider is not enabled for this project. Choose another model or ask the administrator.",
  runtime_config_invalid:
    "The server configuration is invalid. Ask the administrator to review it in the admin panel.",
  runtime_immutable_changed:
    "A server setting that needs a restart changed. Ask the administrator to restart the harness.",
  codex_login_or_binary_unavailable:
    "Codex is not installed or not signed in on the server. Check it in the admin panel.",
  claude_login_or_binary_unavailable:
    "Claude Code is not installed or not signed in on the server. Check it in the admin panel.",
  codex_execution_failed:
    "Codex stopped before finishing the run. Check the activity and try again.",
  codex_rpc_error:
    "Codex returned an unexpected response. Try again; if it persists, update Codex.",
  codex_output_limit:
    "Codex produced more output than allowed. Narrow the request and try again.",
  deepseek_execution_failed:
    "DeepSeek stopped before finishing the run. Check the activity and try again.",
  deepseek_output_limit:
    "DeepSeek produced more output than allowed. Narrow the request and try again.",
  local_execution_failed:
    "The local model stopped before finishing the run. Check the activity and try again.",
  local_output_limit:
    "The local model produced more output than allowed. Narrow the request and try again.",
  claude_execution_failed:
    "Claude stopped before finishing the run. Check the activity and try again.",
  claude_stream_incomplete:
    "The provider stopped before the answer was complete. Send your message again.",
  claude_invalid_stream:
    "Claude returned an unexpected response. Try again; if it persists, update Claude Code.",
  claude_invalid_question:
    "Claude asked a question the harness could not safely display. Update Claude Code or revise the request.",
  claude_invalid_question_answer:
    "Claude could not use the selected answer. Ask the question again.",
  claude_output_limit:
    "Claude produced more output than allowed. Narrow the request and try again.",
  gemini_execution_failed:
    "Gemini stopped before finishing the run. Check the activity and try again.",
  gemini_output_limit:
    "Gemini produced more output than allowed. Narrow the request and try again.",
  gemini_acp_unavailable:
    "The Gemini CLI connection is unavailable. Choose another model.",
  gemini_acp_incomplete:
    "The Gemini CLI stopped before the answer was complete. Send your message again.",
  gemini_acp_invalid:
    "The Gemini CLI returned an unexpected response. Update the Gemini CLI and try again.",
  gemini_acp_title_unsupported:
    "This Gemini CLI version can't name conversations. Update the Gemini CLI.",
  gemini_client_retired:
    "This Gemini CLI version is no longer supported. Update the Gemini CLI.",
  gemini_oauth_unavailable:
    "Gemini is not signed in on the server. Choose another model.",
  gemini_effort_unavailable:
    "This reasoning level is not available for Gemini. Choose another one.",
  gemini_scoped_unsupported:
    "Gemini can't run isolated conversations. Start a native conversation.",
  gemini_access_mode_invalid:
    "That access mode is not available for Gemini. Choose another one.",
  gemini_integration_denied:
    "A Gemini integration is blocked by policy. Ask the administrator.",
  gemini_integration_unavailable:
    "A Gemini integration is unavailable. Choose another model.",
  gemini_system_auth_conflict:
    "The server's Gemini settings conflict with the harness sign-in. Ask the administrator to review them.",
  gemini_system_policy_conflict:
    "The server's Gemini policy conflicts with the harness. Ask the administrator to review it.",
  gemini_system_settings_invalid:
    "The server's Gemini settings are invalid. Ask the administrator to review them.",
  deepseek_api_configuration_required:
    "DeepSeek needs an API key. Add it in the admin panel.",
  deepseek_effort_unavailable:
    "This reasoning level is not available for DeepSeek. Choose another one.",
  local_cli_binary_unavailable:
    "The local model's command-line tool is missing on the server. Check it in the admin panel.",
  local_filesystem_isolation_unavailable:
    "The server can't isolate the local model's file access. Ask the administrator to install bubblewrap.",
  local_project_scope_invalid:
    "The local model can't reach this project's folders. Check the project folders.",
  // Step engine (workflows and declared "/" chains); codes keep their persisted names.
  maestro_model_or_effort_denied:
    "A workflow step uses a model or effort that is not enabled. Ask the administrator or edit the workflow.",
  maestro_step_not_allowed: "A workflow step is not allowed here. Edit the workflow.",
  maestro_invalid_plan_json: "The workflow steps are invalid. Check the workflow.",
  maestro_invalid_steps: "The workflow steps are invalid. Check the workflow.",
  maestro_invalid_step: "The workflow steps are invalid. Check the workflow.",
  maestro_invalid_step_description: "The workflow steps are invalid. Check the workflow.",
  maestro_step_incomplete: "A workflow step did not finish. Try again.",
  // Invocations and human option gates.
  invalid_invocation: "This resource invocation is invalid. Select it again.",
  invalid_invocation_args:
    "The resource arguments are too large or invalid. Shorten them and try again.",
  invalid_invocation_backend:
    "That resource cannot use the requested provider. Choose another resource or model.",
  invalid_invocation_id:
    "The invocation identifier is invalid. Select the resource again.",
  invalid_invocation_kind:
    "That resource type cannot be invoked. Select another resource.",
  invalid_invocation_mode:
    "That resource cannot run in the requested mode. Select it again.",
  invalid_invocation_order:
    "The resource chain order is invalid. Rebuild the chain and try again.",
  invalid_invocation_resource_id:
    "The selected resource identifier is invalid. Refresh the palette and select it again.",
  invocation_chain_limit:
    "A resource chain can contain at most 12 steps. Remove some steps and try again.",
  invocation_selection_mismatch:
    "The selected resources no longer match this invocation. Select them again.",
  conversational_chain_unsupported:
    "A conversational agent must run by itself. Remove the other resource steps.",
  active_persona_resource_conflict:
    "End the current agent conversation before selecting another resource.",
  invalid_gate_question:
    "The provider asked an invalid question. Revise the request and try again.",
  invalid_gate_options:
    "The provider supplied invalid answer choices. Revise the request and try again.",
  invalid_gate_choice: "That answer is no longer available. Choose again.",
  approval_already_resolved: "This approval was already decided in another view. Refresh to see the accepted decision.",
  workflow_source_unavailable: "A workflow input file is missing or unreadable. Restore the workspace input files or attachment sources before resuming.",
  gate_already_resolved: "This question was already answered.",
  gate_expired: "This question expired. Ask the agent to present it again.",
  gate_invalidated:
    "This question was invalidated by an execution change. Ask the agent to present it again.",
  // Publication requests and evidence-based recovery.
  effect_already_used:
    "This publication request was already handled. Review its recorded outcome before preparing another request.",
  effect_approval_required:
    "This publication needs approval from an enrolled human session before it can be sent.",
  effect_arguments_invalid:
    "This publication has unsupported arguments. Ask the agent to prepare a valid request.",
  effect_sensitive_content: "Publication was not prepared because it contains private credentials. Remove the sensitive content and request approval again.",
  effect_artifact_invalid:
    "The publication artifact is invalid. Ask the agent to check its required fields and prepare it again.",
  effect_binding_changed:
    "The publication changed after it was prepared. Review a newly prepared request and approve it again.",
  effect_contract_invalid:
    "This publication integration is not configured correctly. Ask the server owner to check its settings.",
  effect_credentials_not_private:
    "The publication credentials are not stored privately. Ask the server owner to correct the credential store permissions.",
  effect_credentials_unavailable:
    "The harness cannot access this integration's credentials. Ask the server owner to check its credential binding.",
  effect_destination_denied:
    "This destination is not allowed for the publication integration. Choose an allowed destination.",
  effect_duplicate_outcome_pending:
    "An identical publication is already executing, complete, or has an unknown outcome. Review its recorded outcome before preparing another publication.",
  effect_execution_inactive:
    "This run can no longer prepare a publication. Start a new message if you still need it.",
  effect_integration_unavailable:
    "This publication integration is unavailable. Ask the server owner to check its configuration.",
  effect_not_found: "This publication request could not be found. Refresh the run console.",
  effect_not_unknown:
    "This publication no longer needs reconciliation. Refresh the run console to see its recorded outcome.",
  effect_operation_unsupported:
    "This integration does not support that publication operation. Only creating a Jira issue is available.",
  effect_prepare_limit:
    "This execution has reached its publication preparation limit. Review its existing publication requests.",
  effect_reconcile_backoff:
    "Wait before checking again so Jira search has time to catch up. The outcome remains unknown; this will not retry publication.",
  effect_reconcile_invalid:
    "Choose whether to check external evidence or keep the publication outcome unknown.",
  effect_request_invalid:
    "This publication request is invalid. Ask the agent to prepare it again with the required fields.",
  effect_request_too_large:
    "This publication request is too large. Reduce the artifact content and prepare it again.",
  unsafe_scoped_home:
    "This execution cannot start because its isolated workspace is unsafe. Ask the server owner to check its workspace configuration.",
  scoped_private_file_linked:
    "This isolated run cannot start because private server state has a linked copy. Ask the server owner to remove the link before retrying.",
  scoped_private_files_unavailable:
    "This isolated run cannot verify private server state. Ask the server owner to check its storage and permissions, then retry.",
  // Projects, folders and workspaces.
  invalid_project: "This project is invalid. Choose another one.",
  project_busy: "This project is busy with another change. Try again shortly.",
  project_edit_forbidden: "You can't edit this project.",
  project_registration_disabled:
    "Adding projects is turned off on this server. Ask the administrator to enable it.",
  project_management_local_only:
    "Project folders can only be added, changed or deleted from the computer that runs KeepHarness.",
  host_denied: "This address is not one KeepHarness answers on. Open it by its usual address.",
  funnel_denied:
    "KeepHarness does not answer requests from the public internet. Turn off Tailscale Funnel for it.",
  host_files_owner_only:
    "Only the owner can browse or attach files from the folders of the computer that runs KeepHarness.",
  project_directory_shared:
    "One of the chosen folders already belongs to another project.",
  project_folder_busy:
    "The project folder is in use by a run. Wait for it to finish.",
  project_folder_changed:
    "The project folder changed since you opened it. Review it and try again.",
  project_folder_confirmation_required: "Confirm the folder name to delete it.",
  project_folder_deleted: "The project folder was already deleted.",
  project_folder_delete_failed:
    "The project folder could not be deleted. Check its permissions.",
  project_folder_deletion_unsupported:
    "This project's folder can't be deleted from the app.",
  project_has_no_directory: "This project has no folder yet. Add one first.",
  project_root_denied: "This folder is outside the project's allowed folders.",
  project_root_unavailable:
    "The project folder is missing or unreadable. Check it on the server.",
  system_root_denied: "System folders can't be used here.",
  directory_not_found: "This folder no longer exists. Refresh the list.",
  path_not_authorized: "This path is not authorized for this project.",
  read_denied: "You don't have permission to read this file.",
  file_not_found: "The file no longer exists. Refresh the list.",
  invalid_path: "The path is invalid.",
  invalid_query: "The search is invalid. Change it and try again.",
  invalid_range: "The requested part of the list is invalid. Refresh it.",
  invalid_selection: "The selection is invalid. Select the files again.",
  invalid_file_id: "An attachment is no longer available. Attach it again.",
  empty_workspace: "There is nothing to download in this workspace yet.",
  preview_not_available: "A preview is not available for this file.",
  workspace_not_found: "This workspace no longer exists.",
  workspace_path_denied: "That workspace path is not allowed.",
  workspace_permission_denied:
    "You don't have permission to use this workspace.",
  workspace_project_denied: "This workspace belongs to another project.",
  workspace_size_limit: "The workspace is larger than allowed.",
  workspace_size_or_encryption_limit:
    "The archive is too large or encrypted. Upload a smaller, unencrypted archive.",
  workspace_storage_limit:
    "The workspace storage is full. Remove files and try again.",
  workspace_export_limit: "The workspace is too large to download at once.",
  workspace_file_limit: "The workspace has too many files.",
  invalid_archive: "The archive is invalid or damaged.",
  unsafe_archive_entry:
    "The archive contains a path that is not allowed. It was not extracted.",
  binary_file_use_download:
    "This is a binary file. Download it instead of opening it.",
  invalid_staged_file:
    "A file prepared for the isolated run is invalid. Attach it again.",
  staged_context_limit:
    "The files prepared for the isolated run are too large. Attach fewer files.",
  deployment_path_denied:
    "The changes touch a path that is not allowed. They were not applied.",
  deployment_file_limit:
    "The changes touch too many files. They were not applied.",
  deployment_conflict:
    "The files changed while the run worked. The changes were not applied.",
  deployment_javascript_syntax:
    "The changes contain a JavaScript syntax error. They were not applied.",
  deployment_python_syntax:
    "The changes contain a Python syntax error. They were not applied.",
  restart_schedule_failed:
    "The changes were applied, but the panel could not schedule its restart.",
  service_control_denied: "You can't control this service.",
  invalid_service_action: "That service action is not available.",
  invalid_service_unit: "That service is not available.",
  service_not_registered: "That service is not registered for this project.",
  service_manager_unavailable_requires_systemd_user:
    "Service control needs the user systemd manager on the server.",
  // Approvals.
  ambiguous_work_item: "The invocation matches more than one work item. Tag the run with one key or make the arguments unambiguous.",
  invalid_work_item: "Use a work-item key with at most 128 characters and no control characters.",
  invalid_work_item_pattern: "The configured work-item pattern is invalid. Ask the project administrator to correct it.",
  work_item_project_required: "Choose a project before filtering by work item.",
  invalid_event_limit: "The event page size is invalid. Reload the run console and try again.",
  invalid_include_content: "The content visibility option is invalid. Reload the run console and try again.",
  approval_expired:
    "This approval request expired. Send your message again if you still need it.",
  approval_expiration_limit:
    "The run was cancelled after repeated approval requests expired. Send your message again when you are ready to respond.",
  approval_session_required:
    "This browser is not enrolled to approve actions yet. Ask the admin of this KeepHarness to enroll this browser for your own account (keepharness approve-device), then open the link they send you and try again.",
  approval_session_expired:
    "This browser's approval session expired (approval sessions last 7 days). The approval is still waiting: ask the admin of this KeepHarness for a new enrollment link (keepharness approve-device), open it in this browser, then approve again.",
  approval_storage_unsafe:
    "Approval sessions could not be stored securely. Ask the server owner to check the state directory permissions before trying again.",
  approval_enrollment_invalid:
    "This device enrollment link expired or was already used. Ask the owner for a new link.",
  approval_owner_unknown:
    "Choose an existing owner id when enrolling this browser.",
  enrollment_rate_limit:
    "Too many enrollment attempts. Wait a moment before trying again.",
  session_rate_limit:
    "Too many session authentication attempts. Wait a moment before trying again.",
  invalid_provider_capacity:
    "The provider capacity is invalid. Ask the administrator to set a positive whole number.",
  provider_idle_timeout:
    "The provider stopped responding. Review the run details and try again.",
  active_runtime_timeout:
    "The run reached its active time limit. Human approval waiting time was excluded.",
  invalid_approval_scope: "That approval option is not available.",
  // Resources.
  resource_read_denied:
    "This model can't read the selected resources. Choose another model.",
  resource_changed:
    "A selected resource changed. Select it again before sending.",
  resource_unavailable:
    "A selected resource is no longer available. Remove it and try again.",
  resource_unavailable_in_engine:
    "A selected resource is not available for this model. Remove it or choose another model.",
  resource_selection_missing:
    "A selected resource is missing. Select it again before sending.",
  resource_name_ambiguous:
    "More than one resource has that name. Pick it from the list.",
  resource_prompt_limit:
    "The selected resources are too large. Select fewer resources.",
  resource_scan_limit:
    "There are too many resources to list. Narrow the search.",
  invalid_resource_selections:
    "The selected resources are invalid. Select them again.",
  invalid_command_arguments: "The command arguments are invalid.",
  resources_unavailable_in_workspace:
    "Resources are not available in this workspace.",
  harness_resources_unavailable: "Resources are not available right now.",
  harness_agent_exists: "An agent with that name already exists.",
  harness_agent_invalid: "The agent details are not valid. Check each field and try again.",
  harness_agent_limit: "You have reached the limit of 100 agents. Delete one to add another.",
  harness_agent_changed: "This agent was changed elsewhere. Reload it and try again.",
  harness_agent_not_found: "That agent no longer exists.",
  harness_agent_storage_unsafe: "The agents folder cannot be used safely. Check the harness state folder.",
  harness_agent_local_only: "Agents can only be created, edited or deleted from the computer that runs KeepHarness.",
  page_invalid: "The page is not valid. Check the title and the text and try again.",
  page_not_found: "That page no longer exists.",
  page_changed: "This page was changed elsewhere. Reload it and try again.",
  page_limit: "This project has reached the limit of 500 pages. Delete one to add another.",
  page_storage_unsafe: "The pages folder cannot be used safely. Check the harness state folder.",
  schedule_invalid: "The schedule is not valid. Check each field and try again.",
  schedule_agent_unselected: "Pick the agent in the Agent field instead of typing @@name in the prompt.",
  schedule_agent_missing: "The agent of this schedule no longer exists. Pick another agent or remove it.",
  schedule_page_missing: "A page of this schedule no longer exists. Open the schedule and untick it.",
  schedule_not_found: "That schedule no longer exists.",
  schedule_changed: "This schedule was changed elsewhere. Reload it and try again.",
  schedule_limit: "You have reached the limit of 50 schedules. Delete one to add another.",
  schedule_storage_unsafe: "The schedules folder cannot be used safely. Check the harness state folder.",
};
// The 403 body names the caller's owner id, and a guest's Tailscale login. Name one in the
// command only when it is safe to paste into a shell; otherwise keep the generic text, which
// names no owner. Only the owner on this computer can run the command; a guest asks the owner
// of this KeepHarness, by the login the owner knows them by (PRD-R4-5).
const SAFE_OWNER_ID = /^[\w@][\w.@+-]{0,127}$/;
function enrollmentMessage(owner, login) {
  if (owner === "local")
    return "This browser is not enrolled to approve actions yet. On this computer run: keepharness approve-device --owner local, then open the link it prints in this browser and try again.";
  const id = typeof login === "string" && SAFE_OWNER_ID.test(login) ? login : owner;
  return typeof id === "string" && SAFE_OWNER_ID.test(id)
    ? "This browser is not enrolled to approve actions yet. Ask the owner of this KeepHarness to run keepharness approve-device --owner " +
        id +
        " and send you the link, then open it in this browser and try again."
    : userErrors.approval_session_required;
}
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
      (e.code === "approval_session_required" && enrollmentMessage(e.owner, e.login)) ||
      userErrors[e.code] ||
      attachmentError(e.code) ||
      (r.status === 429
        ? "The server applied a temporary limit to this request."
        : "The server did not complete the request. Check the data or try again.");
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
    error.field = e.field;
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
function composerModels(catalog) {
  return catalog.models.filter(m => HarnessUI.selectableModel(m.backend, m.id));
}
function setBusy(value) {
  value = value || streamDisconnected;
  busy = value;
  syncNavButtons();
  $("prompt").readOnly = loading;
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
      previousName = modelName(previous),
      effort = $("effort").value;
    models = composerModels(data);
    uploadsAllowed = data.uploads_enabled === true;
    offerFullAccess(data.full_access === true);
    localOwner = data.local_owner === true;
    $("model").replaceChildren(
      ...models.map((m) => new Option(modelLabel(m), m.id)),
    );
    if (models.some((m) => m.id === previous)) $("model").value = previous;
    // F-90: never switch the draft to another model silently.
    else if (previous && models.length) selectionNotice(previousName);
    policyProject = project;
    policyPending = false;
    if (policyError) {
      const showingPolicyError = $("status").textContent === policyError;
      policyError = "";
      if (showingPolicyError) status("Ready to chat");
    }
    updateEfforts();
    if ([...$("effort").options].some((o) => o.value === effort))
      $("effort").value = effort;
  } catch (e) {
    if (sequence !== policySequence) return;
    policyError = "Couldn't load this project's permissions. Select it again to retry: " + e.message;
    status(policyError);
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
  // QA-R4-4: at the limit the button says why instead of failing after a pick.
  const full = files.length >= MAX_ATTACHMENTS;
  $("attach").disabled = busy || loading || uploads > 0 || !allowed || full;
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
  $("attach").title = full
    ? MAX_ATTACHMENTS + " of " + MAX_ATTACHMENTS + " files attached. Remove one to add another."
    : allowed
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
  // CDX-R4-3: a fresh choice starts on the provider's default, or Medium, not on whatever is listed first.
  const preferred = ["configured", "medium"].find((e) => m.efforts.includes(e));
  if (preferred) $("effort").value = preferred;
  $("model-note").textContent =
    m.backend === "local"
      ? "Local model runs on the server through the Codex CLI · no OpenAI quota · check the sources"
      : m.backend === "deepseek"
        ? "DeepSeek API · uses your DeepSeek credits · runs through the Codex CLI"
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
const quotaPrimedAt = new Map(); // backend -> last /v1/usage read, in memory only
const railQuotaSummaries = new Map(); // backend -> why its rail meter reads n/a, for the panel
const quotaOwnerOnly = new Set(); // backends that answered 403 quota_owner_only this session
let quotaIdentityBackend = "",
  quotaView = "",
  quotaFocus = null; // the provider whose meter was activated; null follows the selected model
const quotaHeadings = {
  codex: "ChatGPT account quota",
  claude: "Claude Code subscription quota",
  gemini: "Gemini subscription quota",
  deepseek: "DeepSeek balance",
  local: "Local models",
};
const quotaNotes = {
  codex: "Shared with the account's other usage. Rounded percentages don't measure this run's exact cost.",
  claude: "Shared with the account's other Claude usage. Percentages are what Claude Code last reported.",
  deepseek: "Prepaid balance from your DeepSeek account, read every few minutes. It is not an estimate of this run's cost.",
};
const quotaViewBackend = () => quotaFocus || selected()?.backend || "";
function quotaDetailText(view) {
  return (
    {
      local: "This model runs locally. Context usage appears separately in the context indicator.",
      gemini: "Gemini CLI reports its own subscription usage; check it in your Google account.",
    }[view] || "Select a model to check the provider quota."
  );
}
function renderQuotaIdentity() {
  const model = selected(),
    backend = model?.backend || "",
    view = quotaViewBackend(),
    modelChanged = backend !== quotaIdentityBackend,
    changed = modelChanged || view !== quotaView;
  quotaIdentityBackend = backend;
  quotaView = view;
  $("quota-model-icon").textContent = model ? modelIcon(model.id) : "◈";
  $("quota-model-name").textContent = model ? modelName(model.id) : "Model";
  $("quota-model-identity").title = model
    ? modelName(model.id) + " · " + (providerNames[backend] || backend)
    : "Selected model";
  $("quota-toggle").hidden = false;
  // L55: the panel names the provider it explains, and its note matches that provider.
  $("quota-heading-title").textContent = quotaHeadings[view] || "Provider quota";
  $("quota-note").textContent = quotaNotes[view] || "";
  $("quota-note").hidden = !quotaNotes[view];
  const states = {
    local: "No provider quota",
    claude: "Checking Claude quota…",
    deepseek: "DeepSeek credits",
    gemini: "Checking Gemini quota…",
  };
  if (backend === "codex" || backend === "claude") {
    if (modelChanged)
      $("quota-short").textContent =
        backend === "claude" ? "Checking Claude quota…" : "Checking quota…";
  } else {
    $("quota-short").textContent = states[backend] || "Quota unavailable";
  }
  if (["codex", "claude", "deepseek"].includes(view)) {
    if (changed) {
      $("quota-current").textContent =
        view === "deepseek"
          ? "This model uses your own DeepSeek account credits. Checking the balance…"
          : view === "claude"
            ? "Checking Claude quota…"
            : "Checking quota…";
      $("quota-comparison").replaceChildren();
    }
  } else {
    $("quota-comparison").replaceChildren();
    const detail = document.createElement("p");
    detail.textContent = quotaDetailText(view);
    $("quota-current").replaceChildren(detail);
    if (quotaFocus && railQuotaSummaries.has(view)) {
      const reason = document.createElement("p");
      reason.textContent = railQuotaSummaries.get(view);
      $("quota-current").append(reason);
    }
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
function paintQuota(q, backend = quotaViewBackend()) {
  if (quotaViewBackend() !== backend || !["codex", "claude"].includes(backend))
    return;
  const available = q?.available === true,
    windows = available ? quotaWindows(q, backend) : [];
  const source =
    backend === "claude"
      ? "Latest information from Claude"
      : "Shared Codex account quota";
  // The header summary describes the selected model; another provider's meter only fills the panel.
  const forSelected = selected()?.backend === backend;
  if (forSelected)
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
  if (forSelected) {
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
  }
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
// PRD-R2-11: the balance of a prepaid DeepSeek key, as the provider reported it.
function paintBalance(value) {
  const notes = [];
  if (value?.available === true) {
    for (const b of value.balances || []) {
      const row = document.createElement("div");
      row.className = "quota-window";
      row.textContent = `${b.currency} ${b.total} available`;
      const detail = document.createElement("small");
      detail.textContent = `Granted ${b.granted} · topped up ${b.topped_up}`;
      row.append(detail);
      notes.push(row);
    }
    if (value.account_active === false) {
      const warn = document.createElement("p");
      warn.textContent = "DeepSeek reports this balance as unavailable for requests. Top it up in your DeepSeek account.";
      notes.push(warn);
    }
  } else {
    const miss = document.createElement("p");
    miss.textContent = "This model uses your own DeepSeek account credits. The balance could not be read right now, and no amount was estimated.";
    notes.push(miss);
  }
  $("quota-current").replaceChildren(...notes);
}
let quotaRequest = 0;
const quotaUrls = {
  codex: "/v1/usage",
  claude: "/v1/usage?backend=claude",
  deepseek: "/v1/usage?backend=deepseek",
};
async function quota() {
  const request = ++quotaRequest,
    backend = quotaViewBackend();
  renderQuotaIdentity();
  if (!quotaUrls[backend]) {
    // A meter without a reading opens the panel with its reason; only a model switch closes it.
    if (!quotaFocus) setQuotaOpen(false);
    return;
  }
  // A prepaid balance changes only when money moves: read it when the panel opens, not on every run.
  if (backend === "deepseek" && $("quota-panel").hidden) return;
  const paint = (value) =>
    backend === "deepseek" ? paintBalance(value) : paintQuota(value, backend);
  try {
    quotaPrimedAt.set(backend, Date.now());
    const value = await json(quotaUrls[backend]);
    if (request === quotaRequest && quotaViewBackend() === backend) paint(value);
  } catch (error) {
    noteQuotaOwnerOnly(backend, error);
    if (request === quotaRequest && quotaViewBackend() === backend) paint(null);
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
function conversationState(c = {}) {
  const value = String(c.state || "").toLowerCase();
  if (c.needs_you || ["needs_you", "awaiting_approval", "approval_required"].includes(value))
    return "needs-you";
  if (["running", "loading", "planning"].includes(value)) return "running";
  if (value === "queued") return "queued";
  return "done";
}
function conversationAge(c = {}) {
  const raw = c.updated ?? c.updated_at ?? c.created_at ?? c.created ?? 0;
  let stamp = Number(raw);
  if (!Number.isFinite(stamp) && typeof raw === "string")
    stamp = Date.parse(raw) / 1000;
  if (!stamp) return "";
  const seconds = Math.max(0, Date.now() / 1000 - stamp);
  if (seconds < 60) return "now";
  if (seconds < 3600) return Math.floor(seconds / 60) + "m";
  if (seconds < 86400) return Math.floor(seconds / 3600) + "h";
  return Math.floor(seconds / 86400) + "d";
}
function conversationUpdated(c = {}) {
  const raw = c.updated ?? c.updated_at ?? c.created_at ?? c.created ?? 0;
  const numeric = Number(raw);
  if (Number.isFinite(numeric) && numeric > 0) return numeric;
  const parsed = typeof raw === "string" ? Date.parse(raw) / 1000 : 0;
  return Number.isFinite(parsed) ? parsed : 0;
}
function waitReasonLabel(reason) {
  return ({ human_approval: "Waiting for your approval", conversation_parent: "Waiting for the previous response", provider_capacity: "Waiting for another task on this provider", held_after_stop: "Held after Stop", conversation: "Waiting for this conversation", work_item: "Waiting for this work item", writable_root: "Waiting for access to project files", queue: "Waiting in the queue" })[reason] || reason;
}
function conversationSummary(c = {}) {
  if (c.live_wait_reason || c.wait_reason)
    return waitReasonLabel(c.live_wait_reason || c.wait_reason);
  const state = conversationState(c);
  if (state === "needs-you") return "Waiting for your approval";
  if (state === "running")
    return c.live_activity || c.activity || "Run in progress";
  if (state === "queued") return "Waiting in the queue";
  return ({ failed: "Failed", cancelled: "Cancelled", interrupted: "Interrupted" })[c.state] || c.summary || "Completed";
}
// QA-R1-2: "Worked for" counts the time the run was active; time in the queue is shown apart.
function runTiming(result = {}) {
  const total = Number(result.total_seconds),
    queued = Number(result.queue_seconds),
    waited = Number.isFinite(queued) && queued >= 1 && queued < total ? queued : 0,
    seconds = (value) => value.toFixed(1) + " s";
  return {
    worked: Number.isFinite(total) && total - waited > 0 ? seconds(total - waited) : "",
    waited: waited ? seconds(waited) : "",
  };
}
function renderConversationHeader(c = null) {
  const state = c ? conversationState(c) : "draft";
  const states = { "needs-you": "Awaiting approval", running: "Running", queued: "Queued", done: "Completed", draft: "Draft" };
  $("conversation-state-pill").textContent = ({ failed: "Failed", cancelled: "Cancelled", interrupted: "Interrupted" })[c?.state] || states[state];
  $("conversation-state-pill").dataset.state = state;
  $("header-execution-mode").textContent = executionMode === "scoped" ? "Isolated conversation" : "Native conversation";
  $("header-access").textContent = accessLabel();
}
// WP8 (D-032): one meter per provider in a fixed order. A reading shows a bar, DeepSeek shows its
// prepaid balance and a provider without a reading shows "n/a" with the reason.
const QUOTA_RAIL_ORDER = ["codex", "claude", "gemini", "deepseek", "local"];
const quotaReasonTexts = {
  quota_not_read: "not read yet",
  quota_stale: "last reading is older than 5 minutes",
  usage_unavailable: "could not be read",
  quota_not_reported: "provider reports no quota",
  local_no_quota: "local models have no quota",
  balance_not_read: "balance not read yet",
  owner_only: "visible to the owner only",
};
const QUOTA_PRIME_REASONS = new Set(["quota_not_read", "quota_stale", "balance_not_read", "usage_unavailable"]);
const QUOTA_PRIME_MS = 300 * 1000;
// The activity feed is passive: this asks the server to read a missing quota, at most once per
// 300 s per backend and only for a visible tab, then asks the run console for fresh activity.
function primeProviderQuota(backend) {
  if (!quotaUrls[backend] || document.visibilityState !== "visible") return;
  if (Date.now() - (quotaPrimedAt.get(backend) ?? -Infinity) < QUOTA_PRIME_MS) return;
  quotaPrimedAt.set(backend, Date.now());
  json(quotaUrls[backend])
    .then(() => document.dispatchEvent(new CustomEvent("harness:quota-primed")))
    .catch((error) => noteQuotaOwnerOnly(backend, error));
}
// A 403 quota_owner_only (ownership changed between polls) turns the meter into the owner-only n/a
// for the rest of the session, which also stops priming: owner_only is not a priming reason.
function noteQuotaOwnerOnly(backend, error) {
  if (error?.code !== "quota_owner_only" || quotaOwnerOnly.has(backend)) return;
  quotaOwnerOnly.add(backend);
  document.dispatchEvent(new CustomEvent("harness:quota-primed"));
}
function providerQuotaReading(item) {
  const q = item.quota;
  const windows = q ? quotaWindows(q, item.backend) : [];
  if (windows.length) return { backend: item.backend, state: "ok", remaining: Math.min(...windows.map((window) => window.remaining)) };
  const amount = Number(q?.balance?.amount);
  if (q?.available && q.kind === "balance" && Number.isFinite(amount))
    return { backend: item.backend, state: "balance", amount, currency: q.balance.currency };
  return { backend: item.backend, state: "na", reason: quotaOwnerOnly.has(item.backend) ? "owner_only" : q?.reason || "quota_not_read" };
}
// One meter per backend: a reading beats none, and the lowest remaining quota wins.
function preferredReading(previous, next) {
  if (!previous) return next;
  if (previous.state !== "ok" || next.state !== "ok") return previous.state === "na" ? next : previous;
  return next.remaining < previous.remaining ? next : previous;
}
function formatBalance(reading, compact) {
  const { amount, currency } = reading;
  try {
    return new Intl.NumberFormat(undefined, compact
      ? { style: "currency", currency, notation: "compact", maximumFractionDigits: 0 }
      : { style: "currency", currency }).format(compact ? Math.floor(amount) : amount);
  } catch {
    return `${compact ? Math.floor(amount) : amount} ${currency || ""}`.trim();
  }
}
function providerQuotaMeter(reading) {
  const { backend, state } = reading;
  const meter = document.createElement("button");
  meter.type = "button";
  meter.className = "provider-quota-meter";
  meter.dataset.provider = backend;
  meter.dataset.state = state;
  meter.dataset.testid = "quota-meter";
  // D-035: the provider's logo labels the meter; the full canonical name (D42) is the accessible one.
  const logo = providerModelIcon(backend);
  const value = document.createElement("b");
  const bar = document.createElement("i");
  let description;
  if (state === "balance") {
    value.textContent = formatBalance(reading, true);
    description = `${providerNames[backend] || backend} balance ${formatBalance(reading, false)}.`;
  } else if (state === "ok") {
    bar.style.setProperty("--quota", reading.remaining + "%");
    value.textContent = Math.round(reading.remaining) + "%";
    description = `${providerNames[backend] || backend} quota, ${Math.round(reading.remaining)}% remaining.`;
  } else {
    bar.style.setProperty("--quota", "0%");
    value.textContent = "n/a";
    description = `${providerNames[backend] || backend} quota not available: ${quotaReasonTexts[reading.reason] || "not available"}.`;
    railQuotaSummaries.set(backend, description);
  }
  meter.setAttribute("aria-label", description + " Open details.");
  meter.title = meter.getAttribute("aria-label");
  meter.append(...(state === "balance" ? [logo, value] : [logo, bar, value]));
  return meter;
}
window.updateProviderQuotas = function updateProviderQuotas(items = []) {
  const container = $("provider-quotas");
  const perProvider = new Map();
  for (const item of items) {
    const reading = providerQuotaReading(item);
    perProvider.set(item.backend, preferredReading(perProvider.get(item.backend), reading));
  }
  const readings = [...perProvider.values()].sort((a, b) => {
    const rank = (reading) => (QUOTA_RAIL_ORDER.includes(reading.backend) ? QUOTA_RAIL_ORDER.indexOf(reading.backend) : QUOTA_RAIL_ORDER.length);
    return rank(a) - rank(b);
  });
  railQuotaSummaries.clear();
  const meters = readings.map(providerQuotaMeter);
  for (const reading of readings)
    if (reading.state === "na" && QUOTA_PRIME_REASONS.has(reading.reason)) primeProviderQuota(reading.backend);
  container.replaceChildren(...meters);
  container.hidden = !meters.length;
};
// D35: the needs-you count leads the window title, and an unfocused window gets an OS
// notification when a run needs the user, fails or finishes (the desktop app allows it for its own origins).
const WINDOW_TITLE = document.title;
let conversationWindowTitle = WINDOW_TITLE, windowAttentionCount = 0;
function updateWindowTitle() {
  document.title = (windowAttentionCount ? `(${windowAttentionCount}) ` : "") + conversationWindowTitle;
}
const RECENT_JOB_SECONDS = 600;
const seenJobStates = new Map();
const seenRequests = new Set();
let alertsPrimed = false;
function notifyUser(text) {
  if (document.hasFocus() || typeof Notification === "undefined" || Notification.permission !== "granted") return;
  try {
    new Notification("KeepHarness", { body: text });
  } catch {}
}
function jobAlert(job, before) {
  const known = before !== undefined && before !== job.state;
  const recent = before === undefined && Date.now() / 1000 - (job.created || 0) < RECENT_JOB_SECONDS;
  if (!known && !recent) return "";
  const prefix = { failed: "Failed: ", completed: "Finished: " }[job.state];
  return prefix ? prefix + (job.title || "a run") : "";
}
function notifyAttention(data = {}) {
  const needs = data.needs_you || [];
  windowAttentionCount = needs.length;
  updateWindowTitle();
  const alerts = [];
  for (const item of needs) {
    const id = item.gate_id || item.approval_id;
    if (seenRequests.has(id)) continue;
    seenRequests.add(id);
    alerts.push("Needs you: " + (item.title || "a run"));
  }
  for (const job of data.jobs || []) {
    const alert = jobAlert(job, seenJobStates.get(job.job_id));
    seenJobStates.set(job.job_id, job.state);
    if (alert) alerts.push(alert);
  }
  if (alertsPrimed) alerts.forEach(notifyUser);
  alertsPrimed = true;
}
// The permission prompt comes with the first message, when the user has a run to wait for.
function askNotificationPermission() {
  if (typeof Notification === "undefined" || Notification.permission !== "default") return;
  Notification.requestPermission().catch(() => {});
}
$("send").addEventListener("click", askNotificationPermission);
$("prompt").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) askNotificationPermission();
});
window.applyActivitySnapshot = function applyActivitySnapshot(data = {}) {
  notifyAttention(data);
  observedActivityJobs = Array.isArray(data.jobs) ? [...data.jobs] : [];
  renderWorkspaceTasks(observedActivityJobs);
  const pending = new Set(
    (data.needs_you || []).map((item) => item.conversation_id).filter(Boolean),
  );
  const live = new Map();
  // C-05: a running job outranks the queued follow-ups behind it, whatever the list order.
  for (const item of data.jobs || [])
    if (
      item.conversation_id &&
      (!live.has(item.conversation_id) ||
        (item.state === "running" && live.get(item.conversation_id).state !== "running"))
    )
      live.set(item.conversation_id, item);
  let changed = false;
  for (const item of conversations) {
    const job = live.get(item.id);
    const nextNeeds = pending.has(item.id);
    const nextWait = job?.wait_reason || "";
    const nextActivity = job
      ? [job.state === "running" ? "Run in progress" : "Waiting", job.work_item]
          .filter(Boolean)
          .join(": ")
      : "";
    if (
      !!item.needs_you !== nextNeeds ||
      (item.live_wait_reason || "") !== nextWait ||
      (item.live_activity || "") !== nextActivity
    ) {
      item.needs_you = nextNeeds;
      item.live_wait_reason = nextWait;
      item.live_activity = nextActivity;
      if (job?.state && item.state !== job.state) {
        item.state = job.state;
        // C-08: record the live state so a later completion in the list reads as unread.
        observeConversation(item);
        saveConversationActivity();
      }
      changed = true;
    }
  }
  if (!changed) return;
  renderProjects();
  const current = conversations.find((item) => item.id === conversation);
  if (current) renderConversationHeader(current);
};
// CDX-R2-2: a meter explains its own provider, whichever model is selected.
$('provider-quotas').onclick = (event) => {
  const meter = event.target.closest(".provider-quota-meter");
  if (!meter) return;
  quotaFocus = meter.dataset.provider;
  void quota();
  setQuotaOpen(true);
};
const history = async (timeout = 30000, background = false) => {
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
    document.dispatchEvent(new CustomEvent("harness:history", { detail: { background } }));
    if ($("conversation-search-dialog").open) renderConversationSearch();
    if (conversation) {
      const current = conversations.find((item) => item.id === conversation);
      if (current) {
        setConversationTitle(current.title);
        renderConversationHeader(current);
      }
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
};
function conversationRow(c) {
  const row = document.createElement("div");
  row.className = "conversation-row";
  const open = document.createElement("button");
  open.setAttribute("aria-current", c.id === conversation ? "true" : "false");
  open.textContent = c.title || "Conversation";
  open.title = open.textContent;
  open.className = c.id === conversation ? "active" : "";
  open.dataset.conversationId = c.id;
  open.onclick = () => void navigate({ kind: "conversation", id: c.id, legacy: c.legacy });
  const actions = document.createElement("details");
  actions.className = "conversation-actions";
  actions.hidden = !!c.legacy;
  const trigger = document.createElement("summary");
  trigger.textContent = "⋯";
  trigger.setAttribute("aria-label", "Actions for " + open.textContent);
  trigger.title = "Conversation actions";
  const menu = document.createElement("div");
  menu.className = "conversation-actions-menu";
  const rename = menuAction("pencil", "Rename conversation");
  rename.onclick = () => {
    actions.open = false;
    if (busy || loading || uploads) return;
    openRenameConversation(c, trigger);
  };
  const handoff = menuAction("message-plus", "Continue in another app…");
  handoff.onclick = () => {
    actions.open = false;
    if (busy || loading || uploads) return;
    openContinuation(c, trigger);
  };
  const archive = menuAction("archive", "Archive conversation");
  archive.onclick = () => {
    actions.open = false;
    if (busy || loading || uploads) return;
    void archiveConversation(c, true);
  };
  const remove = menuAction("trash", "Delete permanently");
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
      event.preventDefault();
      event.stopPropagation();
      actions.open = false;
      trigger.focus();
    }
  });
  menu.append(rename, handoff, archive, remove);
  actions.append(trigger, menu);
  const model = c.execution?.model;
  const icon = document.createElement("span");
  icon.className = "conversation-model-icon";
  icon.append(providerModelIcon(c.execution?.backend || c.backend, model || c.model));
  icon.setAttribute("aria-hidden", "true");
  const title = document.createElement("span");
  title.className = "conversation-title";
  title.textContent = c.title || "Conversation";
  title.dir = "auto";
  open.replaceChildren(icon, title);
  const indicator = conversationIndicator(c);
  if (indicator) open.prepend(indicator);
  open.title = [
    title.textContent,
    model ? modelName(model).replace(/ · local$/, "") : "",
  ]
    .filter(Boolean)
    .join("\n");
  const meta = document.createElement("span");
  meta.className = "conversation-row-meta";
  const summary = document.createElement("span");
  summary.className = "conversation-summary";
  summary.textContent = conversationSummary(c);
  summary.title = summary.textContent;
  const age = document.createElement("time");
  age.textContent = conversationAge(c);
  const backend = document.createElement("span");
  backend.className = "backend-chip";
  backend.dataset.backend = c.execution?.backend || c.backend || "";
  const backendId = c.execution?.backend || c.backend || "";
  backend.textContent = providerNames[backendId] || backendId;
  backend.title = backend.textContent;
  const project = document.createElement("span");
  project.className = "conversation-project";
  project.textContent = projectDetails[c.project]?.label || c.project || "No project";
  meta.append(summary, age, backend, project);
  // Runs started by a scheduled task say so, with the task's title.
  if (c.schedule_title) {
    const scheduled = document.createElement("span");
    scheduled.className = "conversation-scheduled";
    scheduled.textContent = "Scheduled";
    scheduled.title = "Scheduled task: " + c.schedule_title;
    meta.prepend(scheduled);
    open.title += "\nScheduled task: " + c.schedule_title;
  }
  open.append(meta);
  if (conversationState(c) === "needs-you") {
    const peek = document.createElement("button");
    peek.type = "button";
    peek.className = "conversation-peek";
    peek.textContent = "Peek";
    peek.onclick = (event) => {
      event.stopPropagation();
      void navigate({ kind: "conversation", id: c.id, legacy: c.legacy });
    };
    row.append(open, peek, actions);
    return row;
  }
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
const HANDOFF_APP_NAMES = { chatgpt: "ChatGPT", claude: "Claude" };
// Codes of the desktop bridge (desktop/main.cjs). Not backend codes, so they stay out of userErrors.
const handoffErrors = {
  handoff_forbidden: () => "KeepHarness can only open other apps from its own window.",
  handoff_invalid: () => "KeepHarness couldn't build a link from this handoff.",
  handoff_app_missing: (app) => app + " isn't installed or isn't set up to open links.",
  handoff_open_failed: (app) => "Couldn't open " + app + ".",
};
function continuationFilename(title, target) {
  const clean = String(title || "")
    .replace(/[\u007f-\u009f\u202a-\u202e\u2066-\u2069]/g, "")
    .replace(/[\\/:*?"<>|\u0000-\u001f]+/g, "-")
    .replace(/-{2,}/g, "-");
  const name = Array.from(clean).slice(0, 80).join("").replace(/^[\s.-]+|[\s.-]+$/g, "");
  return (name || "conversation") + "-continue-in-" + target + ".md";
}
function saveContinuation(text, filename) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/markdown" })),
    link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}
// The handoff text exists only in this closure and the read-only textarea; closing clears both.
function openContinuation(c, trigger) {
  const dialog = $("continuation-dialog"),
    field = $("continuation-text"),
    paths = $("continuation-paths"),
    summary = $("continuation-summary"),
    error = $("continuation-error"),
    copy = $("continuation-copy"),
    save = $("continuation-save"),
    offer = $("continuation-open"),
    openStatus = $("continuation-open-status");
  let target = "chatgpt",
    current = "",
    controller = null;
  const reset = () => {
    current = "";
    field.value = "";
    summary.textContent = "";
    error.textContent = "";
    openStatus.textContent = "";
    offer.hidden = true;
    copy.disabled = save.disabled = true;
  };
  const load = async () => {
    controller?.abort();
    const mine = (controller = new AbortController());
    reset();
    summary.textContent = "Preparing the handoff…";
    try {
      const data = await json(
        "/v1/conversations/" + encodeURIComponent(c.id) + "/continuation?target=" + target + "&include_paths=" + (paths.checked ? 1 : 0),
        { signal: AbortSignal.any([mine.signal, AbortSignal.timeout(30000)]) },
      );
      if (mine !== controller) return;
      current = field.value = data.text;
      const turns = data.turns_included;
      summary.textContent = turns + (turns === 1 ? " turn" : " turns") + " included." + (data.truncated ? " Older turns were left out to fit the size limit." : "");
      copy.disabled = save.disabled = false;
    } catch (e) {
      if (mine !== controller) return;
      summary.textContent = "";
      error.textContent = "Couldn't prepare the handoff: " + e.message;
    }
  };
  // Asked only after the user copied or saved, and only for an app the desktop bridge lists.
  // text and app are what was copied or saved, captured before any await.
  const offerOpen = async (text, app) => {
    const bridge = window.keepharnessDesktop;
    if (!bridge || !text) return;
    let apps = [];
    try {
      apps = (await bridge.handoffApps())?.apps || [];
    } catch {}
    if (text !== current || app !== target || !apps.includes(app)) return;
    const name = HANDOFF_APP_NAMES[app];
    $("continuation-open-question").textContent = "Open " + name + " to continue there? Nothing is sent until you send it yourself.";
    $("continuation-open-yes").textContent = "Open " + name;
    $("continuation-open-yes").disabled = false;
    offer.hidden = false;
  };
  $("continuation-open-yes").onclick = async () => {
    const app = target,
      text = current,
      name = HANDOFF_APP_NAMES[app],
      yes = $("continuation-open-yes");
    if (yes.disabled || !text) return;
    yes.disabled = true;
    const onClipboard = " The handoff is on your clipboard.";
    let message;
    try {
      await writeClipboard(text);
    } catch {
      message = "Couldn't copy the handoff, so " + name + " was not opened.";
    }
    if (!message)
      try {
        const result = await window.keepharnessDesktop.openHandoff(app, text);
        message = result?.opened
          ? result.mode === "full"
            ? "Opened " + name + " with the handoff in a new chat. Review it and send it there."
            : "Opened " + name + ". The handoff is too long for a link, so paste it from your clipboard (Ctrl+V)."
          : (handoffErrors[result?.error] || handoffErrors.handoff_open_failed)(name) + onClipboard;
      } catch {
        message = handoffErrors.handoff_open_failed(name) + onClipboard;
      }
    // Even if the target changed meanwhile, say which app was actually opened.
    offer.hidden = true;
    openStatus.textContent = message;
    copy.focus();
  };
  $("continuation-open-no").onclick = () => {
    offer.hidden = true;
    copy.focus();
  };
  copy.onclick = async () => {
    const text = current,
      app = target;
    if (copy.disabled || !(await copyText(text, copy))) return;
    void offerOpen(text, app);
  };
  save.onclick = () => {
    if (save.disabled) return;
    const text = current,
      app = target;
    saveContinuation(text, continuationFilename(c.title, app));
    void offerOpen(text, app);
  };
  $("continuation-close").onclick = () => dialog.close();
  dialog.onchange = (event) => {
    const input = event.target;
    if (input.name === "continuation-target") target = input.value;
    else if (input !== paths) return;
    void load();
  };
  dialog.onclose = () => {
    controller?.abort();
    controller = null;
    reset();
    if (trigger.isConnected) trigger.focus();
    else $("history").querySelector(".conversation-actions summary")?.focus();
  };
  document.querySelector('input[name="continuation-target"][value="chatgpt"]').checked = true;
  paths.checked = localOwner;
  void load();
  dialog.showModal();
  $("continuation-close").focus();
}
function menuAction(icon, label) {
  const button = document.createElement("button"),
    svg = document.createElementNS("http://www.w3.org/2000/svg", "svg"),
    use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  button.type = "button";
  svg.classList.add("th-icon", "menu-action-icon");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  use.setAttribute("href", "/assets/icons.svg#" + icon);
  svg.append(use);
  button.append(svg, document.createTextNode(label));
  return button;
}
// Archive hides a conversation until Unarchive (decision D31); nothing is erased.
async function archiveConversation(c, archived) {
  try {
    await json("/v1/conversations/" + encodeURIComponent(c.id), {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ archived }),
    });
    if (archived && conversation === c.id) newConversation();
    status(archived ? "Conversation archived. Find it in Settings › Archived chats." : "Conversation restored.");
    await history();
    if (!$("settings-archived").hidden) await loadArchived();
  } catch (e) {
    const message = (archived ? "Couldn't archive: " : "Couldn't unarchive: ") + e.message;
    if ($("settings-archived").hidden) status(message);
    else $("archived-error").textContent = message;
  }
}
// Settings › Archived chats: the archived list and this project's use of its storage caps.
function storageLine(data) {
  const bytes = (n) =>
    n >= 1024 ** 3 ? (n / 1024 ** 3).toFixed(1) + " GB" : Math.ceil(n / 1024 ** 2) + " MB";
  return (
    "Storage: " +
    data.runs.used.toLocaleString("en") +
    " of " +
    data.runs.limit.toLocaleString("en") +
    " runs · " +
    bytes(data.bytes.used) +
    " of " +
    bytes(data.bytes.limit) +
    " of files in this project" +
    (data.warning
      ? ". Almost full: archived chats still count, so delete the ones you no longer need permanently."
      : ".")
  );
}
async function loadArchived() {
  const list = $("archived-list"),
    usage = $("storage-usage");
  $("archived-error").textContent = "";
  try {
    const [archived, storage] = await Promise.all([
      json("/v1/conversations?archived=true"),
      json("/v1/storage?" + new URLSearchParams({ project_id: $("project").value })),
    ]);
    usage.textContent = storageLine(storage);
    usage.classList.toggle("storage-warning", !!storage.warning);
    list.replaceChildren(
      ...archived.conversations.map((c) => {
        const row = document.createElement("li"),
          title = document.createElement("span"),
          restore = document.createElement("button"),
          remove = document.createElement("button");
        row.className = "archived-chat";
        title.textContent = c.title || "Conversation";
        title.dir = "auto";
        restore.type = remove.type = "button";
        restore.className = remove.className = "btn";
        restore.textContent = "Unarchive";
        remove.textContent = "Delete permanently";
        restore.setAttribute("aria-label", "Unarchive " + title.textContent);
        remove.setAttribute("aria-label", "Delete permanently " + title.textContent);
        restore.onclick = () => void archiveConversation(c, false);
        remove.onclick = () => openDeleteConversation(c, remove);
        row.append(title, restore, remove);
        return row;
      }),
    );
    $("archived-empty").hidden = archived.conversations.length > 0;
  } catch (e) {
    $("archived-error").textContent = "Couldn't load archived chats: " + e.message;
  }
}
function openDeleteConversation(c, trigger) {
  const dialog = $("delete-conversation-dialog"),
    confirm = $("delete-conversation-confirm"),
    cancel = $("delete-conversation-cancel"),
    error = $("delete-conversation-error");
  $("delete-conversation-name").textContent = c.title || "Conversation";
  error.textContent = "";
  let deleting = false;
  cancel.onclick = () => dialog.close();
  dialog.onclose = () => {
    if (trigger.isConnected) trigger.focus();
  };
  dialog.oncancel = (event) => {
    if (deleting) event.preventDefault();
  };
  confirm.onclick = async () => {
    if (deleting || busy || loading || uploads) return;
    deleting = true;
    // Keep the button enabled (and focused) while the request is in flight:
    // disabling the focused element makes Chromium drop focus to <body>.
    confirm.setAttribute("aria-disabled", "true");
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
      if (!$("settings-archived").hidden) await loadArchived();
    } catch (e) {
      error.textContent = "Couldn't delete: " + e.message;
    } finally {
      deleting = false;
      confirm.removeAttribute("aria-disabled");
      cancel.disabled = false;
      confirm.textContent = "Delete permanently";
    }
  };
  dialog.showModal();
  cancel.focus();
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
  // The sub-bar (project, files, agents) serves every new conversation; only the
  // isolation choice needs a model that states its execution modes.
  $("execution-mode-choice").hidden = !modeContract && started;
  $("execution-mode-choice").classList.toggle("no-modes", !modeContract);
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
  $("header-execution-mode").textContent = isolated
    ? "Isolated conversation"
    : "Native conversation";
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
$("header-execution-mode").onclick = () => {
  if (!conversation && !parent && !$("isolation-toggle").disabled)
    $("isolation-toggle").click();
  else
    status(
      "Conversation mode is fixed after the first message. Start a new conversation to change it.",
    );
};
function newConversation(title = "New Conversation", projectId = $("project").value) {
  if (submitting || cancelling || loading || uploads) {
    status(
      "Wait for the current send to finish before starting another conversation.",
    );
    return;
  }
  saveView();
  const draftProject = $("project").value;
  const changedProject = projectId !== draftProject;
  $("project").value = projectId;
  const carriedDraft = readDraft("conversation-draft:" + (conversation || "new:" + (changedProject ? draftProject : projectId)));
  const newDraft = readDraft("conversation-draft:new:" + projectId);
  imageRefusedModel = "";
  resourceSelections = [];
  invalidResourceTokens.clear();
  setActivePersona(null);
  // F-95: an unsent draft survives every way of starting a new conversation;
  // attachments too, unless they were uploaded to another project.
  const draft = $("prompt").value,
    kept = files.filter((f) => f.project === $("project").value);
  conversationLoad++;
  streamDisconnected = false;
  if (document.activeElement === $("resume-execution")) $("prompt").focus({ preventScroll: true });
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
  renderConversationHeader();
  $("access-mode").value = "ask";
  syncAccessMode();
  files = kept;
  renderFiles();
  $("messages").replaceChildren(welcomeTemplate.cloneNode(true));
  followingStream = true;
  lastRoute = null;
  bindSuggestions();
  modelAvailability();
  $("prompt").value = draft;
  if (newDraft?.draft || newDraft?.files?.length) restoreView(newDraft);
  else if (carriedDraft) restoreView(carriedDraft);
  updateComposer();
  saveView();
  $("context-meter").textContent = "New conversation · independent context";
  setBusy(false);
  status("");
  $("prompt").focus({ preventScroll: true });
  refreshProjectPermissions();
  if (changedProject) { clearResourceItems(); void refreshWorkspaceResources(); void loadAuthorizedProjectRoots(); }
}
function chooseProject(id) {
  if (busy || loading || uploads) return;
  newConversation("New Conversation", id);
  renderProjects();
  history();
  closeSidebar();
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
{
  const saved = prefs.get("project_list_preferences", {});
  if (saved && typeof saved === "object" && !Array.isArray(saved))
    projectPreferences = saved;
}
function setProjectPreference(id, key, value) {
  const next = {
    ...projectPreferences,
    [id]: { ...projectPreferences[id], [key]: value },
  };
  if (!prefs.set("project_list_preferences", next)) {
    status("Couldn't save the project list in this browser. Try again.");
    return;
  }
  projectPreferences = next;
  renderProjects();
  const group = [...$("projects").querySelectorAll("[data-project-id]")].find(
    (el) => el.dataset.projectId === id,
  );
  (
    group?.querySelector(".project-actions > button[aria-expanded]") ||
    $("removed-projects")?.querySelector("summary") ||
    $("add-project")
  ).focus();
  if (key === "hide_icon") return;
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
  const focusedConversation = document.activeElement.dataset.conversationId;
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
        group.open = expandedProjects.get(o.value) ?? true;
        group.ontoggle = () => {
          expandedProjects.set(o.value, group.open);
          rememberExpandedProjects();
        };
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
          showIcon = !!projectIcon && !projectPreferences[o.value]?.hide_icon;
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
          HarnessUI.icon("folder"),
          document.createTextNode("Go to project folder"),
        );
        navigate.onclick = () => {
          menu.hidePopover();
          void navigateProjectFolder(o.value);
        };
        const pin = document.createElement("button");
        pin.type = "button";
        pin.append(
          HarnessUI.icon("star"),
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
          HarnessUI.icon("x"),
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
          HarnessUI.icon("trash"),
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
          HarnessUI.icon("scan"),
          document.createTextNode(
            showIcon ? "Hide project icon" : "Show project icon",
          ),
        );
        iconToggle.title = projectIcon
          ? "Detected icon: " + projectIcon.path
          : "No icon found in the project";
        iconToggle.onclick = () => {
          menu.hidePopover();
          setProjectPreference(o.value, "hide_icon", showIcon);
        };
        const edit = document.createElement("button");
        edit.type = "button";
        edit.title = "Change the name, add folders, and choose the main folder";
        edit.append(
          HarnessUI.icon("pencil"),
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
        trigger.replaceChildren(HarnessUI.icon("settings"));
        menu.append(edit, navigate, pin, iconToggle, remove, deleteFolder);
        actions.append(trigger, menu);
        heading.append(button, actions);
        const children = document.createElement("div");
        children.className = "project-conversations";
        const items = matches.filter(
          (c) => (projectAliases[c.project] || c.project) === o.value,
        );
        const urgent = ["needs-you", "running", "queued", "failed"].find((kind) =>
          items.some((c) => conversationStatusKind(c) === kind),
        );
        if (urgent)
          heading.insertBefore(
            statusDot(urgent, STATUS_DOTS[urgent] + " in this project", "project-indicator"),
            actions,
          );
        children.replaceChildren(...items.map(conversationRow));
        // Codex model: a compose icon on the folder row starts a chat in that project.
        const create = document.createElement("button");
        create.type = "button";
        create.className = "project-new";
        create.append(HarnessUI.icon("message-plus"));
        create.setAttribute(
          "aria-label",
          "New Conversation in " + o.textContent,
        );
        create.title = "New Conversation in " + o.textContent;
        create.onclick = (event) => {
          event.preventDefault();
          if (submitting || cancelling || loading || uploads) {
            status(
              "Wait for the current send to finish before starting another chat.",
            );
            return;
          }
          newConversation("New Conversation in project " + o.textContent, o.value);
          expandedProjects.set(o.value, true);
          renderProjects();
          closeSidebar();
        };
        actions.prepend(create);
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
      HarnessUI.icon("archive"),
      document.createTextNode("Removed projects (" + removed.length + ")"),
    );
    section.append(heading);
    for (const option of removed) {
      const restore = document.createElement("button");
      restore.type = "button";
      restore.append(
        HarnessUI.icon("refresh"),
        document.createTextNode("Restore " + option.textContent),
      );
      restore.onclick = () =>
        setProjectPreference(option.value, "hidden", false);
      section.append(restore);
    }
    $("projects").append(section);
  }
  // Codex model: project conversations live under their project; "Chats" lists the rest.
  // Status is a dot on each row; attention first keeps the old group order.
  const listed = new Set(options.filter((o) => !projectPreferences[o.value]?.hidden).map((o) => o.value));
  const rank = { "needs-you": 0, running: 1, queued: 2, done: 3 };
  const chats = matches
    .filter((c) => !listed.has(projectAliases[c.project] || c.project))
    .map((c, index) => ({ c, index }))
    .sort((a, b) => rank[conversationState(a.c)] - rank[conversationState(b.c)] || a.index - b.index)
    .map(({ c }) => c);
  const section = document.createElement("section");
  section.className = "conversation-state-group";
  section.dataset.state = "chats";
  const heading = document.createElement("h2");
  const count = document.createElement("span");
  count.textContent = String(chats.length);
  heading.append(document.createTextNode("Chats"), count);
  section.append(heading, ...chats.map(conversationRow));
  $("history").replaceChildren(section);
  if (!matches.length) {
    const empty = document.createElement("p");
    empty.className = "empty-history";
    empty.textContent = "Your conversations will appear here.";
    $("history").append(empty);
  }
  if (focusedConversation) {
    [...$("sidebar").querySelectorAll("button[data-conversation-id]")]
      .find(node => node.dataset.conversationId === focusedConversation && node.checkVisibility())
      ?.focus({ preventScroll: true });
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
// Every code block: a header with the language and a Copy button (D34).
answerMarkdown.renderer.rules.fence = (tokens, index, options, env, self) => {
  const token = tokens[index];
  if (token.info.trim().toLowerCase() === "json") {
    try {
      token.content = JSON.stringify(JSON.parse(token.content), null, 2) + "\n";
    } catch {}
  }
  const language = token.info.trim().split(/\s+/)[0] || "text";
  return (
    '<div class="code-block"><div class="code-head"><span class="code-lang">' +
    answerMarkdown.utils.escapeHtml(language) +
    '</span><button type="button" class="copy-code" data-testid="copy-code" aria-label="Copy code">Copy</button></div>' +
    answerFence(tokens, index, options, env, self) +
    "</div>"
  );
};
// A wide table scrolls inside its own region; its words are never split (OP-R2-9).
answerMarkdown.renderer.rules.table_open = () =>
  '<div class="table-scroll" role="region" aria-label="Table" tabindex="0"><table>';
answerMarkdown.renderer.rules.table_close = () => "</table></div>";
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
    // Four backticks: a pretty-printed JSON line can never close the fence.
    body.innerHTML = answerMarkdown.render("````json\n" + JSON.stringify(parsed, null, 2) + "\n````");
    syncResponseMotion(body);
    return source;
  }
  body.innerHTML = answerMarkdown.render(source);
  // WP7: only chat bubbles (bubble() renders before it is appended); page previews stay plain.
  if (body.classList.contains("chat-bubble") && visualMarkersOn()) applyProseMarkers(body);
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
function setAnswer(answer, value, notice = "", code = "") {
  cancelAnimationFrame(answer.body.pendingRender);
  answer.body.pendingRender = 0;
  answer.body.rawAnswer = renderAnswer(answer.body, value);
  if (!notice) return;
  // A separate paragraph, so the received answer stays as it was (F-83).
  const note = document.createElement("p");
  note.className = "run-notice";
  note.textContent = notice;
  if (code) note.title = "Error code: " + code;
  answer.body.append(note);
}
// A skipped catalog hook is not an error: the turn goes on, so the notice stays in the turn
// (above the answer, which re-renders from its raw text) rather than in the transient status.
function showSkippedHookNotice(data) {
  if (!active) return;
  const text = userErrors[data.reason] || "A catalog hook was skipped and the turn went on without it.";
  const shown = active.el.querySelectorAll('[data-testid="catalog-hook-notice"]');
  if ([...shown].some((note) => note.textContent === text)) return;
  const note = document.createElement("p");
  note.className = "run-notice";
  note.dataset.testid = "catalog-hook-notice";
  note.textContent = text;
  active.el.insertBefore(note, active.body);
}
// One action under an answer: a quiet button with an icon and a label (Copy, Ask again).
function answerAction(testid, label, icon) {
  const button = document.createElement("button"),
    text = document.createElement("span");
  button.type = "button";
  button.className = "btn " + testid;
  button.dataset.testid = testid;
  button.setAttribute("aria-label", label);
  text.className = "action-label";
  text.textContent = label;
  button.append(HarnessUI.icon(icon), text);
  return button;
}
const COPIED_LABEL_MS = 1600;
// navigator.clipboard exists only on secure origins; a tailnet http page falls back to execCommand.
async function writeClipboard(text) {
  if (navigator.clipboard?.writeText) return navigator.clipboard.writeText(text);
  const area = document.createElement("textarea");
  area.value = text;
  area.readOnly = true;
  area.className = "visually-hidden";
  // Outside the open modal the textarea is inert: select() and copy would silently do nothing.
  const host = document.querySelector("dialog:modal") || document.body;
  host.append(area);
  area.select();
  const copied = document.execCommand("copy");
  area.remove();
  if (!copied) throw new Error("copy failed");
}
async function copyText(text, label) {
  if (!text) return false;
  try {
    await writeClipboard(text);
  } catch {
    status("Couldn't copy to the clipboard");
    return false;
  }
  label.dataset.label ??= label.textContent; // a second click must not capture "Copied"
  label.textContent = "Copied";
  status("Copied");
  clearTimeout(label.copiedTimer);
  label.copiedTimer = setTimeout(() => (label.textContent = label.dataset.label), COPIED_LABEL_MS);
  return true;
}
$("messages").addEventListener("click", (event) => {
  const button = event.target.closest?.(".copy-code");
  if (button) copyText(button.closest(".code-block")?.querySelector("code")?.textContent, button);
});
function excerptChip(file) {
  const chip = document.createElement("small");
  chip.className = "attachment-excerpt";
  chip.textContent = "excerpt sent";
  chip.title =
    "Only the first part of " +
    file.name +
    " was sent inline. The model can read the rest only if it has file tools.";
  return chip;
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
    if (file.excerpt) card.append(excerptChip(file));
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
function messageResourceChips(message, selections = []) {
  const valid = (selections || []).filter(
    (selection) => typeof selection?.token === "string" && selection.token,
  );
  if (!valid.length) return;
  const chips = document.createElement("div");
  chips.className = "message-resource-chips";
  chips.setAttribute("aria-label", "Selected resources");
  for (const selection of valid) {
    const chip = document.createElement("span");
    chip.className = "message-resource-chip";
    chip.textContent = selection.token;
    chips.append(chip);
  }
  message.body.prepend(chips);
}
// Aggregator: a conversation may change model or provider between turns; a
// quiet divider says so, and that the conversation so far goes along.
let lastRoute = null;
function turnRetriedMarker() {
  const marker = document.createElement("p");
  marker.className = "turn-retried";
  marker.dataset.testid = "turn-retried";
  marker.setAttribute("role", "note");
  marker.append(HarnessUI.icon("refresh"), document.createTextNode("Retried"));
  $("messages").append(marker);
}
function routeDivider(route) {
  const previous = lastRoute;
  lastRoute = route.model ? route : previous;
  if (!previous || !route.model) return;
  if (previous.backend === route.backend && previous.model === route.model) return;
  const crossed = previous.backend !== route.backend,
    divider = document.createElement("p");
  divider.className = "route-divider";
  divider.setAttribute("role", "note");
  divider.append(
    providerModelIcon(route.backend, route.model),
    document.createTextNode(
      crossed
        ? "Switched to " + modelName(route.model) + " · " +
            (providerNames[route.backend] || route.backend) +
            " — the conversation so far goes with it"
        : "Model changed to " + modelName(route.model),
    ),
  );
  $("messages").append(divider);
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
      // Protocol item types and kinds (Codex native, Gemini ACP) are not tool names.
      ...Object.fromEntries(
        "fileChange dynamicToolCall mcpToolCall other think execute read edit delete move search fetch"
          .split(" ")
          .map((type) => [type, "tool"]),
      ),
    }[data.tool] || safeToolId(data.tool)
  );
}
// C-03: a provider tool (an MCP read_file, say) is named by its last segment; anything odd stays generic.
function safeToolId(tool) {
  const name = typeof tool === "string" ? tool.split("__").pop() : "";
  return /^[\w.-]{1,48}$/.test(name) ? name : "";
}
// C-03: like the Codex and Claude CLIs, a shell step shows its command and a file step its path.
// WP7: one table of step categories; the icon and verb of a run step come from here.
const TOOL_MARKERS = {
  read: { icon: "file-text", verb: "Reading", tools: "read read_file" },
  edit: { icon: "pencil", verb: "Editing", tools: "edit write multiedit notebookedit filechange delete move" },
  run: { icon: "terminal-2", verb: "Running", tools: "bash commandexecution exec_command execute" },
  search: { icon: "search", verb: "Searching", tools: "glob grep search search_files" },
  list: { icon: "folder", verb: "Listing", tools: "list_directory list_dir" },
  web: { icon: "world", verb: "Browsing", tools: "websearch webfetch web_search web_fetch fetch" },
  skill: { icon: "cube", verb: "Using skill", tools: "skill" },
  agent: { icon: "robot", verb: "Delegating to", tools: "task agent" },
  plan: { icon: "list-check", verb: "Updating", tools: "todowrite update_plan" },
  mcp: { icon: "plug", verb: "Running", tools: "" },
};
const PAST_TENSE = {
  Running: "Ran", Reading: "Read", Editing: "Edited", Listing: "Listed", Searching: "Searched",
  Browsing: "Browsed", Updating: "Updated", Delegating: "Delegated",
};
const STEP_VERB = new RegExp("^(" + Object.keys(PAST_TENSE).join("|") + ")");
const MARKER_NAME = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$/;
// Skill and agent names are display-only: a name that does not match is never shown.
const markerName = (value) => (typeof value === "string" && MARKER_NAME.test(value) ? value : "");
function toolCategory(data) {
  if (markerName(data.skill)) return "skill";
  if (markerName(data.agent)) return "agent";
  const name = String(data.tool || "").split("__").pop().toLowerCase();
  const key = name && Object.keys(TOOL_MARKERS).find((k) => TOOL_MARKERS[k].tools.split(" ").includes(name));
  if (key) return key;
  return String(data.tool || "").startsWith("mcp__") ? "mcp" : "";
}
function targetTitle(data) {
  const key = toolCategory(data),
    entry = TOOL_MARKERS[key],
    subject = key === "skill" || key === "agent"
      ? markerName(data[key])
      : typeof data.target === "string" ? Array.from(data.target.trim()).slice(0, 160).join("") : "";
  return subject && entry?.tools ? entry.verb + " " + subject : "";
}
function activityTitle(e) {
  const data = e.data || {},
    type = e.type,
    tool = eventToolName(data);
  const condition = executionCondition(
    data.condition || data.error,
    data.backend,
  );
  if (condition) return condition.title;
  if (type === "hook_scope") {
    return {
      disabled: "Hooks disabled for this run",
      project: "Using project hooks only",
      global_and_project: "Using global and project hooks",
    }[data.scope] || "Hook scope reported";
  }
  if (type === "resource_fallback")
    return data.scope === "execution"
      ? "Resource fallback applied for this run"
      : "Resource fallback is advisory";
  if (["invocation_started", "invocation_completed"].includes(type)) {
    const invocation = data.invocation || data,
      identity = data.role || invocation.role || invocation.resource_id || invocation.kind || "resource",
      route = [
        data.backend || invocation.backend,
        data.model || invocation.model,
        data.effort || invocation.effort,
      ]
        .filter(Boolean)
        .join(" · ");
    return (
      (type === "invocation_started" ? "Started " : "Completed ") +
      identity +
      (route ? " · " + route : "")
    );
  }
  if (type === "tool_start" && targetTitle(data)) return targetTitle(data);
  if (type === "tool_start")
    return data.command_name && tool
      ? "Running command " + tool
      : {
          Read: "Reading file",
          Glob: "Searching files",
          Grep: "Searching text",
          webSearch: "Searching the web",
          WebSearch: "Searching the web",
        }[data.tool] || "Running " + (tool || "tool");
  if (type === "tool_end")
    return data.status === "failed" ? "Tool failed" : "Tool finished";
  if (type === "answer_delta" && data.parent_tool_use_id)
    return "Specialist is responding";
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
  if (type === "maestro_planning") return "Planning";
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
// WP7: catalog entries by "kind:name" so a skill or agent chip can link to its catalog row.
const workspaceCatalog = new Map();
const visualMarkersOn = () => prefs.get("visual_markers", true) !== false;
// One renderer for a run step: plain text when markers are off or the step has no category.
function renderStepRow(row) {
  const text = row.dataset.stepText || "",
    category = row.dataset.markerCategory,
    entry = TOOL_MARKERS[category];
  if (!entry || !visualMarkersOn()) {
    row.textContent = text;
    row.removeAttribute("aria-label");
    return;
  }
  const resourceId = workspaceCatalog.get(category + ":" + row.dataset.markerName),
    chip = document.createElement(resourceId ? "a" : "span"),
    label = document.createElement("span"),
    icon = HarnessUI.icon(entry.icon);
  chip.className = "step-chip";
  chip.dataset.testid = "step-marker";
  chip.dataset.category = category;
  if (resourceId) {
    chip.href = "#workspace-resources";
    chip.dataset.resourceId = resourceId;
  }
  icon.classList.add("step-icon");
  label.textContent = text;
  chip.append(icon, label);
  row.replaceChildren(chip);
  row.setAttribute("aria-label", text);
}
// The marker may arrive on tool_start or tool_end. A tool_start category comes from the tool
// name only; a named one (skill, agent) is more specific and wins over it.
function setStepRow(row, text, marker = {}) {
  row.dataset.stepText = text;
  if (marker.category && (marker.name || !row.dataset.markerCategory)) row.dataset.markerCategory = marker.category;
  if (marker.name && !row.dataset.markerName) row.dataset.markerName = marker.name;
  renderStepRow(row);
}
// WP7 prose chips: only exact catalog names ("/name", or a bare name as a whole inline code)
// and exact known file paths; text nodes only, never inside a code block, link or chip.
const PROSE_EDGE = /^[([{"'`]+|[.,;:!?)\]}"'`]+$/g;
function proseKnownPaths() {
  const known = new Map();
  for (const entries of fileTree.cache.values())
    for (const entry of entries) if (entry.path) known.set(entry.path, entry.type === "directory" ? "folder" : "file");
  return known;
}
// A path counts in prose only with a "/" or "." ("tests" or "docs" alone are common words);
// a bare name must be a whole inline code element.
function proseMatch(token, bare, paths) {
  const name = token.startsWith("/") ? token.slice(1) : bare ? token : "";
  for (const kind of name ? ["skill", "agent"] : []) {
    const id = workspaceCatalog.get(kind + ":" + name);
    if (id) return { kind, id };
  }
  return paths.has(token) && (bare || /[/.]/.test(token)) ? { kind: paths.get(token) } : null;
}
function proseChip(match, ...content) {
  const chip = document.createElement(match.id ? "a" : "span");
  chip.className = "prose-chip";
  chip.dataset.kind = match.kind;
  if (match.id) {
    chip.href = "#workspace-resources";
    chip.dataset.resourceId = match.id;
  }
  chip.append(...content);
  return chip;
}
function chipTextNode(node, paths) {
  const text = node.nodeValue, parts = [];
  let last = 0;
  for (const word of text.matchAll(/\S+/g)) {
    const token = word[0].replace(PROSE_EDGE, ""),
      match = token && proseMatch(token, false, paths);
    if (!match) continue;
    const start = word.index + word[0].indexOf(token);
    parts.push(text.slice(last, start), proseChip(match, token));
    last = start + token.length;
  }
  if (parts.length) node.replaceWith(...parts, text.slice(last));
}
function applyProseMarkers(el) {
  const paths = proseKnownPaths();
  if (!workspaceCatalog.size && !paths.size) return;
  const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, {
    acceptNode: (node) =>
      node.parentElement.closest("pre, code, a, .prose-chip") ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT,
  });
  const nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  nodes.forEach((node) => chipTextNode(node, paths));
  for (const code of el.querySelectorAll("code")) {
    if (code.closest("pre, a, .prose-chip")) continue;
    const match = proseMatch(code.textContent, true, paths);
    if (match) code.replaceWith(proseChip(match, code.cloneNode(true)));
  }
}
function removeProseMarkers(el) {
  el.querySelectorAll(".prose-chip").forEach((chip) => chip.replaceWith(...chip.childNodes));
  el.normalize();
}
function refreshVisualMarkers() {
  document.querySelectorAll("li[data-step-text]").forEach(renderStepRow);
  // User bubbles carry no rawAnswer, so only assistant answers are touched.
  document.querySelectorAll("#messages .chat-bubble").forEach((body) => {
    if (body.rawAnswer === undefined) return;
    removeProseMarkers(body);
    if (visualMarkersOn()) applyProseMarkers(body);
  });
}
// Opening the panel reloads the resource rows, so a focus request outlives that reload.
let pendingResourceFocus = "";
function focusResourceRow(id) {
  const row = [...document.querySelectorAll("#workspace-resources [data-resource-id]")].find(
    (item) => item.dataset.resourceId === id,
  );
  row?.focus();
}
document.addEventListener("click", (event) => {
  const link = event.target.closest?.("a:is(.step-chip, .prose-chip)[data-resource-id]");
  if (!link) return;
  event.preventDefault();
  const id = link.dataset.resourceId;
  pendingResourceFocus = $("activity-panel").hidden ? id : "";
  setPanelOpen(true);
  setPanelView("activity");
  selectAccordionSection("resources");
  focusResourceRow(id);
});
function stepMarker(data) {
  const category = toolCategory(data);
  return { category, name: category === "skill" || category === "agent" ? markerName(data[category]) : "" };
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
    const text = targetTitle(data) || row.dataset.stepText;
    setStepRow(
      row,
      toolFailed
        ? "Failed: " + text
        : text.replace(STEP_VERB, (verb) => PAST_TENSE[verb]),
      stepMarker(data),
    );
    return;
  }
  const title = activityTitle(e);
  if (!title) return;
  if (title === "Thinking" && list.lastElementChild?.dataset.stepText === title)
    return;
  const row = document.createElement("li");
  setStepRow(row, title, e.type === "tool_start" || e.type === "tool_end" ? stepMarker(data) : {});
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
    document.createTextNode(model ? modelName(model) : "Response"),
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
  const copy = answerAction("copy-answer", "Copy", "copy");
  copy.onclick = () => copyText(a.body.rawAnswer || a.body.textContent, copy.querySelector(".action-label"));
  a.el.append(copy, askAgainButton(a.el));
  window.runConsole?.attachAnswer(a.el, id);
  return { ...a, activity, activitySummary, milestones, meta, chip };
}
// D37: "Ask again" sends the question this answer replied to as a new turn, on the model now selected.
function askAgainButton(answer) {
  const button = answerAction("ask-again", "Ask again", "refresh");
  button.onclick = () => {
    let asked = answer.previousElementSibling;
    while (asked && !asked.matches("article.message.user")) asked = asked.previousElementSibling;
    const question = asked?.textContent;
    if (!question || busy || submitting) return;
    if ($("prompt").value.trim()) return status("Send or clear the draft first, then ask again.");
    $("prompt").value = question.trim();
    updateComposer();
    send();
  };
  return button;
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
    // Only on a change: renderAnswer re-places the marker itself (F-87).
    if (active.body.dataset.motion !== (mode ? "answer" : "")) {
      active.body.dataset.motion = mode ? "answer" : "";
      syncResponseMotion(active.body);
    }
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
// C-06: follow the stream while the user is at the bottom. The state changes only on a scroll event
// (user or our own jump), never from the distance after a render, which a fast stream outgrows.
let followingStream = true,
  ownScroll = false;
$("messages").addEventListener(
  "scroll",
  () => {
    if (ownScroll) return; // our own jump: content that grew after it must not unpin the view
    const box = $("messages");
    followingStream = box.scrollHeight - box.scrollTop - box.clientHeight < 48;
  },
  { passive: true },
);
function scroll() {
  // "instant": #messages is scroll-behavior smooth, and an animated jump lags a fast stream.
  if (followingStream) {
    ownScroll = true;
    requestAnimationFrame(() => (ownScroll = false)); // scroll events fire before the next frame callbacks
    $("messages").scrollTo({ top: $("messages").scrollHeight, behavior: "instant" });
  }
  updateLatest();
}
// Read-only: workflow steps, or a plan recorded by an earlier version; nothing here can be approved.
function renderPlanOutcome(card, runState = card.dataset.runState) {
  if (!card) return;
  if (runState) card.dataset.runState = runState;
  const choice = card.dataset.choice;
  const terminal = { completed: "Completed", failed: "Failed", cancelled: "Cancelled", interrupted: "Interrupted" }[runState];
  let label, note;
  if (choice === "deny") { label = "Discarded"; note = "Plan discarded."; }
  else if (choice === "approve" && !card.id && !terminal) {
    label = "Running"; note = "The workflow is running these steps.";
  } else if (choice === "approve") {
    label = "Approved · " + (terminal || "recorded");
    note = terminal ? "The approved run is " + terminal.toLowerCase() + "." : "This plan was approved earlier.";
  } else { label = "Not active"; note = "This plan can no longer be approved."; }
  card.querySelector(".state-pill").replaceChildren(HarnessUI.icon(choice === "approve" ? "check" : "shield"), document.createTextNode(label));
  card.querySelector('[role="status"]').textContent = note;
}
const workflowResumeKeys = new Map();
function showWorkflowRecovery(run) {
  const target = active?.el;
  if (!target || !["failed", "cancelled", "interrupted"].includes(run.state) || target.querySelector(".workflow-recovery")) return;
  if (!run.workflow_checkpoint) return;
  const section = document.createElement("section"), note = document.createElement("p"), resume = document.createElement("button");
  section.className = "workflow-recovery";
  note.textContent = run.workflow_completed_steps === 0
    ? "No completed steps can be reused. Retry starts at the first step of the saved plan."
    : (Number.isInteger(run.workflow_completed_steps) ? run.workflow_completed_steps + " completed step(s) can be reused. " : "Resume from the last valid checkpoint. ") + "Completed steps are reused when their inputs and workflow are unchanged; remaining steps run again.";
  note.setAttribute("role", "status");
  resume.type = "button";
  resume.className = "btn";
  resume.textContent = "Resume workflow";
  let resumedConversation;
  resume.onclick = async () => {
    resume.disabled = true;
    try {
      const storageKey = "workflow-resume:" + run.id;
      let key = workflowResumeKeys.get(run.id);
      try { key ||= sessionStorage.getItem(storageKey); } catch {}
      key ||= crypto.randomUUID?.() || Array.from(crypto.getRandomValues(new Uint8Array(16)), n => n.toString(16).padStart(2, "0")).join("");
      workflowResumeKeys.set(run.id, key);
      try { sessionStorage.setItem(storageKey, key); } catch {}
      if (!resumedConversation) {
        const child = await json("/v1/jobs/" + encodeURIComponent(run.id) + "/resume", {
          method: "POST", headers: { "Content-Type": "application/json", "Idempotency-Key": key }, body: "{}",
        });
        resumedConversation = child.conversation_id || child.job_id;
        resume.textContent = "Open resumed run";
      }
      await load(resumedConversation);
      if (conversation !== resumedConversation) {
        note.textContent = "The workflow was resumed. Couldn't open its conversation; use Open resumed run to try again.";
        resume.disabled = false;
      }
      void history();
    } catch (error) {
      note.textContent = error.code === "workflow_effect_outcome_unknown"
        ? "Resolve the uncertain publication outcome before resuming this workflow."
        : (resumedConversation ? "The workflow was resumed. Couldn't open its conversation: " : "Couldn't resume the workflow: ") + error.message;
      resume.disabled = false;
    }
  };
  section.append(note, resume);
  target.append(section);
}
// D-031: Retry only on the latest failed or interrupted turn. The server owns the exclusions
// (cancelled, workflow, schedule, superseded); the UI hides what it can already tell.
function showTurnRetry(run) {
  const target = active?.el, req = run.request || {};
  if (!target || !["failed", "interrupted"].includes(run.state) || target.querySelector(".turn-retry-actions")) return;
  if (run.workflow_checkpoint || req.schedule_id || req.backend === "maestro" || req.invocations?.some?.((item) => item.kind === "workflow")) return;
  const section = document.createElement("p"), note = document.createElement("span"), button = document.createElement("button");
  section.className = "turn-retry-actions";
  note.setAttribute("role", "status");
  button.type = "button";
  button.className = "btn";
  const unreadable = (run.attachments || []).some((file) => file.preview_url) &&
    models.find((m) => m.id === (req.backend === "qwen" ? "qwen-local" : req.model))?.capabilities?.images === false;
  if (unreadable) {
    note.textContent = modelName(req.model) + " can't read the image of this turn, so Retry would drop it again.";
    button.dataset.testid = "turn-choose-model";
    button.textContent = "Choose another model";
    button.onclick = () => $("model-trigger").click();
  } else {
    button.dataset.testid = "turn-retry";
    button.append(HarnessUI.icon("refresh"), document.createTextNode("Retry"));
    button.onclick = async () => {
      if (button.disabled) return;
      const turnConversation = conversation;
      button.disabled = true;
      button.setAttribute("aria-busy", "true");
      let failure = "";
      try {
        await post("/v1/jobs/" + encodeURIComponent(run.id) + "/retry", {});
        if (req.prompt && $("prompt").value === req.prompt) {
          $("prompt").value = "";
          resourceSelections = [];
          syncResourceSelections();
          renderResourceChips();
          saveView();
        }
      } catch (e) {
        failure = userErrors[e.code] || e.message;
      }
      if (conversation !== turnConversation) return;
      await load(turnConversation);
      const fresh = $("messages").querySelector(".turn-retry-actions [role=status]");
      if (failure && fresh) {
        fresh.textContent = failure;
        fresh.tabIndex = -1;
        fresh.focus();
        return;
      }
      if (failure) status(failure);
      $("prompt").focus();
    };
  }
  section.append(note, button);
  target.append(section);
}
function showMaestroPlan(data = {}) {
  if (!active || !Array.isArray(data.steps)) return;
  active.el.querySelector(".maestro-plan-card")?.remove();
  const card = document.createElement("section");
  card.className = "maestro-plan-card";
  if (data.gate_id) card.id = "gate-" + data.gate_id;
  card.dataset.state = data.state || (data.gate_id ? "pending" : "running");
  card.dataset.choice = data.choice || (data.gate_id ? "" : "approve");
  const heading = document.createElement("div");
  heading.className = "maestro-plan-heading";
  const title = document.createElement("strong");
  title.textContent = "Plan";
  const state = document.createElement("span");
  state.className = "state-pill";
  const note = document.createElement("span");
  note.setAttribute("role", "status");
  heading.append(title, state, note);
  const steps = document.createElement("ol");
  for (const step of data.steps) {
    const item = document.createElement("li");
    const role = document.createElement("span");
    role.className = "plan-role";
    role.textContent = step.role || "Agent";
    const model = document.createElement("span");
    model.className = "backend-chip";
    model.dataset.backend = step.backend || "";
    model.textContent = [step.backend, step.model].filter(Boolean).join(" · ");
    model.title = model.textContent;
    const effort = document.createElement("span");
    effort.textContent = step.effort || "";
    const task = document.createElement("p");
    task.textContent = [step.task, step.reason].filter(Boolean).join(" · ");
    item.append(role, model, effort, task);
    steps.append(item);
  }
  card.append(heading, steps);
  renderPlanOutcome(card);
  active.el.insertBefore(card, active.body);
}
function event(e) {
  if (e.id <= last) return;
  last = e.id;
  window.runConsole?.observe(e);
  if (e.type === "session_turn_started") return;
  if (e.type === "invocation_started" && e.data?.invocation?.mode === "conversational")
    setActivePersona({
      name: e.data.role || e.data.invocation.resource_id,
      resource_id: e.data.invocation.resource_id,
      route: activePersona?.resource_id === e.data.invocation.resource_id
        ? activePersona.route
        : lastSentRoute,
    }, true);
  if (e.type === "gate_required") {
    showGate(e.data);
    return;
  }
  if (["gate_resolved", "gate_expired", "gate_invalidated"].includes(e.type)) {
    finishGate(e.data.gate_id, e.type.slice(5), e.data);
    return;
  }
  if (e.type === "approval_required") {
    showApproval(e.data);
    return;
  }
  if (e.type === "approval_expired") {
    expireApproval(e.data.approval_id);
    return;
  }
  if (e.type === "approval_resolved") {
    const box = document.getElementById("approval-" + e.data.approval_id);
    if (box?.contains(document.activeElement)) $("prompt").focus({ preventScroll: true });
    box?.remove();
    status("Approval decision recorded");
    return;
  }
  if (e.type === "maestro_plan") showMaestroPlan(e.data);
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
  if (active) showHeldTurn(active, e.type === "queue_wait" && e.data?.reason === "held_after_stop");
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
  } else if (e.type === "answer_delta" && e.data.parent_tool_use_id) {
    if (active) setActivitySummary(active, "Specialist is responding…");
    status("Specialist is responding…");
    return;
  } else if (e.type === "answer_delta") {
    // F-87: re-render the markdown at most once per frame, not per delta.
    const body = active.body;
    body.rawAnswer = (body.rawAnswer || "") + e.data.text;
    body.pendingRender ||= requestAnimationFrame(() => {
      body.pendingRender = 0;
      renderAnswer(body, body.rawAnswer);
      scroll();
    });
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
  } else if (e.type === "catalog_hook" && e.data?.outcome === "skipped") {
    showSkippedHookNotice(e.data);
    return;
  } else {
    status(e.type === "queue_wait" ? waitReasonLabel(e.data?.reason || "queue") : labels[e.type] || e.type);
  }
  pendingGateStatus();
  // Deltas scroll after their batched render; reading layout here per delta
  // would force a reflow for each one (F-87).
  if (e.type !== "answer_delta") scroll();
}
// F-85: provider conditions from the worker ({condition, backend}), plus the
// legacy Claude codes still stored in older history.
const conditionCopy = {
  backend_unavailable: "unavailable",
  provider_unavailable: "unavailable",
  provider_authentication_required: "authentication",
  provider_authentication_failed: "authentication",
  provider_quota_exhausted: "quota",
  provider_rate_limit: "rate",
  claude_authentication_required: "authentication",
  claude_authentication_failed: "authentication",
  claude_quota_exhausted: "quota",
  claude_rate_limit: "quota",
};
function executionCondition(code, backend, detail) {
  const kind = conditionCopy[code];
  if (!kind) return null;
  if (String(code).startsWith("claude_")) backend = "claude";
  backend ||= selected()?.backend;
  const name =
      { claude: "Claude", gemini: "Gemini" }[backend] ||
      providerNames[backend] ||
      "provider",
    panel = providerNames[backend] || name,
    reason = providerMessage(detail);
  // DeepSeek runs on a pasted API key and a prepaid balance: no sign-in, nothing renews.
  const deepseek = backend === "deepseek" && {
    authentication: {
      title: "Replace the DeepSeek API key",
      message:
        "DeepSeek rejected the API key. In the admin panel, paste a valid DeepSeek API key and send your message again.",
    },
    quota: {
      title: "Top up your DeepSeek balance",
      message:
        "Your DeepSeek balance is used up. Top it up in your DeepSeek account, or select a different provider to continue this conversation.",
    },
  }[kind];
  // D47: the admin has no Gemini card, so there is nothing to open or renew.
  const gemini = backend === "gemini" && ["unavailable", "authentication"].includes(kind) && {
    title: "Gemini unavailable",
    message: "Gemini is not available in this KeepHarness release. Select another provider to continue this conversation.",
  };
  const copy = deepseek || gemini || {
    unavailable: {title: name + " unavailable", message: "Open the admin panel to check " + name + ", or select another provider."},
    authentication: {
      title: "Renew " + name + " access",
      message:
        "Your " +
        name +
        " access needs to be renewed. In the admin panel, find " +
        panel +
        " and click “Renew access”. Complete the sign-in in the browser and send your message again.",
    },
    quota: {
      title: "Wait for quota renewal",
      message:
        "Your " +
        name +
        " quota is temporarily exhausted. Wait for it to renew or select a different provider to continue this conversation.",
    },
    rate: {
      title: "Wait a moment",
      message:
        name +
        " is limiting requests. Wait a moment, or select a different provider to continue this conversation.",
    },
  }[kind];
  if (reason) copy.message += " " + name + " says: " + reason;
  return copy;
}
// The prose after an adapter's "<code>: " prefix, never the code itself.
function providerMessage(detail) {
  const text = String(detail || "").trim(),
    match = /^[a-z0-9_]+: (.+)$/s.exec(text);
  return match ? match[1] : /^[a-z0-9_]+$/.test(text) ? "" : text;
}
function executionError(error, detail) {
  const [code] = String(error).split(": ", 1),
    condition = executionCondition(code);
  if (condition) return condition.message;
  if (
    /context_limit_exceeded|exceed_context_size|exceeds the available context|maximum context length|source_context_limit|conversation_context_limit|context_window_exceeded/i.test(
      String(error),
    )
  )
    return "Couldn't prepare or process the context for this attempt. The history and files were preserved. You can continue this conversation; if the limit persists, use a model with reading tools or reduce the content sent at once.";
  const reason = providerMessage(detail || error);
  return (
    (userErrors[code] ||
      attachmentError(code) ||
      "The run did not finish. Check the activity and try again.") +
    (reason ? " Provider message: " + reason : "")
  );
}
async function result(
  expectedJob = job,
  expectedController = controller,
  snapshot = null,
) {
  const r = snapshot || (await json("/v1/jobs/" + expectedJob));
  if (job !== expectedJob || controller !== expectedController) return r;
  restoreGates(r.gates);
  const planCard = active?.el.querySelector(".maestro-plan-card");
  if (planCard) renderPlanOutcome(planCard, r.state);
  showWorkflowRecovery(r);
  showTurnRetry(r);
  const terminal = {
    completed: "Completed",
    failed: "Failed run",
    cancelled: "Run cancelled",
    interrupted: "Run interrupted",
  };
  const condition = executionCondition(
    r.result?.condition || r.result?.error,
    r.result?.backend || r.request?.backend,
    r.result?.error_detail,
  );
  if (condition) composerCondition = { ...condition, backend: r.result?.backend || r.request?.backend || selected()?.backend };
  updateComposer();
  updateMotion(r.state);
  $("activity-state").textContent = condition
    ? "ℹ " + condition.title
    : (activityIcons[r.state] || "•") + " " + (labels[r.state] || r.state);
  if (active) {
    active.chip.textContent = $("activity-state").textContent;
    const data = r.result || {};
    if (data.context_usage) paintContext(data.context_usage, data.metrics);
    else if (data.metrics) paintLocalUsage(data.metrics);
    else if (["cancelled", "failed", "interrupted"].includes(r.state))
      // F-57: partial usage events of an unfinished run are not the run's totals.
      $("context-meter").textContent =
        (r.state === "cancelled"
          ? "Last run cancelled"
          : "Last run did not finish") + " · token usage not reported";
    else if (r.state === "completed") $("context-meter").textContent = "Last run completed · token usage not reported";
    if (data.answer !== undefined) setAnswer(active, data.answer);
    // On reload, nothing has streamed into this fresh bubble yet: fall back
    // to the server's persisted partial_answer (WP-F contract) so a failed,
    // interrupted or cancelled run still shows what was received.
    else if (!active.body.rawAnswer && data.partial_answer !== undefined)
      setAnswer(active, data.partial_answer);
    // F-83/F-112: keep what was already received and append the notice.
    const notice = condition
      ? ""
      : data.error
        ? executionError(data.error, data.error_detail)
        : "";
    if (notice) setAnswer(active, active.body.rawAnswer, notice, data.error);
    if (r.state === "cancelled" && !active.body.rawAnswer && !notice)
      setAnswer(active, "Run cancelled.");
    // The notice asks to send again: put the prompt back in an empty composer.
    if (
      (notice || condition) &&
      !snapshot &&
      r.request?.prompt &&
      !$("prompt").value &&
      !files.length
    ) {
      $("prompt").value = r.request.prompt;
      resourceSelections = (r.request.resource_selections || []).map(ref => ({ ...ref }));
      syncResourceSelections();
      renderResourceChips();
      updateComposer();
      saveView();
    }
    const modelId = data.model || $("model").value,
      model = modelId
        ? modelIcon(modelId) +
          " " +
          modelName(modelId)
        : "",
      { worked: duration, waited } = runTiming(data);
    active.meta.textContent = [model, duration, waited && "waited " + waited].filter(Boolean).join(" · ");
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
            : "Run steps"
          : "Working…",
    );
    if (data.incomplete)
      status("Incomplete response. Narrow the scope and try again.");
    else status(condition?.title || terminal[r.state] || r.state);
    if (!terminal[r.state]) pendingGateStatus();
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
// D16: a follow-up queued behind a stopped run waits for the user's choice.
function showHeldTurn(response, held) {
  response.heldActions?.remove();
  response.heldActions = null;
  if (!held) return;
  const actions = document.createElement("p"),
    run = document.createElement("button"),
    discard = document.createElement("button"),
    turn = job;
  actions.className = "held-turn-actions";
  run.type = discard.type = "button";
  run.className = "btn btn-primary";
  discard.className = "btn";
  run.textContent = "Run queued message";
  discard.textContent = "Discard";
  const choose = async (path, done) => {
    run.disabled = discard.disabled = true;
    try {
      await post("/v1/jobs/" + encodeURIComponent(turn) + path, {});
      showHeldTurn(response, false);
      status(done);
    } catch (e) {
      run.disabled = discard.disabled = false;
      status("Couldn't update the queued message: " + e.message);
    }
  };
  run.onclick = () => choose("/run-queued", "Queued message released.");
  discard.onclick = () => choose("/cancel", "Queued message discarded.");
  actions.append(run, discard);
  response.heldActions = actions;
  response.el.insertBefore(actions, response.body);
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
  if (document.activeElement === $("resume-execution")) $("prompt").focus({ preventScroll: true });
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
    if (controller !== current) return;
    $("activity-state").textContent = "● Tracking execution";
    status("Tracking execution…");
    pendingGateStatus();
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
async function load(id, legacy = false, restoredView = null, scrollTop) {
  if (submitting || cancelling || uploads) return;
  if (!loading && !restoredView) saveView();
  let savedDraft = restoredView;
  if (!savedDraft) {
    savedDraft = readDraft("conversation-draft:" + id);
  }
  const navigationFocus = document.activeElement;
  const request = ++conversationLoad,
    priorDraft = $("prompt").value,
    priorTracking = (!!controller && busy) || streamDisconnected;
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
    streamDisconnected = false;
    $("resume-execution").hidden = true;
    setActivePersona(null);
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
    renderConversationHeader(conversations.find((item) => item.id === id));
    files = [];
    imageRefusedModel = "";
    resourceSelections = [];
    invalidResourceTokens = new Set();
    renderFiles();
    $("messages").replaceChildren();
    followingStream = true;
    lastRoute = null;
    $("prompt").value = "";
    for (const r of data.turns) {
      if (r.request?.release_persona) setActivePersona(null);
      else {
        const persona = r.request?.invocations;
        if (
          Array.isArray(persona) &&
          persona.length === 1 &&
          persona[0]?.mode === "conversational"
        ) {
          const token = r.request?.resource_selections?.[0]?.token;
          setActivePersona({
            name:
              (typeof token === "string" && token.replace(/^[@/]+/, "")) ||
              persona[0].resource_id,
            resource_id: persona[0].resource_id,
            route: { backend: r.request?.backend, model: r.request?.model, effort: r.request?.effort },
          });
        }
      }
      $("project").value = r.project;
      syncActiveProjectBadge();
      const model =
        r.request?.backend === "qwen" ? "qwen-local" : r.request?.model;
      if (models.some((m) => m.id === model)) {
        $("model").value = model;
        updateEfforts();
        $("effort").value = r.request?.effort || $("effort").value;
      }
      routeDivider({ backend: r.request?.backend, model });
      if (r.request?.retry_of) turnRetriedMarker();
      else {
        const userMessage = bubble("user", r.request?.prompt || "Previous run");
        messageAttachments(userMessage, r.attachments);
        messageResourceChips(userMessage, r.request?.resource_selections);
      }
      active = assistant(r.id, model, !["queued", "running"].includes(r.state));
      restoreGates(r.gates);
      const planCard = active.el.querySelector(".maestro-plan-card");
      if (planCard) renderPlanOutcome(planCard, r.state);
      showWorkflowRecovery(r);
      job = r.id;
      if (["queued", "running"].includes(r.state))
        queuedTurns.push({ id: r.id, response: active });
      if (
        r !== data.turns[data.turns.length - 1] &&
        !["queued", "running"].includes(r.state)
      ) {
        const condition = executionCondition(
            r.result?.condition || r.result?.error,
            r.result?.backend || r.request?.backend,
            r.result?.error_detail,
          ),
          notice =
            condition?.message ||
            (r.result?.error
              ? executionError(r.result.error, r.result.error_detail)
              : "");
        setAnswer(
          active,
          r.result?.answer ??
            r.result?.partial_answer ??
            (notice ? "" : r.state),
          notice,
          r.result?.error,
        );
        active.chip.textContent = condition
          ? "ℹ " + condition.title
          : (activityIcons[r.state] || "•") +
            " " +
            (labels[r.state] || r.state);
        const { worked: duration, waited } = runTiming(r.result);
        active.meta.textContent = [r.result?.model || model || "", duration, waited && "waited " + waited]
          .filter(Boolean)
          .join(" · ");
        setActivitySummary(
          active,
          condition
            ? condition.title
            : duration
              ? "Worked for " + duration
              : "Run steps",
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
    void refreshWorkspaceResources();
    void loadAuthorizedProjectRoots();
    await refreshProjectPermissions();
    if (request !== conversationLoad) return;
    const restoreNavigationFocus = !!navigationFocus.dataset.conversationId &&
      document.activeElement.dataset.conversationId === navigationFocus.dataset.conversationId;
    expandedProjects.set($("project").value, true);
    renderProjects();
    // One scrolling sidebar: reveal the most specific row (the project copy when expanded).
    const currentRow = ".conversation-row > button[aria-current=\"true\"]";
    ($("projects").querySelector(currentRow) || $("history").querySelector(currentRow))
      ?.scrollIntoView({ block: "nearest" });
    last = 0;
    closeSidebar();
    loading = false;
    if (savedDraft) restoreView(savedDraft);
    else updateComposer();
    if (restoreNavigationFocus) {
      const target = innerWidth <= 620 ? $("messages") :
        $("sidebar").querySelector('.conversation-row > button[aria-current="true"]');
      target?.focus({ preventScroll: true });
    }
    // Back/forward: put the saved position back now; watch() below can run for the whole stream.
    if (scrollTop !== undefined) restoreScroll(scrollTop);
    saveView();
    const latest = data.turns.find((turn) => turn.id === job);
    if (
      ["completed", "failed", "cancelled", "interrupted"].includes(latest.state)
    )
    {
      await result(job, controller, latest);
      // "The end" again once the final answer has rendered: it was not in the list above.
      if (scrollTop < 0 && conversation === id) restoreScroll(scrollTop);
    } else if (!restoredView) await watch();
    return ["queued", "running"].includes(latest.state);
  } catch (e) {
    if (request !== conversationLoad) return;
    loading = false;
    if (priorTracking && job) {
      streamDisconnected = true;
      $("resume-execution").hidden = false;
    }
    setBusy(false);
    $("prompt").value = priorDraft;
    updateComposer();
    closeSidebar();
    // F-80: a conversation the server confirms is gone leaves the list.
    if (e.code === "conversation_not_found") {
      conversations = conversations.filter((c) => c.id !== id);
      renderProjects();
    }
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
// The Files chip offers the last uploads again; this tab's memory is enough for that.
const RECENT_UPLOADS_KEY = "keepharness-recent-uploads";
function recentUploads() {
  try {
    const saved = JSON.parse(sessionStorage.getItem(RECENT_UPLOADS_KEY) || "[]");
    return Array.isArray(saved) ? saved : [];
  } catch {
    return [];
  }
}
function rememberUpload(entry) {
  try {
    sessionStorage.setItem(
      RECENT_UPLOADS_KEY,
      JSON.stringify([entry, ...recentUploads().filter((item) => item.id !== entry.id)].slice(0, 8)),
    );
  } catch {}
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
    const picked = Array.from(list);
    for (const [index, f] of picked.entries()) {
      if (files.length >= MAX_ATTACHMENTS) {
        // F-71: say how many of the selection were dropped, and which ones.
        const dropped = picked.slice(index).map((file) => file.name);
        status(
          "Limit of 20 attachments reached. " +
            (dropped.length === picked.length
              ? "Remove one before adding another."
              : dropped.length +
                " of the " +
                picked.length +
                " selected files " +
                (dropped.length === 1 ? "was" : "were") +
                " not attached."),
        );
        attachmentNotice(dropped, "file_limit");
        break;
      }
      const video = /\.mp4$/i.test(f.name) || f.type === "video/mp4",
        audio = /\.(wav|mp3|m4a|ogg|flac|webm|aac|opus)$/i.test(f.name);
      if (f.size > MAX_ATTACHMENT_BYTES) {
        // F-72: a persistent notice, since the next upload overwrites #status.
        attachmentNotice(f.name, "file_too_large");
        status(f.name + ": The per-file limit is 100 MiB.");
        continue;
      }
      // F-56: the same file twice would be sent twice.
      if (
        files.some(
          (a) =>
            a.name === f.name &&
            a.size === f.size &&
            a.modified === f.lastModified,
        )
      ) {
        status(f.name + " is already attached.");
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
        const entry = {
          id: r.file_id,
          name: f.name,
          preview_url: r.preview_url,
          excerpt: r.excerpt,
          project: $("project").value,
          size: f.size,
          modified: f.lastModified,
        };
        files.push(entry);
        rememberUpload(entry);
        renderFiles();
        saveView();
        status("File received.");
      } catch (e) {
        if (imageRefusalCodes.has(e.code)) imageRefusedModel = selected()?.id || "";
        attachmentNotice(f.name, e.code);
        status("Couldn't upload: " + (attachmentError(e.code) || e.message));
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
    sendCooldownSeconds() ||
    $("prompt").disabled
  )
    return;
  if (syncImageWarning()) {
    $("image-capability-choose").focus();
    return;
  }
  const following = busy && !!job;
  const draft = $("prompt").value,
    prompt = draft;
  if (!prompt.trim()) return;
  // @@name must be one of the user's agents chosen from the list; //name is still reserved.
  const unfenced = unfencedPrompt(prompt),
    unknownAgent = [...unfenced.matchAll(/(?:^|\s)(@@[\w:-]+)(?=\s|$)/g)].find(
      (match) => !resourceSelections.some((ref) => ref.token === match[1]),
    );
  if (unknownAgent) {
    status("Choose " + unknownAgent[1] + " from the @ list, or create it under Your agents.");
    return;
  }
  if (/^\s*\/\/[A-Za-z_][\w:-]*(?=\s|$)/.test(unfenced)) {
    status("KeepHarness skills and commands are not available yet.");
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
  let m = selected();
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
  const submissionFocus = document.activeElement;
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
    // An agent keeps its own route: choosing another agent or model ends its
    // conversation on this send; the conversation history still carries over.
    const personaRoute = activePersona?.route;
    if (
      activePersona &&
      (resourceSelections.length ||
        (personaRoute &&
          (personaRoute.backend !== m.backend ||
            personaRoute.model !== m.id ||
            (personaRoute.effort && personaRoute.effort !== data.effort))))
    )
      releasePersonaPending = true;
    if (releasePersonaPending) data.release_persona = true;
    lastSentRoute = { backend: m.backend, model: m.id, effort: data.effort };
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
    if (r.execution_mode) executionMode = r.execution_mode;
    const executor = models.find(model => model.id === r.model && model.backend === r.backend);
    if (executor) {
      m = executor;
      $("model").value = executor.id;
      updateEfforts();
      if (r.effort) $("effort").value = r.effort;
      rememberSelection();
    }
    if (releasePersonaPending) setActivePersona(null);
    clearSubmission();
    $("welcome")?.remove();
    routeDivider({ backend: r.backend || m.backend, model: r.model || m.id });
    const userMessage = bubble("user", prompt);
    messageAttachments(userMessage, files);
    messageResourceChips(userMessage, data.resource_selections);
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
      retireDraft("conversation-draft:new:" + $("project").value);
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
      if ([document.body, $("send"), $("prompt")].includes(document.activeElement) && [$("send"), $("prompt")].includes(submissionFocus)) $("prompt").focus({ preventScroll: true });
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
  $("files-selection-actions").hidden = !count;
  $("files-new-chat").textContent =
    count === 1 ? "New chat with this file" : "New chat with these files";
}
function renderProjectFileTree() {
  const entries =
    fileTree.cache.get(fileTree.rootId + "\0" + fileTree.basePath) || [];
  const focus = projectTreeFocus($("files-tree"));
  $("files-tree").replaceChildren();
  const group = document.createElement("ul");
  group.setAttribute("role", "group");
  renderProjectFileEntries(group, entries);
  $("files-tree").append(group);
  restoreProjectTreeFocus($("files-tree"), fileTree, focus);
  renderProjectFileSelection();
}
function projectTreeFocus(container) {
  const active = document.activeElement;
  return container.contains(active) ? {
    path: active.closest('[role="treeitem"]')?.dataset.path,
    toggle: active.classList.contains("file-chevron"),
  } : null;
}
function restoreProjectTreeFocus(container, tree, focus) {
  const rows = [...container.querySelectorAll('[role="treeitem"]')];
  const path = focus?.path || tree.focusedPath;
  const row = rows.find(item => item.dataset.path === path) || rows.findLast(item => path?.startsWith(item.dataset.path + "/")) || rows[0];
  if (!row) return;
  row.tabIndex = 0;
  tree.focusedPath = row.dataset.path;
  if (focus) (focus.toggle ? row.querySelector(".file-chevron") || row : row).focus({preventScroll:true});
}
function navigateProjectTree(event, item, entry, tree) {
  if (!["ArrowUp", "ArrowDown", "Home", "End", "ArrowRight", "ArrowLeft"].includes(event.key)) return false;
  event.preventDefault();
  event.stopPropagation();
  const container = tree.foldersOnly ? $("project-directory-list") : $("files-tree");
  const rows = [...container.querySelectorAll('[role="treeitem"]')];
  const index = rows.indexOf(item);
  let next;
  if (event.key === "Home") next = rows[0];
  else if (event.key === "End") next = rows.at(-1);
  else if (event.key === "ArrowUp") next = rows[Math.max(0, index - 1)];
  else if (event.key === "ArrowDown") next = rows[Math.min(rows.length - 1, index + 1)];
  else if (event.key === "ArrowRight" && entry.type === "directory") {
    if (tree.expanded.has(entry.path)) next = item.querySelector('[role="treeitem"]');
    else void (tree.foldersOnly ? toggleProjectFolder(entry) : toggleProjectDirectory(entry));
  } else if (event.key === "ArrowLeft") {
    if (tree.expanded.has(entry.path)) void (tree.foldersOnly ? toggleProjectFolder(entry) : toggleProjectDirectory(entry));
    else next = item.parentElement.closest('[role="treeitem"]');
  }
  next?.focus();
  return true;
}
function renderProjectFileEntries(list, entries, tree = fileTree) {
  for (const entry of entries.filter(
    (entry) => !tree.foldersOnly || entry.type === "directory",
  )) {
    const item = document.createElement("li");
    item.setAttribute("role", "treeitem");
    item.setAttribute("aria-selected", String(tree.selected.has(entry.path)));
    item.tabIndex = -1;
    item.dataset.path = entry.path;
    item.addEventListener("focusin", event => {
      if (event.target.closest('[role="treeitem"]') !== item) return;
      const container = tree.foldersOnly ? $("project-directory-list") : $("files-tree");
      for (const row of container.querySelectorAll('[role="treeitem"]')) row.tabIndex = row === item ? 0 : -1;
      tree.focusedPath = entry.path;
    });
    const row = document.createElement("div");
    row.className = "project-file-row";
    if (entry.type === "directory") {
      const open = tree.expanded.has(entry.path),
        toggle = document.createElement("button");
      toggle.type = "button";
      toggle.tabIndex = -1;
      item.setAttribute("aria-expanded", String(open));
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
        if (navigateProjectTree(e, item, entry, tree)) return;
        if ([" ", "Enter"].includes(e.key)) {
          e.preventDefault();
          selectProjectFolder(entry);
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
      if (e.target.closest('[role="treeitem"]') !== item) return;
      if (navigateProjectTree(e, item, entry, tree)) return;
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
        "application/x-keepharness-authorized-project-files",
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
  const theme = window.HarnessFileIcons,
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
    selectAccordionSection("system-files");
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
    closeSidebar();
    await loadProjectFileDirectory(fileTree.rootId, fileTree.basePath);
  } catch (error) {
    status("Couldn't navigate to the project folder: " + error.message);
  }
}
async function loadProjectFileRoots(force = false) {
  if (!interfaceReady || $("activity-panel").hidden) return;
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
  $("files-error").setAttribute("role", "alert");
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
    // A guest cannot browse the system's folders: say so quietly, with nothing to retry.
    const ownerOnly = error.code === "host_files_owner_only";
    $("files-error").className = ownerOnly ? "file-tree-note" : "";
    $("files-error").setAttribute("role", ownerOnly ? "note" : "alert");
    $("files-error").textContent = ownerOnly
      ? "System files are visible to the owner only."
      : "Couldn't load the authorized folders: " + error.message;
    $("files-retry").hidden = ownerOnly;
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
  await loadProjectFileDirectory(root.id, "");
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
    refreshVisualMarkers();
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
  // F-59: re-list on every expand; folders may have changed outside the app.
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
  if (!(selection.root_id || selection.project_root_id) || !paths.length || !maxFiles) {
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
        ...(selection.project_root_id
          ? { project_root_id: selection.project_root_id }
          : { root_id: selection.root_id }),
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
        excerpt: attachment.excerpt,
        project: $("project").value,
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
function fileSizeLabel(bytes) {
  if (bytes < 1024) return bytes + " B";
  if (bytes < 1024 * 1024) return Math.round(bytes / 1024) + " KB";
  return (bytes / (1024 * 1024)).toFixed(1) + " MB";
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
      if (f.excerpt) el.append(excerptChip(f));
      if (f.size > 0) {
        const size = document.createElement("small");
        size.className = "attachment-size";
        size.textContent = fileSizeLabel(f.size);
        el.append(size);
      }
      const b = document.createElement("button");
      b.type = "button";
      b.append(HarnessUI.icon("x"));
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
  count.title = "Up to " + MAX_ATTACHMENTS + " files per message, 100 MiB each";
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
$("new").onclick = () => void navigate({ kind: "home" });
function startNewConversation() {
  const loose = Array.from($("project").options).some(
    (o) => o.value === "sem-projeto",
  );
  newConversation(
    loose
      ? "New Conversation"
      : "New Conversation in project " +
          $("project").selectedOptions[0]?.textContent,
    loose ? "sem-projeto" : $("project").value,
  );
  renderProjects();
  history();
  closeSidebar();
}
$("project").onchange = () => {
  const destination = $("project").value, draft = $("prompt").value,
    stale = [...invalidResourceTokens, ...resourceSelections.map(ref => ref.token)];
  if ([...$("project").options].some(option => option.value === composerProjectId))
    $("project").value = composerProjectId;
  chooseProject(destination);
  $("prompt").value = draft;
  resourceSelections = [];
  invalidResourceTokens = new Set(stale);
  updateComposer();
  saveView();
};
$("model").onchange = () => {
  imageRefusedModel = "";
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
      "application/x-keepharness-authorized-project-files",
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
    "application/x-keepharness-authorized-project-files",
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
  window.runConsole?.closeForPanel();
  if (matchMedia("(max-width:620px)").matches) {
    $("sidebar").classList.toggle("open");
  } else {
    document.body.classList.toggle("sidebar-collapsed");
    prefs.set(
      "sidebar_collapsed",
      document.body.classList.contains("sidebar-collapsed"),
    );
  }
  syncSidebarFocus();
  $("menu").setAttribute(
    "aria-expanded",
    String(
      matchMedia("(max-width:620px)").matches
        ? $("sidebar").classList.contains("open")
        : !document.body.classList.contains("sidebar-collapsed"),
    ),
  );
}
// Every site that closes the phone sidebar outside of toggleSidebar() must
// also clear #menu's aria-expanded, so assistive tech sees the same state.
const workspaceCoveredContent = new Map();
function syncWorkspaceModal() {
  if (!interfaceReady || document.querySelector("#tour-root")) return;
  for (const [node, inert] of workspaceCoveredContent) node.inert = inert;
  workspaceCoveredContent.clear();
  const sidebar = $("sidebar"), panel = $("activity-panel"), console = $("run-console");
  const active = console && !console.hidden && (innerWidth <= 700 || innerHeight <= 500) ? console
    : innerWidth <= 620 && sidebar.classList.contains("open") ? sidebar
    : innerWidth < 1000 && !panel.hidden ? panel : null;
  for (const node of [sidebar, panel, console].filter(Boolean)) {
    node.removeAttribute("aria-modal"); node.removeAttribute("aria-owns");
  }
  if (!active) return;
  active.setAttribute("aria-modal", "true");
  const main = document.querySelector("main");
  const covered = active === console
    ? [...main.children].filter(node => node !== console && node.id !== "run-status-strip").concat(sidebar, panel)
    : [main, active === sidebar ? panel : sidebar];
  active.setAttribute("aria-owns", active === console ? "run-status-strip app-topbar" : "app-topbar");
  const skip = document.querySelector(".skip-link");
  if (skip) covered.push(skip);
  for (const node of covered) {
    workspaceCoveredContent.set(node, node.inert);
    node.inert = true;
  }
  if (document.activeElement.closest("[inert]") || document.activeElement === document.body) {
    [...active.querySelectorAll("button,summary,input,select,textarea,a[href],[tabindex]")]
      .find(node => !node.disabled && node.tabIndex >= 0 && node.checkVisibility())?.focus({ preventScroll: true });
  }
}
function syncSidebarFocus() {
  const sidebar = $("sidebar"), overlay = innerWidth <= 620 && sidebar.classList.contains("open");
  if (overlay) {
    sidebar.setAttribute("role", "dialog"); sidebar.setAttribute("aria-modal", "true"); sidebar.setAttribute("aria-label", "Conversations");
    // Focus the dialog itself: its first button is an action (New Conversation) that typing could trigger.
    if (!sidebar.contains(document.activeElement)) { sidebar.tabIndex = -1; sidebar.focus({ preventScroll: true }); }
  } else { sidebar.removeAttribute("role"); sidebar.removeAttribute("aria-modal"); sidebar.removeAttribute("aria-label"); }
  syncWorkspaceModal();
}
function closeSidebar() {
  $("sidebar").classList.remove("open");
  syncSidebarFocus();
  if (matchMedia("(max-width:620px)").matches) {
    $("menu").setAttribute("aria-expanded", "false");
  }
}
$("menu").onclick = () => {
  toggleSidebar();
  fitPanels();
};
$("about").onclick = () => $("about-dialog").showModal();
$("about-close").onclick = () => $("about-dialog").close();
$("about-dialog").addEventListener("close", () => {
  if (document.querySelector("#tour-root")) return;
  ($("about").checkVisibility() ? $("about") : $("settings")).focus();
});
// D43: the theme switch lives in Settings › Appearance; the label names what a press does.
function syncThemeToggle() {
  $("theme-toggle").textContent =
    "Switch to " + (document.documentElement.dataset.theme === "dark" ? "light" : "dark") + " theme";
}
$("theme-toggle").onclick = () => {
  const dark = document.documentElement.dataset.theme === "dark";
  window.HarnessTheme?.apply(dark ? "paper" : "graphite");
  syncThemeToggle();
};
syncThemeToggle();
new MutationObserver(syncThemeToggle).observe(document.documentElement, {
  attributes: true, attributeFilter: ["data-theme"],
});
$("take-tour").addEventListener("click", () => $("settings-dialog").close());
// Codex-style "Choose project" under the composer: reuses the project select and its onchange.
// Composer sub-bar shortcuts (Codex model): project files and agents.
// OP-R1-22: the chip attaches (recent uploads, Space pages, Upload…); it never changes the mode.
function browseProjectFiles() {
  togglePanelView("files");
  // D-033: the browsable tree (and its attach actions) lives in System Files, like navigateProjectFolder.
  selectAccordionSection("system-files");
  const target =
    $("files-tree").querySelector('[role="treeitem"][tabindex="0"]') ||
    $("files-tree").querySelector('[role="treeitem"]') ||
    $("workspace-system-files-head");
  target?.focus({ preventScroll: true });
}
$("files-chip").onclick = () => openChipMenu($("files-menu"), $("files-chip"), renderFilesMenu);
$("files-menu").addEventListener("toggle", (event) =>
  $("files-chip").setAttribute("aria-expanded", String(event.newState === "open")),
);
// OP-R1-14: the chip opens the agent list at the caret; the draft is not touched until one is chosen.
$("agents-chip").onclick = () => {
  const input = $("prompt"),
    at = input.selectionEnd ?? input.value.length;
  input.focus();
  void refreshResources({ prefix: "@", query: "", start: at, end: at });
};
$("files-attach-selected").onclick = () => void attachSelectedProjectFiles();
// Start a chat from a set of files: a new conversation in this project with them attached.
$("files-new-chat").onclick = async () => {
  const selection = { root_id: fileTree.rootId, paths: [...fileTree.selected] },
    previous = conversation;
  if (!selection.paths.length) return;
  newConversation();
  if (previous && conversation === previous) return;
  await attachSelectedProjectFiles(selection);
  $("prompt").focus({ preventScroll: true });
};
function syncComposerProjectButton() {
  const option = $("project").selectedOptions[0];
  const chosen = option && option.value !== "sem-projeto";
  $("project-button-label").textContent = chosen
    ? option.textContent
    : option
      ? "No project"
      : "Choose project";
  $("project-button").title = chosen
    ? "Project: " + option.textContent
    : "Choose the project for this conversation";
  $("project-button").disabled = $("project").disabled;
  $("agents-chip").disabled = $("prompt").disabled;
}
$("project-button").onclick = () => {
  const menu = $("project-menu");
  if (menu.matches(":popover-open")) return menu.hidePopover();
  menu.replaceChildren(
    ...[...$("project").options].map((option) => {
      const item = document.createElement("button");
      item.type = "button";
      item.setAttribute("role", "option");
      item.setAttribute("aria-selected", String(option.value === $("project").value));
      item.append(
        HarnessUI.icon("folder"),
        document.createTextNode(option.value === "sem-projeto" ? "No project" : option.textContent),
      );
      item.onclick = () => {
        menu.hidePopover();
        if (option.value !== $("project").value) {
          $("project").value = option.value;
          $("project").onchange();
        }
        syncComposerProjectButton();
        $("prompt").focus();
      };
      return item;
    }),
  );
  menu.showPopover();
  const r = $("project-button").getBoundingClientRect();
  menu.style.left = Math.max(12, Math.min(r.left, innerWidth - menu.offsetWidth - 12)) + "px";
  menu.style.top = Math.max(12, Math.min(innerHeight - menu.offsetHeight - 12, r.bottom + 6)) + "px";
  menu.querySelector('[aria-selected="true"]')?.focus();
};
// Connectors and plugins (Codex "Plugins" chip): what is installed, allowed and
// effective for this project and route, and what was used here recently.
function usageAge(seconds) {
  const age = Math.max(0, Date.now() / 1000 - Number(seconds || 0));
  return age < 3600
    ? Math.max(1, Math.round(age / 60)) + " min ago"
    : age < 86400
      ? Math.round(age / 3600) + " h ago"
      : Math.round(age / 86400) + " d ago";
}
let pluginsView = null;
// WP4: tools another provider has connected, per route. Drives the Plugins chip dot,
// the "On other providers" menu section and the provider-switch note.
let elsewhereView = { key: "", list: [] },
  elsewherePending = "";
function routeKey(m) {
  return m.backend ? integrationsUrl(m) : "";
}
function integrationsUrl(m) {
  return (
    "/v1/integrations?" +
    new URLSearchParams({
      project_id: $("project").value,
      backend: m.backend,
      model: m.model,
      execution_mode: m.execution_mode,
      access_mode: $("access-mode").value,
    })
  );
}
// An answer for a route the composer has left is dropped; only refreshElsewhere tracks what is in flight.
function setElsewhere(key, list) {
  if (key !== routeKey(resourceEngine())) return;
  elsewhereView = { key, list: Array.isArray(list) ? list : [] };
  const chip = $("plugins-chip");
  if (elsewhereView.list.length) {
    chip.dataset.elsewhere = "true";
    chip.setAttribute("aria-label", "Plugins, tools available on other providers");
  } else {
    delete chip.dataset.elsewhere;
    chip.removeAttribute("aria-label");
  }
  syncRouteCarryover();
}
async function refreshElsewhere() {
  const m = resourceEngine(),
    key = routeKey(m);
  if (!key) {
    // No model: nothing is connected elsewhere for it, and a late answer for the old route is stale.
    elsewherePending = "";
    if (elsewhereView.key) setElsewhere("", []);
    return;
  }
  if (key === elsewhereView.key || key === elsewherePending) return;
  elsewherePending = key;
  try {
    const data = await json(integrationsUrl(m));
    if (elsewherePending === key) setElsewhere(key, data.elsewhere);
  } catch {
    // Only a hint is lost; the menu reports the real error when it is opened.
    if (elsewherePending === key) setElsewhere(key, []);
  }
}
function elsewhereSection(list, backend, menu) {
  const wrap = document.createElement("section"),
    heading = document.createElement("h3"),
    ul = document.createElement("ul"),
    here = providerNames[backend] || backend;
  wrap.dataset.testid = "elsewhere-section";
  heading.textContent = "On other providers";
  for (const entry of list) {
    const row = document.createElement("li"),
      box = document.createElement("div"),
      text = document.createElement("div"),
      name = document.createElement("strong"),
      meta = document.createElement("small"),
      on = entry.providers
        .filter((p) => p.allowed)
        .map((p) => providerNames[p.backend] || p.backend)
        .join(", ");
    box.className = "integration-row";
    box.dataset.testid = "elsewhere-row";
    name.textContent = entry.label;
    meta.textContent =
      entry.here === "enable"
        ? "Installed on " + here + ", turned off for KeepHarness"
        : "Not connected on " + here + " (connected on " + on + ")";
    text.append(name, meta);
    box.append(HarnessUI.icon("plug"), text);
    // The allow list lives in Settings (the harness API has no enable endpoint): System > Providers
    // where the admin is reachable, Customize otherwise.
    const action = document.createElement("button");
    action.type = "button";
    action.className = "plugins-action";
    action.textContent = entry.here === "enable" ? "Enable" : "Open Plugins";
    action.onclick = () => {
      menu.hidePopover();
      openSettings("providers") || openSettings("customize");
    };
    box.append(action);
    row.append(box);
    ul.append(row);
  }
  wrap.append(heading, ul);
  return wrap;
}
function carryoverToolLines(from, next) {
  if (elsewhereView.key !== routeKey(resourceEngine())) return [];
  const to = providerNames[next.backend] || next.backend,
    was = providerNames[from] || from;
  return elsewhereView.list
    .filter((entry) => entry.providers.some((p) => p.backend === from && p.allowed))
    .map((entry) =>
      entry.here === "absent"
        ? entry.label + " is connected on " + was + " but not on " + to
        : entry.label + " is installed on " + to + " but not enabled - enable it in Settings › System › Providers",
    );
}
function integrationRow(item, sharedReason = "") {
  const row = document.createElement("li"),
    open = document.createElement("button"),
    text = document.createElement("div"),
    name = document.createElement("strong"),
    meta = document.createElement("small");
  open.type = "button";
  open.className = "integration-row" + (item.effective ? "" : " unavailable");
  open.dataset.integrationId = item.id;
  open.title = "Show details";
  open.onclick = () => showIntegrationDetail(item);
  name.textContent = item.name;
  meta.textContent = [
    item.kind === "mcp" ? "Connector" + (item.transport ? " · " + item.transport : "") : "Plugin",
    item.used?.count
      ? "used " + item.used.count + "× · " + usageAge(item.used.last_used)
      : item.effective
        ? "not used here recently"
        : "",
    item.effective || item.reason === sharedReason ? "" : item.reason,
  ]
    .filter(Boolean)
    .join(" · ");
  text.append(name, meta);
  open.append(HarnessUI.icon(item.kind === "mcp" ? "plug" : "stack-2"), text);
  row.append(open);
  return row;
}
// Detail of one connector or plugin (Codex plugin page): what it is, whether
// this conversation may use it and why, and how it was used in this project.
function showIntegrationDetail(item) {
  const body = $("plugins-menu").querySelector(".plugins-body"),
    data = pluginsView?.data || {},
    back = document.createElement("button"),
    head = document.createElement("div"),
    title = document.createElement("h3"),
    kind = document.createElement("small"),
    facts = document.createElement("dl");
  back.type = "button";
  back.className = "plugins-back";
  back.append(HarnessUI.icon("chevron-left"), document.createTextNode("Connectors and plugins"));
  back.onclick = () => {
    body.replaceChildren(...(pluginsView?.parts || []));
    body.querySelector('[data-integration-id="' + CSS.escape(item.id) + '"]')?.focus();
  };
  head.className = "plugins-detail-head";
  title.textContent = item.name;
  kind.textContent =
    item.kind === "mcp"
      ? "Connector (MCP server)" + (item.transport ? " · " + item.transport : "")
      : "Plugin";
  head.append(HarnessUI.icon(item.kind === "mcp" ? "plug" : "stack-2"), title, kind);
  const fact = (term, value) => {
    if (!value) return;
    const dt = document.createElement("dt"),
      dd = document.createElement("dd");
    dt.textContent = term;
    dd.textContent = value;
    facts.append(dt, dd);
  };
  fact("In this conversation", item.effective ? "Available" : item.reason || "Not available");
  fact("Allowed for this provider", item.allowed ? "Yes" : "No");
  fact("Installed", item.status === "installed" || item.status === "configured" ? "Yes · " + item.status : item.status);
  fact(
    "Used in this project",
    item.used?.count
      ? item.used.count + "× · last " + usageAge(item.used.last_used)
      : "Not in the last " + (data.window_days || 30) + " days",
  );
  fact("Tools used", (item.used?.tools || []).join(", "));
  fact("Approvals", item.effective ? data.effective_note || "Follows the conversation's access mode." : "");
  facts.className = "plugins-facts";
  const parts = [back, head, facts];
  if (!$("settings-system-nav").hidden) {
    const manage = document.createElement("button");
    manage.type = "button";
    manage.className = "plugins-manage";
    manage.append(HarnessUI.icon("settings"), document.createTextNode("Manage connectors and plugins"));
    manage.onclick = () => {
      $("plugins-menu").hidePopover();
      openSettings("providers");
    };
    parts.push(manage);
  }
  body.replaceChildren(...parts);
  back.focus();
}
async function renderPluginsMenu() {
  const menu = $("plugins-menu"),
    m = resourceEngine(),
    heading = document.createElement("p");
  heading.className = "access-menu-heading";
  heading.textContent =
    "Connectors and plugins · " + (providerNames[m.backend] || m.backend || "no model");
  const body = document.createElement("div");
  body.className = "plugins-body";
  body.textContent = "Checking…";
  menu.replaceChildren(heading, body);
  if (!m.backend) {
    body.textContent = "Choose a model to see its connectors and plugins.";
    return;
  }
  try {
    const data = await json(integrationsUrl(m));
    setElsewhere(routeKey(m), data.elsewhere);
    const elsewhere = Array.isArray(data.elsewhere) ? data.elsewhere : [],
      items = Array.isArray(data.items) ? data.items : [],
      effective = items.filter((item) => item.effective),
      others = items.filter((item) => !item.effective),
      parts = [];
    if (data.effective_note) {
      const note = document.createElement("p");
      note.className = "plugins-note";
      note.textContent = data.effective_note;
      parts.push(note);
    }
    // A reason shared by every row is said once, under the heading.
    const section = (title, list, empty) => {
      const wrap = document.createElement("section"),
        h = document.createElement("h3"),
        ul = document.createElement("ul"),
        reasons = new Set(list.filter((item) => !item.effective).map((item) => item.reason)),
        shared = list.length > 1 && reasons.size === 1 ? [...reasons][0] : "";
      h.textContent = title;
      ul.replaceChildren(...list.map((item) => integrationRow(item, shared)));
      wrap.append(h);
      if (shared)
        wrap.append(Object.assign(document.createElement("p"), { className: "plugins-note", textContent: shared }));
      wrap.append(list.length ? ul : Object.assign(document.createElement("p"), { className: "plugins-empty", textContent: empty }));
      return wrap;
    };
    parts.push(
      section("Available in this conversation", effective, "None for this project and model."),
    );
    if (others.length) parts.push(section("Installed, not available here", others, ""));
    if (elsewhere.length) parts.push(elsewhereSection(elsewhere, m.backend, menu));
    const tools = Array.isArray(data.other_tools) ? data.other_tools : [];
    if (tools.length) {
      const details = document.createElement("details"),
        summary = document.createElement("summary"),
        list = document.createElement("ul");
      details.className = "plugins-tools";
      summary.textContent =
        "Other tools used in this project (" + (data.window_days || 30) + " days)";
      list.replaceChildren(
        ...tools.map((tool) =>
          Object.assign(document.createElement("li"), {
            textContent: tool.name + " · " + tool.count + "× · " + usageAge(tool.last_used),
          }),
        ),
      );
      details.append(summary, list);
      parts.push(details);
    }
    for (const warning of Array.isArray(data.warnings) ? data.warnings : [])
      parts.push(Object.assign(document.createElement("p"), { className: "plugins-note", textContent: warning }));
    if (!$("settings-system-nav").hidden) {
      const manage = document.createElement("button");
      manage.type = "button";
      manage.className = "plugins-manage";
      manage.append(HarnessUI.icon("settings"), document.createTextNode("Manage connectors and plugins"));
      manage.onclick = () => {
        menu.hidePopover();
        openSettings("providers");
      };
      parts.push(manage);
    }
    body.replaceChildren(...parts);
    pluginsView = { data, parts };
  } catch (error) {
    body.textContent = "Couldn't check connectors and plugins. " + error.message;
  }
}
function openChipMenu(menu, chip, render) {
  if (menu.matches(":popover-open")) return menu.hidePopover();
  menu.showPopover();
  // Anchored on the side with more room, so it grows away from the chip.
  const place = () => {
    const r = chip.getBoundingClientRect(),
      above = r.top > innerHeight - r.bottom;
    menu.style.left = Math.max(12, Math.min(r.left, innerWidth - menu.offsetWidth - 12)) + "px";
    menu.style.top = above ? "auto" : r.bottom + 6 + "px";
    menu.style.bottom = above ? innerHeight - r.top + 6 + "px" : "auto";
    menu.style.maxHeight = Math.max(160, (above ? r.top : innerHeight - r.bottom) - 18) + "px";
  };
  place();
  menu.tabIndex = -1;
  menu.focus({ preventScroll: true });
  void render().then(place);
}
$("plugins-chip").onclick = () => openChipMenu($("plugins-menu"), $("plugins-chip"), renderPluginsMenu);
// The Files chip (D40): what the next message can start from, without leaving the chat.
function menuButton(label, onclick, { testid, icon, hint } = {}) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "files-menu-item";
  if (testid) button.dataset.testid = testid;
  if (icon) button.append(icon);
  const text = document.createElement("span");
  text.textContent = label;
  button.append(text);
  if (hint) {
    const small = document.createElement("small");
    small.textContent = hint;
    button.append(small);
  }
  button.onclick = () => {
    $("files-menu").hidePopover();
    onclick();
  };
  return button;
}
function menuSection(title, items, empty) {
  const section = document.createElement("section"),
    heading = document.createElement("h3");
  heading.textContent = title;
  section.append(heading, ...(items.length ? items : [Object.assign(document.createElement("p"), { className: "plugins-empty", textContent: empty })]));
  return section;
}
function attachRecentUpload(entry) {
  if (files.some((item) => item.id === entry.id)) return;
  if (files.length >= MAX_ATTACHMENTS) return void status("Limit of 20 attachments reached. Remove one before adding another.");
  files.push(entry);
  renderFiles();
  saveView();
  updateComposer();
  status("File attached.");
}
async function attachSpacePage(pageId, project) {
  try {
    const page = await json("/v1/pages/" + encodeURIComponent(pageId) + "?" + new URLSearchParams({ project_id: project }));
    await upload([pageFile(page.title, page.body)]);
  } catch (error) {
    status("Couldn't attach the page: " + error.message);
  }
}
async function renderFilesMenu() {
  const menu = $("files-menu"),
    project = $("project").value,
    heading = document.createElement("p"),
    body = document.createElement("div"),
    attachable = canUpload() && !busy && !loading && !uploads,
    uploadButton = menuButton("Upload…", () => $("file").click(), { testid: "files-menu-upload", icon: HarnessUI.icon("paperclip") });
  heading.className = "access-menu-heading";
  heading.textContent = "Attach to this message";
  body.className = "plugins-body files-body";
  uploadButton.disabled = !attachable;
  if (!attachable) uploadButton.title = "Attachments are not available for this model or project right now.";
  const recent = recentUploads()
    .filter((entry) => entry.project === project && !files.some((item) => item.id === entry.id))
    .map((entry) => menuButton(entry.name, () => attachRecentUpload(entry), { testid: "files-menu-recent", icon: projectFileIcon(entry.name), hint: entry.size > 0 ? fileSizeLabel(entry.size) : "" }));
  for (const button of recent) button.disabled = !attachable;
  const pagesSection = menuSection("Space pages", [], "Checking…");
  body.append(uploadButton, menuSection("Recent uploads", recent, "Nothing uploaded in this project yet."), pagesSection);
  const detail = projectDetails[project];
  if (!detail || detail.root)
    body.append(menuButton("Browse project files…", browseProjectFiles, { testid: "files-menu-browse", icon: HarnessUI.icon("folder") }));
  menu.replaceChildren(heading, body);
  try {
    const listed = (await json("/v1/pages?" + new URLSearchParams({ project_id: project }))).pages || [];
    const buttons = listed.map((page) =>
      menuButton(page.title, () => void attachSpacePage(page.id, project), { testid: "files-menu-page", icon: projectFileIcon(page.title + ".md"), hint: "Current version" }),
    );
    for (const button of buttons) button.disabled = !attachable;
    pagesSection.replaceWith(menuSection("Space pages", buttons, "No pages in this project yet."));
  } catch (error) {
    pagesSection.replaceWith(menuSection("Space pages", [], "Couldn't load pages. " + error.message));
  }
}
$("plugins-menu").addEventListener("toggle", (event) =>
  $("plugins-chip").setAttribute("aria-expanded", String(event.newState === "open")),
);
$("project-menu").addEventListener("toggle", (event) =>
  $("project-button").setAttribute("aria-expanded", String(event.newState === "open")),
);
$("project-menu").addEventListener("keydown", (event) => {
  const options = [...$("project-menu").querySelectorAll("[role=option]")],
    index = options.indexOf(document.activeElement);
  if (!options.length || !["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
  event.preventDefault();
  const next = event.key === "Home" ? 0 : event.key === "End" ? options.length - 1
    : (index + (event.key === "ArrowDown" ? 1 : -1) + options.length) % options.length;
  options[next].focus();
});
// Chat | Code view switch (D45): the same conversation, with files and run activity beside it.
function syncViewSwitch() {
  const code = $("panel-toggle").getAttribute("aria-expanded") === "true";
  $("view-chat").setAttribute("aria-selected", String(!code));
  $("view-code").setAttribute("aria-selected", String(code));
  $("view-chat").tabIndex = code ? -1 : 0;
  $("view-code").tabIndex = code ? 0 : -1;
  document.body.dataset.view = code ? "code" : "chat";
}
function showView(view) {
  const code = $("panel-toggle").getAttribute("aria-expanded") === "true";
  if ((view === "code") !== code) $("panel-toggle").click();
  syncViewSwitch();
  $(view === "code" ? "view-code" : "view-chat").focus();
}
$("view-chat").onclick = () => showView("chat");
$("view-code").onclick = () => showView("code");
$("view-switch").addEventListener("keydown", (event) => {
  if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
  event.preventDefault();
  showView(event.key === "ArrowRight" || event.key === "End" ? "code" : "chat");
});
new MutationObserver(syncViewSwitch).observe($("panel-toggle"), { attributes: true, attributeFilter: ["aria-expanded"] });
syncViewSwitch();
function positionAttentionPopover() {
  const popover = $("attention-popover");
  if (popover.hidden) return;
  const anchor = $("attention-bell").getBoundingClientRect();
  popover.style.right = "auto";
  popover.style.left = Math.max(12, Math.min(anchor.right - popover.offsetWidth, innerWidth - popover.offsetWidth - 12)) + "px";
  popover.style.top = anchor.bottom + 8 + "px";
}
window.addEventListener("resize", positionAttentionPopover);
new ResizeObserver(positionAttentionPopover).observe($("provider-quotas"));
$("attention-bell").onclick = () => {
  const popover = $("attention-popover");
  popover.hidden = !popover.hidden;
  positionAttentionPopover();
  $("attention-bell").setAttribute("aria-expanded", String(!popover.hidden));
  // UX-R4-3: a dialog-role popover opened from the keyboard puts focus on its first control.
  if (!popover.hidden) popover.querySelector("button")?.focus();
};
const updateAttentionLabel = () => {
  const count = Number($("attention-count").textContent) || 0;
  $("attention-bell").setAttribute(
    "aria-label",
    `Attention, ${count} ${count === 1 ? "item" : "items"}`,
  );
  $("attention-summary").textContent =
    (count ? `${count} ${count === 1 ? "item needs" : "items need"} you now.` : "Nothing needs you right now.") +
    " Choose which events to open in the inbox.";
};
new MutationObserver(updateAttentionLabel).observe($("attention-count"), {
  childList: true,
  characterData: true,
  subtree: true,
});
updateAttentionLabel();
$("attention-open-inbox").onclick = () => {
  $("attention-popover").hidden = true;
  $("attention-bell").setAttribute("aria-expanded", "false");
  window.runConsole?.openAttention("request");
};
for (const button of document.querySelectorAll("[data-attention-filter]"))
  button.onclick = () => {
    document
      .querySelectorAll("[data-attention-filter]")
      .forEach((item) =>
        item.setAttribute(
          "aria-pressed",
          String(item === button),
        ),
      );
    $("attention-popover").hidden = true;
    $("attention-bell").setAttribute("aria-expanded", "false");
    window.runConsole?.openAttention(button.dataset.attentionFilter);
  };
document.addEventListener("pointerdown", (event) => {
  if (!event.target.closest("#attention-bell, #attention-popover")) {
    $("attention-popover").hidden = true;
    $("attention-bell").setAttribute("aria-expanded", "false");
  }
});
document.body.classList.toggle(
  "sidebar-collapsed",
  prefs.get("sidebar_collapsed", false) === true,
);
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
  readinessRetryAt = 0,
  readinessFocus = null;
function setReadiness(ready, message = "") {
  if (!ready && interfaceReady) {
    const active = document.activeElement;
    readinessFocus = active.closest("#settings-dialog") ? $("settings") : active;
  }
  if (!ready) window.keepHarnessTour?.stop(false);
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
  if (ready) {
    syncWorkspaceModal();
    if (readinessFocus?.isConnected && readinessFocus.checkVisibility() && !readinessFocus.closest("[inert]"))
      readinessFocus.focus({ preventScroll: true });
    readinessFocus = null;
    document.dispatchEvent(new Event("harness:ready"));
  }
  if (!ready) {
    for (const menu of document.querySelectorAll(".composer-menu:popover-open"))
      menu.hidePopover();
    // Editors stay open with what was typed; their own saves report a failed request.
    for (const dialog of document.querySelectorAll(
      "dialog[open]:not(#vpn-login, #space-dialog, #scheduled-dialog, #agent-dialog)",
    ))
      dialog.close();
  } else if ($("vpn-login").open) $("vpn-login").close();
}
function modelAvailability(
  data = { admin_url: $("admin-link").getAttribute("href") },
  error = "",
) {
  modelAvailabilityError = error;
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
  // The embedded admin is this computer's and only accepts its own host: a page
  // opened over the network, or as localhost for a 127.0.0.1 admin, cannot frame it.
  $("settings-system-nav").hidden =
    link.hidden || location.hostname !== new URL(link.href).hostname;
  // "Open admin panel" is Settings > Providers, not a second window.
  // Where Settings > System is unavailable (localhost, network host) the link keeps its normal navigation.
  link.onclick = (event) => {
    if (openSettings("providers")) event.preventDefault();
  };
  $("model").disabled =
    !models.length || submitting || loading || uploads > 0 || policyPending;
  $("effort").disabled = $("model").disabled;
  $("prompt").placeholder = models.length
    ? "Send a message, or / for agents and skills"
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
// QA-R2-3: an idle tab keeps under ~30 requests a minute (the server budget is shared by every tab of a person).
const VERSION_CHECK_EVERY = 3;
let backgroundTicks = 0;
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
        const routeConversation = new URLSearchParams(location.search).get("conversation");
        if (routeConversation && /^[A-Za-z0-9_-]{1,200}$/.test(routeConversation) && routeConversation !== saved.conversation)
          saved = { conversation: routeConversation };
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
    models = composerModels(m);
    uploadsAllowed = m.uploads_enabled === true;
    offerFullAccess(m.full_access === true);
    localOwner = m.local_owner === true;
    policyProject = null;
    policyPending = false;
    $("model").replaceChildren(
      ...models.map((m) => new Option(modelLabel(m), m.id)),
    );
    if (models.some((m) => m.id === previous)) $("model").value = previous;
    updateEfforts();
    restoreSelection();
    if (!(await refreshProjectPermissions(5000)))
      throw Error("Couldn't load this project's permissions.");
    modelAvailability(m);
    if (!(await history(5000)))
      throw Error("Couldn't load the conversation history.");
    status(
      pendingSelectionNotice ||
        (models.length ? "Ready to chat." : "Set up a model to get started."),
    );
    pendingSelectionNotice = "";
    let resumeWatch = false;
    if (!startupTimer) {
      try {
        if (saved.conversation)
          resumeWatch = await load(saved.conversation, false, saved, scrollByConversation.get(saved.conversation));
        restoreView(saved);
      } catch {}
      startupTimer = setInterval(() => {
        if (!document.hidden && interfaceReady && !initializing) {
          // Check installed UI and runtime updates every third tick.
          if (++backgroundTicks % VERSION_CHECK_EVERY === 0) checkVersion();
          // Rebuilding the sidebar would close an open row or project actions menu.
          if (!document.querySelector(".conversation-actions[open], .project-actions-menu:popover-open")) history(undefined, true);
        }
      }, 10000);
    }
    setReadiness(true);
    if (!$("activity-panel").hidden) {
      loadAuthorizedProjectRoots();
      loadProjectFileRoots();
    }
    void Promise.all([quota(), checkVersion()]);
    updateComposer();
    // F-82: a stream cut by the outage re-attaches as soon as the server is back.
    if ((resumeWatch || streamDisconnected) && job) void watch();
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
window.addEventListener("online", async () => {
  await retryReadiness();
  // F-82: resume tracking a run the outage detached, without a user tap.
  if (streamDisconnected && job && interfaceReady && !initializing)
    void watch();
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
    let project = $("project").value;
    if (!p.projects.includes(project)) {
      // F-81: a removed project is not a lost connection. Move the draft to a
      // project that still exists and say so until something else is shown.
      if (busy || loading || uploads) return;
      const label = $("project").selectedOptions[0]?.textContent || project;
      updateProjectMetadata(p.details);
      $("project").replaceChildren(
        ...p.projects.map((id) => new Option(p.details?.[id]?.label || id, id)),
      );
      policyProject = null; // re-check permissions even for a known project
      chooseProject(
        p.projects.includes("sem-projeto") ? "sem-projeto" : p.projects[0],
      );
      project = $("project").value;
      status(
        "The project “" +
          label +
          "” was removed. Your draft was kept and moved to " +
          (project === "sem-projeto"
            ? "No project"
            : $("project").selectedOptions[0]?.textContent || project) +
          ".",
      );
    }
    const scoped = await json(
      "/v1/models?project_id=" + encodeURIComponent(project),
      { signal: AbortSignal.timeout(5000) },
    );
    if (!Array.isArray(scoped.models)) throw Error("Invalid permissions");
    if (
      !busy &&
      !loading &&
      project === $("project").value &&
      (JSON.stringify(composerModels(scoped)) !== JSON.stringify(models) ||
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
$("models-retry").onclick = async () => {
  if (!busy) { await initialize(); composerCondition = null; updateComposer(); }
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
      .slice(0, MAX_ATTACHMENTS)
      .map((f) => ({ ...f, project: saved.project }));
    renderFiles();
  }
  saveView();
  updateComposer();
}
const draftViews = new Map();
const unsavedDrafts = new Map();
let latestDraftSnapshot = null, draftRetry = 0;
function flushDrafts() {
  clearTimeout(draftRetry);
  try {
    for (const [key, snapshot] of unsavedDrafts) sessionStorage.setItem(key, snapshot);
    if (latestDraftSnapshot !== null) sessionStorage.setItem("remote-view", latestDraftSnapshot);
    unsavedDrafts.clear();
    latestDraftSnapshot = null;
  } catch {
    draftRetry = setTimeout(flushDrafts, 2000);
  }
  const dirty = unsavedDrafts.size > 0 || latestDraftSnapshot !== null;
  $("draft-storage-warning").hidden = !dirty;
  return !dirty;
}
window.addEventListener("beforeunload", event => {
  if (!flushDrafts()) { event.preventDefault(); event.returnValue = ""; }
});
window.addEventListener("pagehide", flushDrafts);
document.addEventListener("visibilitychange", () => { if (document.hidden) flushDrafts(); });
function retireDraft(key) {
  draftViews.delete(key);
  unsavedDrafts.delete(key);
  try { sessionStorage.removeItem(key); } catch {}
}
function readDraft(key) {
  try { return JSON.parse(draftViews.get(key) || sessionStorage.getItem(key) || "null"); }
  catch { return null; }
}
function saveView() {
  if (loading) return true;
  try {
    const route = new URL(location.href);
    if (conversation) route.searchParams.set("conversation", conversation);
    else route.searchParams.delete("conversation");
    if (route.href !== location.href) window.history.replaceState(null, "", route);
    const snapshot = JSON.stringify({
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
      });
    const key = "conversation-draft:" + (conversation || "new:" + $("project").value);
    draftViews.set(key, snapshot);
    unsavedDrafts.set(key, snapshot);
    latestDraftSnapshot = snapshot;
    return flushDrafts();
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
    const restartPending = v.disk_source_build && v.disk_source_build !== v.source_build;
    $("version").textContent = "Release: " + v.version +
      (restartPending ? " · Restart to finish the update" : "");
    $("version").title = "";
    $("about-version").textContent = "Release " + v.version + " · MIT licence";
    if (v.config_reload_error)
      status(
        "Couldn't apply the configuration. The harness kept the last valid configuration. Review the admin panel.",
      );
    if (uiBuild && v.ui_build && uiBuild !== v.ui_build) reloadPending = true;
    uiBuild = v.ui_build;
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
// D-033: the panel shows one view at a time, Activities (a one-open accordion) or Files.
let rightPanelView = "files";
const ACTIVITY_SECTIONS = ["activity", "background-tasks", "resources"];
const FILES_SECTIONS = ["project-files", "system-files"];
const ACCORDION_GROUPS = [ACTIVITY_SECTIONS, FILES_SECTIONS];
function syncPanelToggles() {
  const open = !$("activity-panel").hidden;
  $("files-toggle").setAttribute("aria-expanded", String(open && rightPanelView === "files"));
  $("activity-toggle").setAttribute("aria-expanded", String(open && rightPanelView === "activity"));
}
function setPanelView(view, persist = true) {
  rightPanelView = view === "files" ? "files" : "activity";
  const files = rightPanelView === "files";
  $("activities-view").hidden = files;
  $("files-view").hidden = !files;
  $("activity-title").textContent = "Activity";
  syncPanelToggles();
  if (persist) prefs.set("right_panel_view", rightPanelView);
}
// Exactly one section per accordion (Activities, Files) is open; it is remembered in the workspace_sections preference.
function selectAccordionSection(name, { persist = true, focus = false } = {}) {
  const group = ACCORDION_GROUPS.find((names) => names.includes(name));
  for (const section of group) {
    const on = section === name, head = $("workspace-" + section + "-head");
    document.querySelector('[data-workspace-section="' + section + '"]').dataset.open = String(on);
    head.setAttribute("aria-expanded", String(on));
    head.setAttribute("aria-disabled", String(on));
    $("workspace-" + section).hidden = !on;
  }
  if (focus) $("workspace-" + name + "-head").focus();
  if (persist)
    prefs.set("workspace_sections", {
      ...prefs.get("workspace_sections", {}),
      ...Object.fromEntries(group.map((section) => [section, { open: section === name, height: null }])),
    });
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
  if (open && persist) window.runConsole?.closeForPanel();
  $("activity-panel").hidden = !open;
  const overlay = open && innerWidth < 1000;
  const panel = $("activity-panel");
  panel.setAttribute("role", overlay ? "dialog" : "complementary");
  if (overlay) panel.setAttribute("aria-modal", "true");
  else panel.removeAttribute("aria-modal");
  if (overlay && !panel.contains(document.activeElement)) $("files-toggle").focus();
  syncQuotaDock();
  $("panel-toggle").setAttribute("aria-expanded", String(open));
  if (!open && $("activity-panel").contains(document.activeElement))
    $("panel-toggle").focus();
  syncPanelToggles();
  syncWorkspaceModal();
  if (open && interfaceReady) {
    void refreshWorkspaceResources();
    loadAuthorizedProjectRoots();
    loadProjectFileRoots();
  }
  if (persist) prefs.set("activity_open", !!open);
}
document.addEventListener("keydown", event => {
  if (event.defaultPrevented || event.key !== "Tab" || document.querySelector("dialog[open], #tour-root, [popover]:popover-open")) return;
  const panel = !$("attention-popover").hidden ? $("attention-popover")
    : (innerWidth <= 700 || innerHeight <= 500) && document.querySelector("#run-console:not([hidden])") ? $("run-console")
    : innerWidth <= 620 && $("sidebar").classList.contains("open") ? $("sidebar")
    : innerWidth < 1000 && !$("activity-panel").hidden ? $("activity-panel") : null;
  // With no overlay, only the page edges wrap, so Tab never drops focus out of the window onto <body>.
  const surfaces = panel ? [panel, ...(panel.getAttribute("aria-owns") || "").split(/\s+/).map(id => $(id)).filter(Boolean)] : [document.body];
  const controls = [...new Set(surfaces.flatMap(surface => [...surface.querySelectorAll("a[href],button,input,select,textarea,summary,[tabindex]")]))]
    .filter(node => node.tabIndex >= 0 && !node.disabled && node.checkVisibility());
  if (!controls.length) return;
  const index = controls.indexOf(document.activeElement);
  if (!panel && !controls.at(event.shiftKey ? 0 : -1).contains(document.activeElement)) return;
  event.preventDefault();
  const next = index < 0 ? (event.shiftKey ? controls.length - 1 : 0) : (index + (event.shiftKey ? -1 : 1) + controls.length) % controls.length;
  controls[next].focus();
  controls[next].scrollIntoView({ block: "nearest", inline: "nearest", behavior: "instant" });
});
let authorizedRootsRequest = 0;
async function loadAuthorizedProjectRoots() {
  const holder = $("authorized-project-roots"),
    authorize = $("authorize-project-root"),
    project = $("project").value,
    request = ++authorizedRootsRequest,
    noProject = project === "sem-projeto";
  $("project-files-empty").hidden = !noProject;
  authorize.hidden = noProject;
  holder.hidden = noProject;
  if (noProject) {
    holder.replaceChildren();
    authorize.disabled = true;
    return;
  }
  holder.textContent = "Loading project roots…";
  authorize.disabled = true;
  authorize.title = "Checking project permissions";
  try {
    const base = new URLSearchParams({
      view: "authorized",
      project_id: project,
      start: "1",
      limit: "100",
    });
    const data = await json("/v1/project-files?" + base);
    if (request !== authorizedRootsRequest) return;
    authorize.disabled = !data.can_authorize || project === "sem-projeto";
    authorize.title = authorize.disabled
      ? "Additional roots are not available for this project."
      : "Add a folder to this project";
    const roots = Array.isArray(data.roots) ? [...data.roots] : [];
    if (data.root_id) {
      roots.sort((left, right) =>
        left.id === data.root_id ? -1 : right.id === data.root_id ? 1 : 0,
      );
    }
    holder.replaceChildren();
    const rootListings = [];
    for (const root of roots) {
      const card = document.createElement("section");
      card.className = "authorized-root-card";
      const heading = document.createElement("strong");
      heading.textContent = root.path || root.label;
      heading.title = heading.textContent;
      heading.prepend(projectFileIcon(root.path || root.label, true));
      const badge = document.createElement("span");
      badge.className = "root-access-badge";
      badge.textContent = "authorized";
      const list = document.createElement("ul");
      card.append(heading, badge, list);
      holder.append(card);
      rootListings.push({ root, list });
    }
    let nextRoot = 0;
    async function loadNextRoot() {
      while (nextRoot < rootListings.length) {
        if (request !== authorizedRootsRequest) return;
        const { root, list } = rootListings[nextRoot++];
        const params = new URLSearchParams({
          view: "authorized",
          project_id: project,
          root_id: root.id,
          path: "",
          start: "1",
          limit: "100",
        });
      try {
        const listing = await json("/v1/project-files?" + params);
        if (request !== authorizedRootsRequest) return;
        for (const entry of (listing.entries || []).slice(0, 12)) {
          const row = document.createElement("li");
          const name = document.createElement(
            entry.type === "directory" ? "span" : "button",
          );
          name.textContent = entry.name;
          name.title = entry.name;
          name.prepend(projectFileIcon(entry.name, entry.type === "directory"));
          if (name.tagName === "BUTTON") {
            name.type = "button";
            name.title = "Attach " + entry.name;
            name.onclick = () =>
              attachSelectedProjectFiles({
                project_root_id: root.id,
                paths: [entry.path],
              });
          }
          row.append(name);
          if (entry.status) {
            const state = document.createElement("b");
            state.className = "file-status-badge";
            state.textContent = entry.status;
            state.title = "Git status " + entry.status;
            row.append(state);
          }
          list.append(row);
        }
        if (!list.children.length) {
          const empty = document.createElement("li");
          empty.textContent = "No files at this level.";
          list.append(empty);
        }
      } catch {
        if (request !== authorizedRootsRequest) return;
        const unavailable = document.createElement("li");
        unavailable.textContent = "Couldn't load this root.";
        list.append(unavailable);
      }
      }
    }
    await Promise.all(
      Array.from(
        { length: Math.min(4, rootListings.length) },
        () => loadNextRoot(),
      ),
    );
    if (!roots.length) holder.textContent = "No project root is authorized.";
  } catch {
    if (request !== authorizedRootsRequest) return;
    holder.textContent = "Project roots are unavailable in this service.";
    authorize.disabled = true;
    authorize.title = "Project root authorization is unavailable.";
  }
}
$("authorize-project-root").onclick = () => {
  const project = $("project").value;
  if (project !== "sem-projeto" && !$("authorize-project-root").disabled)
    openProjectDialog(project);
};
function togglePanelView(view) {
  setPanelView(view);
  if ($("activity-panel").hidden) setPanelOpen(true);
}
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
  const preference = prefs.get("activity_open", false) === true,
    savedView = prefs.get("right_panel_view", null);
  rightPanelView = savedView || (preference ? "activity" : "files");
  setPanelView(rightPanelView, false);
  setPanelOpen(
    matchMedia("(max-width:700px)").matches
      ? false
      : preference,
    false,
  );
} catch {
  rightPanelView = "files";
  setPanelView("files", false);
  setPanelOpen(false, false);
}
matchMedia("(max-width:999px)").addEventListener("change", () => {
  if (!$("activity-panel").hidden) setPanelOpen(true, false);
});
matchMedia("(max-width:700px)").addEventListener("change", (event) => {
  if (event.matches) setPanelOpen(false, false);
  else {
    if (prefs.get("activity_open", false) === true) setPanelOpen(true, false);
  }
});

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
    (model ? modelName(model) : "Model") +
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
if (prefs.get("panel_order", null) === "conversations-right")
  panelOrder = "conversations-right";
const panelField = (id) => id.replace("-", "_");
const panelWidths = { sidebar: 300, "activity-panel": 390 };
const customizedPanels = new Set();
const panelIsLeft = (id) =>
  id === "sidebar"
    ? panelOrder === "conversations-left"
    : panelOrder === "conversations-right";
function panelLimits(id) {
  const mobile = innerWidth <= 620,
    docked = innerWidth >= 1000;
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
        : innerWidth - otherWidth - (innerWidth < 1200 ? 320 : 480),
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
    customizedPanels.add(id);
    prefs.set("panel_widths", { ...prefs.get("panel_widths", {}), [panelField(id)]: value });
  }
}
for (const id of Object.keys(panelWidths)) {
  const saved = Number(prefs.get("panel_widths", {})[panelField(id)]);
  if (saved >= 220 && saved <= 720) { panelWidths[id] = saved; customizedPanels.add(id); }
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
  syncSidebarFocus();
  $("menu").setAttribute("aria-expanded", String(
    matchMedia("(max-width:620px)").matches ? $("sidebar").classList.contains("open") : !document.body.classList.contains("sidebar-collapsed")
  ));
  const narrow = innerWidth >= 1000 && innerWidth < 1200;
  sizePanel("sidebar", narrow && !customizedPanels.has("sidebar") ? 260 : panelWidths.sidebar, false);
  sizePanel("activity-panel", narrow && !customizedPanels.has("activity-panel") ? 340 : panelWidths["activity-panel"], false);
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
  $("nav-forward").after($(reversed ? "panel-toggle" : "menu"));
  $("attention-popover").after($(reversed ? "menu" : "panel-toggle"));
  for (const button of document.querySelectorAll("[data-panel-order]"))
    button.setAttribute(
      "aria-pressed",
      String(button.dataset.panelOrder === panelOrder),
    );
  if (persist) prefs.set("panel_order", panelOrder);
  fitPanels();
}
applyPanelOrder(panelOrder, false);
for (const button of document.querySelectorAll("[data-panel-order]"))
  button.onclick = () => applyPanelOrder(button.dataset.panelOrder);
$("panel-order-reset").onclick = () => {
  panelWidths.sidebar = 300;
  panelWidths["activity-panel"] = 390;
  prefs.set("panel_widths", null);
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
  prefs.set("chat_selection", preferredSelection);
}
let pendingSelectionNotice = "";
// Registered here, after interfaceReady and pendingSelectionNotice exist: queued notices fire at once, and
// initialize() would otherwise overwrite the status line with "Ready to chat."
prefs.onNotice((code) => {
  const text = userErrors[code];
  if (interfaceReady) setTimeout(() => status(text));
  else pendingSelectionNotice = pendingSelectionNotice ? pendingSelectionNotice + " " + text : text;
});
function selectionNotice(gone) {
  const text =
    gone +
    " is no longer available; switched to " +
    modelName($("model").value) +
    ".";
  // After the caller's own status updates (newConversation clears the bar);
  // during startup, initialize shows it instead of "Ready to chat."
  if (interfaceReady) setTimeout(() => status(text));
  else pendingSelectionNotice = text;
}
function restoreSelection() {
  if (!models.length) return;
  const model = models.find((m) => m.id === preferredSelection.model);
  if (!model) {
    // F-90: the saved model is gone; say so once instead of switching silently.
    if (preferredSelection.model) {
      selectionNotice(modelName(preferredSelection.model));
      preferredSelection = { ...preferredSelection, model: "" };
    }
    return;
  }
  $("model").value = model.id;
  updateEfforts();
  if (model.efforts.includes(preferredSelection.effort))
    $("effort").value = preferredSelection.effort;
}
let lastSection = prefs.get("last_section", "appearance");
function showSettingsPage(button) {
  lastSection = button.dataset.adminSection || button.dataset.settings;
  prefs.set("last_section", lastSection);
  for (const name of ["appearance", "customize", "models", "archived", "system"])
    $("settings-" + name).hidden = name !== button.dataset.settings;
  if (button.dataset.settings === "archived") void loadArchived();
  const system = button.dataset.settings === "system";
  $("catalog-status").hidden = $("catalog-refresh").hidden =
    system || button.dataset.settings === "archived";
  $("settings-dialog").classList.toggle("system-open", system);
  if (system) showAdminSection(button.dataset.adminSection);
  document
    .querySelectorAll("[data-settings]")
    .forEach((b) => b.setAttribute("aria-pressed", String(b === button)));
}
document.querySelectorAll("[data-settings]").forEach(
  (button) =>
    (button.onclick = () => void navigate(settingsView(button.dataset.settings, button))),
);
// Settings › System shows the local admin panel on the same screen.
function adminFrameUrl(section) {
  const url = new URL($("admin-link").href);
  url.search =
    "?embedded=1&theme=" +
    encodeURIComponent(document.documentElement.dataset.palette || "");
  url.hash = section;
  return url.href;
}
function showAdminSection(section = "providers") {
  // Created on first use so ordinary page loads carry no extra document.
  let frame = $("admin-frame");
  if (!frame) {
    frame = document.createElement("iframe");
    frame.id = "admin-frame";
    $("settings-system").append(frame);
  }
  const label = document.querySelector('[data-admin-section="' + section + '"]');
  frame.title = "Administration: " + (label?.textContent || section);
  const next = adminFrameUrl(section);
  if (frame.src !== next) frame.src = next;
}
// `section` is a data-admin-section or a data-settings value; false when it is an admin section
// on a host that cannot frame the admin, or unknown.
function openSettings(section) {
  const remembered = section === undefined;
  const find = (name) =>
    document.querySelector('[data-admin-section="' + name + '"]') ||
    document.querySelector('[data-settings="' + name + '"]:not([data-admin-section])');
  const usable = (item) => item && !(item.dataset.adminSection && $("settings-system-nav").hidden);
  let button = find(remembered ? lastSection : section);
  // A remembered section that no longer exists (or is hidden here) opens Appearance.
  if (remembered && !usable(button)) button = find("appearance");
  if (!usable(button)) return false;
  void navigate(settingsView(button.dataset.settings, button));
  return true;
}
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
  void loadHarnessAgents();
  const request = ++catalogRequest,
    project = $("project").value;
  $("catalog-status").textContent = "Checking catalog for " + project + "…";
  $("catalog-models").replaceChildren(
    ...models.map((m) =>
      catalogCard({
        name: modelIcon(m.id) + " " + modelLabel(m),
        description:
          ({
            local: "Local model; runs through the Codex CLI. ",
            gemini: "Model via Gemini CLI. ",
            claude: "Model via Claude Code. ",
            deepseek: "DeepSeek model; runs through the Codex CLI. ",
          }[m.backend] || "Model via Codex. ") +
          "Efforts: " +
          m.efforts.map((e) => efforts[e] || e).join(", "),
        status: "Configured model",
        source: "/v1/models",
      }),
    ),
  );
  // D47: Gemini is unavailable in this release, so no "Not configured" card invites a setup nobody can do.
  for (const provider of ["codex", "claude"]) {
    if (!providers[provider])
      $("catalog-models").append(
        catalogCard({
          name: providerNames[provider],
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
// The Settings button opens a submenu of the visible Settings sections (same buttons as the
// dialog's navigation); Ctrl+, opens the dialog directly at the last section.
const settingsMenu = $("settings-menu");
settingsMenu.addEventListener("beforetoggle", (event) => {
  if (event.newState !== "open") return;
  settingsMenu.replaceChildren(
    ...[...document.querySelectorAll(".settings-nav-group:not([hidden])")].map((group) => {
      const section = document.createElement("div");
      section.className = "settings-menu-group";
      section.setAttribute("role", "group");
      section.setAttribute("aria-label", group.querySelector(".settings-nav-label").textContent);
      const heading = Object.assign(document.createElement("p"), {
        className: "access-menu-heading",
        textContent: group.querySelector(".settings-nav-label").textContent,
      });
      heading.setAttribute("aria-hidden", "true");
      section.append(heading);
      for (const source of group.querySelectorAll("[data-settings]")) {
        const item = document.createElement("button");
        item.type = "button";
        item.setAttribute("role", "menuitem");
        item.tabIndex = -1;
        const icon = source.querySelector("svg");
        if (icon) item.append(icon.cloneNode(true));
        item.append(document.createTextNode(source.textContent.trim()));
        item.onclick = () => {
          settingsMenu.hidePopover();
          openSettings(source.dataset.adminSection || source.dataset.settings);
        };
        section.append(item);
      }
      return section;
    }),
  );
});
settingsMenu.addEventListener("toggle", (event) => {
  const open = event.newState === "open";
  $("settings").setAttribute("aria-expanded", String(open));
  if (open) {
    // Below the button when it fits, else above it (the button may sit near the bottom).
    const rect = $("settings").getBoundingClientRect(),
      height = settingsMenu.offsetHeight;
    settingsMenu.style.top =
      Math.max(8, rect.bottom + 6 + height <= innerHeight - 8 ? rect.bottom + 6 : rect.top - height - 6) + "px";
    settingsMenu.style.left = Math.max(8, Math.min(rect.right - 240, innerWidth - 248)) + "px";
    settingsMenu.dataset.placed = ""; // revealed only once positioned (see the stylesheet)
    settingsMenu.querySelector('[role="menuitem"]')?.focus();
  }
  else {
    delete settingsMenu.dataset.placed;
    if (!document.activeElement || document.activeElement === document.body) $("settings").focus();
  }
});
settingsMenu.addEventListener("keydown", (event) => {
  const items = [...settingsMenu.querySelectorAll('[role="menuitem"]')];
  const at = items.indexOf(document.activeElement);
  const step = { ArrowDown: at + 1, ArrowUp: at - 1, Home: 0, End: items.length - 1 }[event.key];
  if (step !== undefined) {
    event.preventDefault();
    items[(step + items.length) % items.length].focus();
  } else if (event.key === "Tab") settingsMenu.hidePopover();
});
$("settings-close").onclick = () => $("settings-dialog").close();

// Back / forward (Codex model): a short in-memory history of views. It never touches
// window.history, so the saveView URL and the embedded admin iframe are unaffected.
const NAV_LIMIT = 50;
const DIALOG_VIEWS = { settings: "settings-dialog", customize: "settings-dialog", space: "space-dialog", scheduled: "scheduled-dialog" };
let viewHistory = [],
  viewIndex = -1;
// Scroll positions survive a reload: the 50 most recent conversations are kept in the UI state store.
const scrollByConversation = new Map();
for (const [id, top] of prefs.get("conversation_scroll", []))
  if (typeof id === "string" && Number.isFinite(top)) scrollByConversation.set(id, top);
function rememberScroll() {
  // While a conversation loads, #messages is not its content yet.
  if (!conversation || loading) return;
  const box = $("messages");
  scrollByConversation.delete(conversation);
  // At the bottom, remember "the end" (-1), not a pixel offset: the reply may grow or the window shrink.
  scrollByConversation.set(conversation, box.scrollHeight - box.scrollTop - box.clientHeight < 48 ? -1 : box.scrollTop);
  while (scrollByConversation.size > NAV_LIMIT) scrollByConversation.delete(scrollByConversation.keys().next().value);
  prefs.set("conversation_scroll", [...scrollByConversation]);
}
// ui-prefs.js sends on pagehide too, but before this runs; flush again so the last scroll goes out.
const rememberScrollAndFlush = () => { rememberScroll(); void prefs.flush({ keepalive: true }); };
addEventListener("pagehide", rememberScrollAndFlush);
document.addEventListener("visibilitychange", () => document.hidden && rememberScrollAndFlush());
const sameView = (a, b) => a.kind === b.kind && (a.id || null) === (b.id || null) && (a.section || null) === (b.section || null) && (a.sub || null) === (b.sub || null);
const currentBaseView = () => (conversation ? { kind: "conversation", id: conversation } : { kind: "home" });
const pressedSettings = () => document.querySelector('[data-settings][aria-pressed="true"]');
// The five System buttons share data-settings="system"; `sub` (the admin section) tells them apart.
const settingsView = (section, button) =>
  section === "customize"
    ? { kind: "customize", button }
    : { kind: "settings", section, sub: section === "system" ? (button || pressedSettings())?.dataset.adminSection : undefined, button };
const navigationBlocked = (view) => ["conversation", "home"].includes(view.kind) && (submitting || cancelling || loading || uploads > 0);
function syncNavButtons() {
  const unavailable = (delta) => !viewHistory[viewIndex + delta] || navigationBlocked(viewHistory[viewIndex + delta]);
  $("nav-back").disabled = unavailable(-1);
  $("nav-forward").disabled = unavailable(1);
}
function recordView({ button, legacy, ...view }) {
  const base = currentBaseView();
  if (!viewHistory.length) [viewHistory, viewIndex] = [[base], 0];
  // The first send creates the conversation outside navigate(); only that Home entry is corrected here.
  // Any other entry may be a Back target whose load is still pending, while `conversation` is stale.
  if (viewHistory[viewIndex].kind === "home") viewHistory[viewIndex] = base;
  if (!sameView(viewHistory[viewIndex], view)) {
    viewHistory.splice(viewIndex + 1, Infinity, view);
    if (viewHistory.length > NAV_LIMIT) viewHistory.shift();
    viewIndex = viewHistory.length - 1;
  }
  syncNavButtons();
}
async function closeViewDialogs(keep) {
  for (const id of new Set(Object.values(DIALOG_VIEWS))) {
    if (id === keep || !$(id).open) continue;
    if (id === "space-dialog" && !(await leavePage())) return false;
    $(id).close();
  }
  return true;
}
function restoreScroll(top) {
  const box = $("messages");
  // "instant": #messages scrolls smoothly, and a restored position must not animate or be re-pinned.
  if (top < 0) {
    // "The end": pin like a followed stream, so content that renders after this jump keeps it at the bottom.
    followingStream = true;
    return scroll();
  }
  box.scrollTo({ top, behavior: "instant" });
  followingStream = box.scrollHeight - box.scrollTop - box.clientHeight < 48;
}
// Resolves false when the view could not be shown (a dialog refused to close, a conversation failed to load).
async function applyView(view, replay = false) {
  if (!(await closeViewDialogs(DIALOG_VIEWS[view.kind]))) return false;
  if (view.kind === "conversation") {
    const top = scrollByConversation.get(view.id);
    // A click on the open conversation reloads it (it may have advanced or been deleted elsewhere);
    // Back or Forward onto it only restores the scroll.
    if (view.id !== conversation || !replay) await load(view.id, view.legacy, null, top);
    else if (top !== undefined) restoreScroll(top);
    if (conversation !== view.id) return false;
  } else if (view.kind === "home") {
    // A click on New chat always starts a fresh one; a replayed Home only leaves the conversation.
    if (conversation || !replay) startNewConversation();
  } else if (view.kind === "space") await openSpace();
  else if (view.kind === "scheduled") await openScheduled();
  else {
    const section = view.kind === "customize" ? "customize" : view.section;
    if (!$("settings-dialog").open) {
      syncThemeToggle();
      $("settings-dialog").showModal();
      refreshCatalog();
    }
    const button = view.button ||
      document.querySelector('[data-settings="' + section + '"]' + (view.sub ? '[data-admin-section="' + view.sub + '"]' : ""));
    if (view.button || button !== pressedSettings()) showSettingsPage(button);
  }
}
async function navigate(view, { record = true } = {}) {
  if (navigationBlocked(view)) return;
  rememberScroll();
  if (record) recordView(view);
  const shown = await applyView(view, !record);
  // A recorded view that did not open must not stay in the history, or the first Back appears to do nothing.
  if (record && shown === false && sameView(viewHistory[viewIndex], view)) {
    viewHistory.splice(viewIndex, 1);
    viewIndex--;
    syncNavButtons();
  }
  return shown;
}
async function stepHistory(delta) {
  const target = viewHistory[viewIndex + delta];
  if (!target || navigationBlocked(target) || foreignModalOpen()) return;
  const from = viewIndex;
  viewIndex += delta;
  syncNavButtons();
  if ((await navigate(target, { record: false })) !== false || viewIndex !== from + delta) return;
  // The view did not open: a dialog refused to close, or the conversation is gone. Keep the current view
  // and drop a dead conversation entry so Back and Forward never point at it again.
  viewIndex = from;
  if (target.kind === "conversation" && !conversations.some((c) => c.id === target.id)) {
    viewHistory.splice(from + delta, 1);
    viewIndex = from + Math.min(delta, 0);
  }
  syncNavButtons();
}
// A modal dialog that is not a history view (About, search, ...) owns the keyboard shortcuts.
const foreignModalOpen = () =>
  [...document.querySelectorAll("dialog[open]")].some((d) => !Object.values(DIALOG_VIEWS).includes(d.id));
const back = () => stepHistory(-1),
  forward = () => stepHistory(1);
$("nav-back").onclick = back;
$("nav-forward").onclick = forward;
document.addEventListener("keydown", (e) => {
  if (!(e.ctrlKey || e.metaKey) || e.altKey || e.shiftKey || !["[", "]"].includes(e.key)) return;
  e.preventDefault();
  (e.key === "[" ? back : forward)();
});
document.addEventListener("keydown", (e) => {
  if (!(e.ctrlKey || e.metaKey) || e.altKey || e.shiftKey || e.key !== "," || !interfaceReady || foreignModalOpen()) return;
  e.preventDefault();
  settingsMenu.matches(":popover-open") && settingsMenu.hidePopover();
  openSettings() || openSettings("appearance");
});
// `#open=settings/<section>` (set by the desktop app) opens Settings at that section.
function openFromHash() {
  const section = /^#open=settings\/([a-z]+)$/.exec(location.hash)?.[1];
  if (!section || !interfaceReady) return;
  window.history.replaceState(null, "", location.pathname + location.search);
  openSettings(section);
}
addEventListener("hashchange", openFromHash);
document.addEventListener("harness:ready", openFromHash);
// Mouse back / forward buttons; the default is cancelled so the browser never leaves the app.
document.addEventListener("mouseup", (e) => {
  if (e.button !== 3 && e.button !== 4) return;
  e.preventDefault();
  (e.button === 3 ? back : forward)();
});
// Closing a view dialog is a navigation to the conversation underneath, unless the history already moved on.
for (const id of new Set(Object.values(DIALOG_VIEWS)))
  $(id).addEventListener("close", () => {
    const current = viewHistory[viewIndex];
    if (document.querySelector("dialog[open]") || (current && !(current.kind in DIALOG_VIEWS))) return;
    recordView(currentBaseView());
  });
// Harness-owned agents: own instructions, purpose, tasks, target output and the
// provider, model and effort they run on; called with @@name in any chat.
let harnessAgents = [],
  editingAgent = null;
const agentFieldInputs = {
  name: "agent-name",
  purpose: "agent-purpose",
  instructions: "agent-instructions",
  tasks: "agent-tasks",
  target_output: "agent-target-output",
  backend: "agent-backend",
  model: "agent-model",
  effort: "agent-effort",
};
function agentRouteLabel(agent) {
  return (
    modelName(agent.model) +
    " · " +
    (providerNames[agent.backend] || agent.backend) +
    (agent.effort && agent.effort !== "configured" ? " · " + agent.effort : "")
  );
}
function applyAgentRoute(item) {
  const target = models.find(
    (m) => m.id === item.model && (!item.backend || m.backend === item.backend),
  );
  if (!target) return;
  if ($("model").value !== target.id) {
    $("model").value = target.id;
    updateEfforts();
    void quota();
  }
  if ([...$("effort").options].some((o) => o.value === item.effort))
    $("effort").value = item.effort;
  rememberSelection();
  updateComposer();
}
// Provider, model and effort pickers shared by agents and scheduled tasks.
function fillRoute(prefix, backend, model, effort) {
  const backendSelect = $(prefix + "-backend"),
    modelSelect = $(prefix + "-model"),
    effortSelect = $(prefix + "-effort"),
    backends = [
      ...new Set(models.filter((m) => m.backend).map((m) => m.backend)),
    ];
  backendSelect.replaceChildren(...backends.map((b) => new Option(providerNames[b] || b, b)));
  backendSelect.value = backends.includes(backend) ? backend : backends[0] || "";
  const choices = models.filter((m) => m.backend === backendSelect.value);
  modelSelect.replaceChildren(...choices.map((m) => new Option(modelName(m.id), m.id)));
  modelSelect.value = choices.some((m) => m.id === model) ? model : choices[0]?.id || "";
  const efforts = choices.find((m) => m.id === modelSelect.value)?.efforts || [];
  effortSelect.replaceChildren(
    ...efforts.map((e) => new Option(e === "configured" ? "Provider's default" : e[0].toUpperCase() + e.slice(1), e)),
  );
  effortSelect.value = efforts.includes(effort) ? effort : efforts[0] || "";
}
const fillAgentRoute = (backend, model, effort) => fillRoute("agent", backend, model, effort);
for (const prefix of ["agent", "schedule"]) {
  $(prefix + "-backend").onchange = () => fillRoute(prefix, $(prefix + "-backend").value, "", "");
  $(prefix + "-model").onchange = () =>
    fillRoute(prefix, $(prefix + "-backend").value, $(prefix + "-model").value, $(prefix + "-effort").value);
}
// Seconds since a timestamp given as UNIX seconds or an ISO string.
function stampSeconds(value) {
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? number : Date.parse(value) / 1000 || 0;
}
function projectChoices(select, value) {
  select.replaceChildren(
    ...[...$("project").options].map(
      (o) => new Option(o.value === "sem-projeto" ? "No project" : o.textContent, o.value),
    ),
  );
  select.value = [...select.options].some((o) => o.value === value) ? value : "sem-projeto";
}
async function confirmTwice(button, label, action) {
  if (!button.dataset.confirm) {
    button.dataset.confirm = "1";
    button.textContent = "Confirm delete";
    return;
  }
  delete button.dataset.confirm;
  button.textContent = label;
  await action();
}

// Space › Pages (Codex Space): Markdown pages kept per project, usable in any chat.
let currentPage = null,
  pageProject = "",
  pageDirty = false,
  pageEdits = 0,
  pageSession = 0,
  pageSaving = Promise.resolve(true);
async function openSpace() {
  if (!$("space-dialog").open) $("space-dialog").showModal();
  // A page that could not be saved on its way out is still here; keep showing it.
  if (pageDirty) return;
  projectChoices($("space-project"), $("project").value);
  showPageEditor(undefined);
  await loadPages();
}
async function loadPages(selectedId = currentPage?.id) {
  try {
    const data = await json("/v1/pages?" + new URLSearchParams({ project_id: $("space-project").value }));
    const pages = Array.isArray(data.pages) ? data.pages : [];
    $("pages-list").replaceChildren(
      ...pages.map((page) => {
        const row = document.createElement("li"),
          open = document.createElement("button"),
          title = document.createElement("strong"),
          meta = document.createElement("small");
        open.type = "button";
        open.className = "page-view-row";
        open.dataset.pageId = page.id;
        if (page.id === selectedId) open.setAttribute("aria-current", "true");
        title.textContent = page.title;
        meta.textContent = "Edited " + usageAge(stampSeconds(page.updated_at));
        open.append(title, meta);
        open.onclick = () => void openPage(page.id);
        row.append(open);
        return row;
      }),
    );
    $("pages-empty").textContent = "No pages in this project yet.";
    $("pages-empty").hidden = pages.length > 0;
  } catch (error) {
    $("pages-list").replaceChildren();
    $("pages-empty").textContent = "Couldn't load pages. " + error.message;
    $("pages-empty").hidden = false;
  }
}
function showPageEditor(page) {
  currentPage = page || null;
  pageProject = $("space-project").value;
  pageDirty = false;
  pageSession++;
  const editing = page !== undefined;
  $("page-empty-state").hidden = editing;
  $("page-editor").hidden = !editing;
  if (!editing) return;
  $("page-title").value = page?.title || "";
  $("page-body").value = page?.body || "";
  $("page-delete").hidden = !page?.id;
  $("page-delete").textContent = "Delete";
  delete $("page-delete").dataset.confirm;
  setPagePreview(false);
  $("page-status").textContent = page?.id
    ? "Saved " + usageAge(stampSeconds(page.updated_at))
    : "New page";
}
function setPagePreview(on) {
  $("page-preview-toggle").setAttribute("aria-pressed", String(on));
  $("page-preview").hidden = !on;
  $("page-body").hidden = on;
  if (on) renderAnswer($("page-preview"), $("page-body").value || "*Empty page*");
}
async function openPage(id) {
  if (!(await leavePage())) return;
  try {
    const page = await json(
      "/v1/pages/" + encodeURIComponent(id) + "?" + new URLSearchParams({ project_id: $("space-project").value }),
    );
    showPageEditor(page);
    await loadPages(page.id);
  } catch (error) {
    status(error.message);
  }
}
function savePage() {
  // One save at a time, so the next one sends the revision the previous one returned.
  pageSaving = pageSaving.then(writePage);
  return pageSaving;
}
async function writePage() {
  const page = currentPage,
    session = pageSession,
    edits = pageEdits,
    body = {
      project_id: pageProject,
      title: $("page-title").value.trim() || "Untitled",
      body: $("page-body").value,
    };
  $("page-save").disabled = true;
  try {
    const saved = page?.id
      ? await json("/v1/pages/" + encodeURIComponent(page.id), {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ...body, revision: page.revision }),
        })
      : await post("/v1/pages", body);
    if (session === pageSession) {
      currentPage = { ...saved, body: saved.body ?? body.body };
      $("page-delete").hidden = false;
      // Text typed while this save was in flight stays unsaved for the next save.
      if (edits === pageEdits) {
        pageDirty = false;
        $("page-title").value = currentPage.title;
        $("page-status").textContent = "Saved";
      }
    }
    await loadPages();
    return true;
  } catch (error) {
    if (session === pageSession) $("page-status").textContent = error.message;
    return false;
  } finally {
    $("page-save").disabled = false;
  }
}
async function leavePage() {
  // False keeps the page, its project and the save error in view.
  while (pageDirty) if (!(await savePage())) return false;
  return true;
}
async function closeSpace() {
  if (await leavePage()) $("space-dialog").close();
}
function pageFile(title, body) {
  const name = title.replace(/[^\w.-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 80) || "page";
  return new File([body], name + ".md", { type: "text/markdown" });
}
// A page is attached as its current text, into the composer's own project (QA-R2-2): the
// composer never changes project just to take a page, and a new chat waits for its permissions.
async function usePage(startChat) {
  if (!(await leavePage())) return;
  const project = $("space-project").value,
    title = $("page-title").value.trim() || "Untitled",
    file = pageFile(title, $("page-body").value);
  $("space-dialog").close();
  if (startChat) newConversation(title, project);
  await refreshProjectPermissions();
  await upload([file]);
  $("prompt").focus({ preventScroll: true });
}
$("page-editor").onsubmit = (event) => {
  event.preventDefault();
  void savePage();
};
$("page-editor").addEventListener("input", () => {
  pageDirty = true;
  pageEdits++;
  $("page-status").textContent = "Unsaved changes";
});
$("page-editor").addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
    event.preventDefault();
    void savePage();
  }
});
$("page-preview-toggle").onclick = () =>
  setPagePreview($("page-preview-toggle").getAttribute("aria-pressed") !== "true");
$("page-new").onclick = async () => {
  if (!(await leavePage())) return;
  showPageEditor(null);
  await loadPages(null);
  $("page-title").focus();
};
$("page-empty-new").onclick = () => $("page-new").click();
$("page-attach").onclick = () => void usePage(false);
$("page-chat").onclick = () => void usePage(true);
$("page-delete").onclick = () =>
  void confirmTwice($("page-delete"), "Delete", async () => {
    try {
      await json("/v1/pages/" + encodeURIComponent(currentPage.id), {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ project_id: $("space-project").value, revision: currentPage.revision }),
      });
      showPageEditor(undefined);
      await loadPages(null);
    } catch (error) {
      $("page-status").textContent = error.message;
    }
  });
$("space-project").onchange = async () => {
  if (!(await leavePage())) {
    $("space-project").value = pageProject;
    return;
  }
  showPageEditor(undefined);
  void loadPages(null);
};
$("space-close").onclick = () => void closeSpace();
$("space-dialog").addEventListener("cancel", (event) => {
  if (!pageDirty) return;
  event.preventDefault();
  void closeSpace();
});

// Scheduled tasks (Codex Scheduled): a prompt that runs unattended on its own
// route as a new conversation each time; only Ask and Read only access.
let currentSchedule = null;
const weekdays = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
function cadenceLabel(cadence = {}) {
  if (cadence.kind === "interval") return "Every " + cadence.hours + " h";
  if (cadence.kind === "weekly") return weekdays[cadence.weekday] + "s at " + cadence.time;
  return "Daily at " + cadence.time;
}
function untilLabel(seconds) {
  const wait = Number(seconds) - Date.now() / 1000;
  if (!Number.isFinite(wait)) return "";
  if (wait <= 60) return "due now";
  return "next in " + (wait < 3600 ? Math.round(wait / 60) + " min" : wait < 86400 ? Math.round(wait / 3600) + " h" : Math.round(wait / 86400) + " d");
}
async function openScheduled() {
  if (!$("scheduled-dialog").open) $("scheduled-dialog").showModal();
  showScheduleEditor(undefined);
  await loadSchedules();
}
async function loadSchedules(selectedId = currentSchedule?.id) {
  try {
    const data = await json("/v1/schedules");
    const schedules = Array.isArray(data.schedules) ? data.schedules : [];
    $("schedules-list").replaceChildren(
      ...schedules.map((task) => {
        const row = document.createElement("li"),
          open = document.createElement("button"),
          title = document.createElement("strong"),
          meta = document.createElement("small");
        open.type = "button";
        open.className = "page-view-row" + (task.enabled ? "" : " paused");
        open.dataset.scheduleId = task.id;
        if (task.id === selectedId) open.setAttribute("aria-current", "true");
        title.textContent = task.title;
        meta.textContent = [
          task.enabled ? "Active" : "Paused",
          cadenceLabel(task.cadence),
          task.enabled ? untilLabel(task.next_run) : task.paused_reason || "",
          task.last_run?.needs_you && "Last run needs you",
        ]
          .filter(Boolean)
          .join(" · ");
        open.append(title, meta);
        open.onclick = () => showScheduleEditor(task);
        row.append(open);
        return row;
      }),
    );
    $("schedules-empty").textContent = "No scheduled tasks yet.";
    $("schedules-empty").hidden = schedules.length > 0;
    return schedules;
  } catch (error) {
    $("schedules-list").replaceChildren();
    $("schedules-empty").textContent = "Couldn't load scheduled tasks. " + error.message;
    $("schedules-empty").hidden = false;
    return [];
  }
}
function syncCadenceFields() {
  const kind = $("schedule-kind").value;
  $("schedule-weekday-field").hidden = kind !== "weekly";
  $("schedule-time-field").hidden = kind === "interval";
  $("schedule-hours-field").hidden = kind !== "interval";
}
function showScheduleEditor(task) {
  currentSchedule = task || null;
  const editing = task !== undefined;
  $("schedule-empty-state").hidden = editing;
  $("schedule-editor").hidden = !editing;
  for (const row of $("schedules-list").querySelectorAll("[aria-current]")) row.removeAttribute("aria-current");
  if (task?.id)
    $("schedules-list").querySelector('[data-schedule-id="' + CSS.escape(task.id) + '"]')?.setAttribute("aria-current", "true");
  if (!editing) return;
  const current = selected(),
    cadence = task?.cadence || { kind: "daily", time: "09:00" };
  $("schedule-title").value = task?.title || "";
  $("schedule-prompt").value = task?.prompt || "";
  projectChoices($("schedule-project"), task?.project_id || $("project").value);
  void fillScheduleContext(task);
  fillRoute("schedule", task?.backend || current?.backend, task?.model || current?.id, task?.effort || $("effort").value);
  $("schedule-kind").value = cadence.kind;
  $("schedule-time").value = cadence.time || "09:00";
  $("schedule-weekday").value = String(cadence.weekday ?? 0);
  $("schedule-hours").value = String(cadence.hours || 6);
  $("schedule-access").value = task?.access_mode || "ask";
  $("schedule-internet").checked = task?.allow_internet === true;
  $("schedule-enabled").checked = task ? !!task.enabled : true;
  syncCadenceFields();
  $("schedule-error").textContent = "";
  for (const el of $("schedule-editor").querySelectorAll("[aria-invalid]")) el.removeAttribute("aria-invalid");
  $("schedule-delete").hidden = $("schedule-run").hidden = !task?.id;
  $("schedule-delete").textContent = "Delete";
  delete $("schedule-delete").dataset.confirm;
  $("schedule-save").textContent = task?.id ? "Save task" : "Create task";
  showLastRun(task);
}
// D41: the agent and the pages are read at run time; the editor only picks them.
const MAX_SCHEDULE_PAGES = 5;
let scheduleContextLoad = 0;
async function fillScheduleContext(task) {
  const session = ++scheduleContextLoad,
    picker = $("schedule-agent"),
    agent = task?.agent || "";
  // Until the list of this task's pages has loaded, a Save keeps the stored choice.
  $("schedule-pages-list").dataset.loaded = "false";
  picker.replaceChildren(new Option("No agent", ""));
  if (agent) picker.append(new Option("@@" + agent, agent));
  picker.value = agent;
  try {
    const data = await json("/v1/harness-agents");
    if (session !== scheduleContextLoad) return;
    const names = (Array.isArray(data.agents) ? data.agents : []).map((item) => item.name);
    picker.replaceChildren(
      new Option("No agent", ""),
      ...[...new Set([...names, ...(agent ? [agent] : [])])].map((name) => new Option("@@" + name, name)),
    );
    picker.value = agent;
  } catch {}
  await renderSchedulePages($("schedule-project").value, task?.page_ids || []);
}
async function renderSchedulePages(project, chosen) {
  const session = scheduleContextLoad,
    box = $("schedule-pages-list");
  let listed = null;
  try {
    listed = (await json("/v1/pages?" + new URLSearchParams({ project_id: project }))).pages;
  } catch {}
  if (session !== scheduleContextLoad) return;
  // A list that did not load keeps the stored choice: saving must not drop pages it never showed.
  box.dataset.loaded = String(Array.isArray(listed));
  if (!Array.isArray(listed) || !listed.length) {
    box.textContent = Array.isArray(listed) ? "No pages in this project yet." : "Couldn't load pages.";
    return;
  }
  box.replaceChildren(
    ...listed.map((page) => {
      const label = document.createElement("label"),
        check = document.createElement("input");
      check.type = "checkbox";
      check.value = page.id;
      check.checked = chosen.includes(page.id);
      label.append(check, document.createTextNode(" " + page.title));
      return label;
    }),
  );
  limitSchedulePages();
}
function limitSchedulePages() {
  const boxes = [...$("schedule-pages-list").querySelectorAll("input")],
    full = boxes.filter((box) => box.checked).length >= MAX_SCHEDULE_PAGES;
  for (const box of boxes) box.disabled = full && !box.checked;
}
function schedulePageIds() {
  const list = $("schedule-pages-list");
  return list.dataset.loaded === "true"
    ? [...list.querySelectorAll("input:checked")].map((box) => box.value)
    : currentSchedule?.page_ids || [];
}
// The last run's real outcome (D15), with a link to its conversation.
function showLastRun(task) {
  const run = task?.last_run;
  if (!run) {
    $("schedule-last").textContent = task?.id ? "Not run yet." : "";
    return;
  }
  const state = run.state === "submitted" ? "running" : run.state;
  $("schedule-last").textContent = ["Last run " + usageAge(stampSeconds(run.at)), state, run.needs_you && "needs you"]
    .filter(Boolean)
    .join(" · ");
  appendOpenRun(run.job_id);
}
// A scheduled run is the first turn of its own conversation.
function appendOpenRun(jobId) {
  if (!jobId) return;
  const open = document.createElement("button");
  open.type = "button";
  open.className = "btn";
  open.textContent = "Open run";
  open.onclick = () => {
    $("scheduled-dialog").close();
    void navigate({ kind: "conversation", id: jobId });
  };
  $("schedule-last").append(" ", open);
}
function scheduleBody() {
  const kind = $("schedule-kind").value;
  return {
    title: $("schedule-title").value.trim(),
    prompt: $("schedule-prompt").value.trim(),
    project_id: $("schedule-project").value,
    backend: $("schedule-backend").value,
    model: $("schedule-model").value,
    effort: $("schedule-effort").value,
    access_mode: $("schedule-access").value,
    allow_internet: $("schedule-internet").checked,
    cadence:
      kind === "interval"
        ? { kind, hours: Number($("schedule-hours").value) }
        : kind === "weekly"
          ? { kind, weekday: Number($("schedule-weekday").value), time: $("schedule-time").value }
          : { kind, time: $("schedule-time").value },
    enabled: $("schedule-enabled").checked,
    agent: $("schedule-agent").value || null,
    page_ids: schedulePageIds(),
  };
}
function showScheduleError(field, message) {
  $("schedule-error").textContent = message;
  const input = $(
    { title: "schedule-title", prompt: "schedule-prompt", agent: "schedule-agent", page_ids: "schedule-pages-list", project_id: "schedule-project", backend: "schedule-backend", model: "schedule-model", effort: "schedule-effort", access_mode: "schedule-access", allow_internet: "schedule-internet", cadence: $("schedule-kind").value === "interval" ? "schedule-hours" : "schedule-time" }[field] || "",
  );
  if (!input) return;
  input.setAttribute("aria-invalid", "true");
  input.focus();
}
$("schedule-kind").onchange = syncCadenceFields;
$("schedule-project").onchange = () => void renderSchedulePages($("schedule-project").value, []);
$("schedule-pages-list").onchange = limitSchedulePages;
$("schedule-editor").addEventListener("input", (event) => {
  if (event.target.getAttribute?.("aria-invalid") !== "true") return;
  event.target.removeAttribute("aria-invalid");
  $("schedule-error").textContent = "";
});
$("schedule-editor").onsubmit = async (event) => {
  event.preventDefault();
  const body = scheduleBody(),
    task = currentSchedule;
  if (!body.title) return showScheduleError("title", "Give the task a title.");
  if (!body.prompt) return showScheduleError("prompt", "Write what the task should do.");
  // D41: an @@name in the prompt must be the agent picked above, as in the composer.
  const stray = [...unfencedPrompt(body.prompt).matchAll(/(?:^|\s)(@@[\w:-]+)(?=\s|$)/g)].find(
    (match) => !body.agent || match[1] !== "@@" + body.agent,
  );
  if (stray) return showScheduleError("agent", "Pick " + stray[1] + " in the Agent field, or remove it from the prompt.");
  if (body.cadence.kind !== "interval" && !/^\d{2}:\d{2}$/.test(body.cadence.time))
    return showScheduleError("cadence", "Choose a time.");
  $("schedule-save").disabled = true;
  try {
    const saved = task?.id
      ? await json("/v1/schedules/" + encodeURIComponent(task.id), {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ...body, revision: task.revision }),
        })
      : await post("/v1/schedules", body);
    const schedules = await loadSchedules(saved.id);
    showScheduleEditor(schedules.find((item) => item.id === saved.id) || saved);
    $("schedule-last").textContent = (task?.id ? "Saved." : "Created.") + " " + (saved.enabled ? untilLabel(saved.next_run) : "Paused.");
  } catch (error) {
    showScheduleError(error.field || "", error.message);
  } finally {
    $("schedule-save").disabled = false;
  }
};
$("schedule-run").onclick = async () => {
  const task = currentSchedule;
  if (!task?.id) return;
  try {
    const started = await post("/v1/schedules/" + encodeURIComponent(task.id) + "/run", {});
    $("schedule-last").textContent = "Started now. It appears in Chats.";
    appendOpenRun(started.job_id);
    void history();
    const fresh = (await loadSchedules(task.id)).find((item) => item.id === task.id);
    // The run rewrote the stored task, so Save and Delete need its new revision (CDX-R1-4);
    // the editor fields stay as typed.
    if (fresh && currentSchedule?.id === task.id) currentSchedule = fresh;
  } catch (error) {
    showScheduleError("", error.message);
  }
};
$("schedule-delete").onclick = () =>
  void confirmTwice($("schedule-delete"), "Delete", async () => {
    try {
      await json("/v1/schedules/" + encodeURIComponent(currentSchedule.id), {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ revision: currentSchedule.revision }),
      });
      showScheduleEditor(undefined);
      await loadSchedules(null);
    } catch (error) {
      showScheduleError("", error.message);
    }
  });
$("schedule-new").onclick = () => {
  showScheduleEditor(null);
  $("schedule-title").focus();
};
$("schedule-empty-new").onclick = () => $("schedule-new").click();
$("scheduled-close").onclick = () => $("scheduled-dialog").close();
// The drawer repeats Space and Scheduled for the phone layout, where the rail hides them.
for (const id of ["rail-space", "sidebar-space"]) $(id).onclick = () => void navigate({ kind: "space" });
for (const id of ["rail-scheduled", "sidebar-scheduled"]) $(id).onclick = () => void navigate({ kind: "scheduled" });
function openAgentDialog(agent = null) {
  editingAgent = agent;
  $("agent-dialog-title").textContent = agent ? "Edit @@" + agent.name : "Create agent";
  $("agent-save").textContent = agent ? "Save agent" : "Create agent";
  $("agent-delete").hidden = !agent;
  $("agent-delete").textContent = "Delete agent";
  delete $("agent-delete").dataset.confirm;
  $("agent-name").value = agent?.name || "";
  $("agent-name").readOnly = !!agent;
  $("agent-purpose").value = agent?.purpose || "";
  $("agent-instructions").value = agent?.instructions || "";
  $("agent-tasks").value = (agent?.tasks || []).join("\n");
  $("agent-target-output").value = agent?.target_output || "";
  const current = selected();
  fillAgentRoute(
    agent?.backend || current?.backend,
    agent?.model || current?.id,
    agent?.effort || $("effort").value,
  );
  $("agent-form-error").textContent = "";
  for (const el of $("agent-form").querySelectorAll("[aria-invalid]"))
    el.removeAttribute("aria-invalid");
  $("agent-dialog").showModal();
  (agent ? $("agent-purpose") : $("agent-name")).focus();
}
function agentFormBody() {
  return {
    name: $("agent-name").value.trim(),
    purpose: $("agent-purpose").value.trim(),
    instructions: $("agent-instructions").value.trim(),
    tasks: $("agent-tasks").value.split("\n").map((t) => t.trim()).filter(Boolean),
    target_output: $("agent-target-output").value.trim(),
    backend: $("agent-backend").value,
    model: $("agent-model").value,
    effort: $("agent-effort").value,
  };
}
function agentFormProblem(body) {
  if (!/^[a-z0-9][a-z0-9-]{1,47}$/.test(body.name))
    return ["name", "Use 2 to 48 lowercase letters, digits or hyphens, starting with a letter or digit."];
  if (!body.purpose) return ["purpose", "Describe what the agent is for."];
  if (!body.instructions) return ["instructions", "Write the agent's instructions."];
  if (body.tasks.length > 12 || body.tasks.some((t) => t.length > 200))
    return ["tasks", "Use up to 12 tasks of at most 200 characters each."];
  if (!body.backend || !body.model) return ["model", "Choose a provider and a model."];
  return null;
}
function showAgentError(field, message) {
  $("agent-form-error").textContent = message;
  const input = $(agentFieldInputs[field]);
  if (!input) return;
  input.setAttribute("aria-invalid", "true");
  input.focus();
}
$("agent-form").onsubmit = async (event) => {
  event.preventDefault();
  const body = agentFormBody(),
    agent = editingAgent;
  $("agent-form-error").textContent = "";
  for (const el of $("agent-form").querySelectorAll("[aria-invalid]"))
    el.removeAttribute("aria-invalid");
  const problem = agentFormProblem(body);
  if (problem) return showAgentError(...problem);
  $("agent-save").disabled = true;
  try {
    const saved = agent
      ? await json("/v1/harness-agents/" + encodeURIComponent(agent.id), {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ...body, revision: agent.revision }),
        })
      : await post("/v1/harness-agents", body);
    $("agent-dialog").close();
    clearResourceItems();
    await loadHarnessAgents();
    status((agent ? "Saved" : "Created") + " @@" + (saved?.name || body.name) + ".");
  } catch (error) {
    showAgentError(error.field || "", error.message);
  } finally {
    $("agent-save").disabled = false;
  }
};
$("agent-delete").onclick = async () => {
  const button = $("agent-delete"),
    agent = editingAgent;
  if (!agent) return;
  if (!button.dataset.confirm) {
    button.dataset.confirm = "1";
    button.textContent = "Confirm delete";
    return;
  }
  try {
    await json("/v1/harness-agents/" + encodeURIComponent(agent.id), {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ revision: agent.revision }),
    });
    $("agent-dialog").close();
    clearResourceItems();
    await loadHarnessAgents();
    status("Deleted @@" + agent.name + ".");
  } catch (error) {
    showAgentError("", error.message);
  }
};
$("agent-form").addEventListener("input", (event) => {
  if (event.target.getAttribute("aria-invalid") !== "true") return;
  event.target.removeAttribute("aria-invalid");
  $("agent-form-error").textContent = "";
});
$("agent-cancel").onclick = $("agent-dialog-close").onclick = () => $("agent-dialog").close();
$("agent-create").onclick = () => openAgentDialog();
async function loadHarnessAgents() {
  try {
    const data = await json("/v1/harness-agents");
    harnessAgents = Array.isArray(data.agents) ? data.agents : [];
    $("harness-agents-empty").textContent =
      "No agents yet. Create one to give a task its own instructions, provider and model.";
  } catch {
    harnessAgents = [];
    $("harness-agents-empty").textContent = "Couldn't load your agents.";
  }
  renderHarnessAgents();
}
function renderHarnessAgents() {
  $("harness-agents-list").replaceChildren(
    ...harnessAgents.map((agent) => {
      const row = document.createElement("li"),
        text = document.createElement("div"),
        title = document.createElement("strong"),
        purpose = document.createElement("p"),
        route = document.createElement("small"),
        use = document.createElement("button"),
        edit = document.createElement("button");
      row.className = "harness-agent";
      title.textContent = "@@" + agent.name;
      purpose.textContent = agent.purpose;
      route.textContent =
        agent.available === false
          ? agent.unavailable_reason || "Its model is not available now."
          : "Runs on " + agentRouteLabel(agent);
      text.append(title, purpose, route);
      use.type = edit.type = "button";
      use.className = edit.className = "btn";
      use.textContent = "Use";
      use.setAttribute("aria-label", "Use @@" + agent.name + " in the message");
      use.disabled = agent.available === false;
      use.onclick = () => void useHarnessAgent(agent);
      edit.textContent = "Edit";
      edit.setAttribute("aria-label", "Edit @@" + agent.name);
      edit.onclick = () => openAgentDialog(agent);
      row.append(providerModelIcon(agent.backend, agent.model), text, use, edit);
      return row;
    }),
  );
  $("harness-agents-empty").hidden = harnessAgents.length > 0;
}
// "Use" selects the agent like the palette does, so the message carries its revision.
async function useHarnessAgent(agent) {
  $("settings-dialog").close();
  const input = $("prompt"),
    at = input.selectionStart ?? input.value.length,
    before = input.value.slice(0, at),
    spacer = before && !/\s$/.test(before) ? " " : "";
  input.focus();
  input.setRangeText(spacer + "@@", at, input.selectionEnd ?? at, "end");
  const trigger = { prefix: "@@", query: "", start: input.selectionStart - 2, end: input.selectionStart },
    m = resourceEngine();
  try {
    const data = await json(
      "/v1/resources?" +
        new URLSearchParams({
          project_id: $("project").value,
          backend: m.backend,
          model: m.model,
          execution_mode: m.execution_mode,
        }),
    );
    const item = (data.items || []).find((i) => i.resource_id === "harness/agents/" + agent.id);
    if (!item) throw Error("@@" + agent.name + " is not available here.");
    selectResource(item, trigger);
  } catch (error) {
    status(error.message);
  }
}
// Rail shortcuts (Codex model): the run pipeline and the agent and skill catalog.
$("rail-runs").onclick = () => $("run-status-toggle")?.click();
$("rail-agents").onclick = () => void navigate({ kind: "customize" });
$("settings-tour").onclick = () => $("settings-dialog").close();
let quotaReturnsToSettings = false;
$("settings-quota").onclick = () => {
  quotaReturnsToSettings = true;
  $("settings-dialog").close();
  setQuotaOpen(true);
};
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
let projectFileSearch = [],
  projectFileSearchQuery = "",
  projectFileSearchRequest = 0,
  projectFileSearchTimer = 0;
// Prompts and answers are searched on the server; only conversations with a snippet count.
let conversationContent = [],
  conversationContentQuery = "",
  conversationContentRequest = 0;
async function refreshConversationContentSearch(value) {
  const normalized = normalizeSearch(value.trim()),
    request = ++conversationContentRequest;
  conversationContent = [];
  conversationContentQuery = "";
  if (normalized.length >= 2) {
    try {
      const data = await json("/v1/conversations?" + new URLSearchParams({ q: value.trim() }));
      if (request !== conversationContentRequest) return;
      conversationContent = (data.conversations || []).filter((c) => c.snippet);
      conversationContentQuery = normalized;
    } catch {
      if (request !== conversationContentRequest) return;
    }
  }
  renderConversationSearch();
}
async function refreshProjectFileSearch(value) {
  const query = value.trim(),
    normalized = normalizeSearch(query),
    request = ++projectFileSearchRequest;
  // A project without a folder has no files to search (the server answers 422).
  if (normalized.length < 2 || !projectDetails[$("project").value]?.root) {
    projectFileSearch = [];
    projectFileSearchQuery = "";
    renderConversationSearch();
    return;
  }
  try {
    const params = new URLSearchParams({
      project_id: $("project").value,
      query,
      start: "1",
      limit: "100",
    });
    const data = await json("/v1/project-files?" + params);
    if (request !== projectFileSearchRequest) return;
    projectFileSearch = Array.isArray(data.entries) ? data.entries : [];
    projectFileSearchQuery = normalized;
    renderConversationSearch();
  } catch {
    if (request !== projectFileSearchRequest) return;
    projectFileSearch = [];
    projectFileSearchQuery = normalized;
    renderConversationSearch();
  }
}
function renderConversationSearch() {
  const query = normalizeSearch($("conversation-search").value.trim());
  const includes = (...values) =>
    !query || normalizeSearch(values.filter(Boolean).join(" ")).includes(query);
  const observedConversationIds = new Set();
  const observedRuns = observedActivityJobs.map((item) => {
    const source = conversations.find((conversation) => conversation.id === item.conversation_id);
    if (item.conversation_id) observedConversationIds.add(item.conversation_id);
    return {
      ...source,
      id: item.conversation_id || source?.id,
      runId: item.job_id,
      title: source?.title || item.title || "Run " + (item.job_id || ""),
      project: item.project_id || source?.project,
      state: item.state || source?.state,
      wait_reason: item.wait_reason || source?.wait_reason,
      activity: item.work_item || source?.activity,
      execution: {
        ...source?.execution,
        backend: item.backend || source?.execution?.backend,
        model: item.model || source?.execution?.model,
      },
    };
  });
  const searchableRuns = [
    ...observedRuns,
    ...conversations.filter((item) => !observedConversationIds.has(item.id)),
  ];
  const snippets = new Map(
    conversationContentQuery === query ? conversationContent.map((c) => [c.id, c.snippet]) : [],
  );
  const runMatches = searchableRuns.filter((c) =>
    includes(
      c.title || "Conversation",
      c.runId,
      c.state,
      c.wait_reason,
      c.activity,
      c.execution?.backend,
      c.execution?.model,
      projectDetails[c.project]?.label,
    ),
  ).map((c) => ({ ...c, snippet: snippets.get(c.id) || "" }));
  const matchedIds = new Set(runMatches.map((c) => c.id));
  for (const c of conversationContent)
    if (snippets.has(c.id) && !matchedIds.has(c.id))
      runMatches.push({ ...conversations.find((item) => item.id === c.id), ...c });
  const loadedFiles = new Map();
  for (const file of files)
    if (file?.name) loadedFiles.set(file.name, file);
  for (const entries of fileTree.cache.values())
    for (const file of entries || [])
      if (file?.name && file.type !== "directory") loadedFiles.set(file.path || file.name, file);
  if (projectFileSearchQuery === query)
    for (const file of projectFileSearch)
      if (file?.name && file.type !== "directory")
        loadedFiles.set(file.path || file.name, file);
  const fileMatches = [...loadedFiles.entries()].filter(([path, file]) =>
    includes(path, file.name),
  );
  const total = runMatches.length + fileMatches.length;
  $("search-clear").hidden = !query;
  $("search-results").textContent = total
    ? total + " result(s) found"
    : query
      ? "No run or loaded file matched."
      : "No runs or loaded files are available.";
  const sections = [];
  const group = (name, items) => {
    if (!items.length) return;
    const section = document.createElement("section");
    section.className = "search-result-group";
    const heading = document.createElement("h3");
    heading.textContent = name + " · " + items.length;
    section.append(heading, ...items);
    sections.push(section);
  };
  group(
    "Runs",
    runMatches.map((c) => {
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
      if (c.execution?.backend && providerNames[c.execution.backend])
        detail.append(document.createTextNode(" · " + providerNames[c.execution.backend]));
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
      const updated = conversationUpdated(c);
      if (updated) detail.append(document.createTextNode(" · " + new Date(updated * 1000).toLocaleDateString()));
      const indicator = conversationIndicator(c);
      if (indicator) title.prepend(indicator);
      button.append(title, detail);
      if (c.snippet) {
        const excerpt = document.createElement("small");
        excerpt.className = "search-snippet";
        excerpt.textContent = c.snippet;
        button.append(excerpt);
      }
      button.disabled = submitting || cancelling || uploads > 0;
      button.onclick = async () => {
        if (submitting || cancelling || uploads) return;
        $("conversation-search-dialog").close();
        await navigate({ kind: "conversation", id: c.id, legacy: c.legacy });
        if (c.runId) window.runConsole?.openRun(c.runId);
      };
      return button;
    }),
  );
  group(
    "Project files",
    fileMatches.map(([path]) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "conversation-search-result";
      const title = document.createElement("strong");
      title.textContent = path;
      const detail = document.createElement("small");
      detail.textContent = "Authorized project file";
      button.append(title, detail);
      button.onclick = () => {
        $("conversation-search-dialog").close();
        setPanelView("files");
        selectAccordionSection("project-files");
        setPanelOpen(true);
      };
      return button;
    }),
  );
  $("conversation-search-list").replaceChildren(...sections);
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
$("conversation-search").addEventListener("input", () => {
  renderConversationSearch();
  clearTimeout(projectFileSearchTimer);
  projectFileSearchTimer = setTimeout(
    () => {
      refreshProjectFileSearch($("conversation-search").value);
      refreshConversationContentSearch($("conversation-search").value);
    },
    180,
  );
});
$("search-clear").onclick = () => {
  $("conversation-search").value = "";
  renderConversationSearch();
  $("conversation-search").focus();
};
let composerWidth = 0;
new ResizeObserver(entries => {
  const width = entries[0].contentRect.width;
  if (width === composerWidth) return;
  composerWidth = width;
  updateComposer();
}).observe($("prompt"));
// QA-R4-3: the server refuses a request above this many UTF-8 bytes; say so before Enter, in bytes.
const PROMPT_BYTE_LIMIT = 150000;
const PROMPT_WARN_BYTES = PROMPT_BYTE_LIMIT * 0.8;
const utf8 = new TextEncoder();
function syncDraftLimit(value) {
  const note = $("draft-limit");
  // UTF-8 takes at most 3 bytes per UTF-16 unit, so a short draft needs no exact count.
  const bytes = value.length * 3 < PROMPT_WARN_BYTES ? 0 : utf8.encode(value).length;
  const over = bytes > PROMPT_BYTE_LIMIT;
  note.hidden = bytes < PROMPT_WARN_BYTES;
  note.dataset.over = String(over);
  note.textContent = note.hidden
    ? ""
    : over
      ? "This message is " + (bytes - PROMPT_BYTE_LIMIT).toLocaleString("en-US") + " bytes over the " +
        PROMPT_BYTE_LIMIT.toLocaleString("en-US") + "-byte limit. Shorten it or attach it as a file."
      : bytes.toLocaleString("en-US") + " / " + PROMPT_BYTE_LIMIT.toLocaleString("en-US") + " bytes";
  return over;
}
// The spoken character count is computed once typing pauses: counting code points of a long draft per key is slow.
const CHARACTER_COUNT_DELAY_MS = 250;
let characterCountTimer = 0;
function scheduleCharacterCount() {
  clearTimeout(characterCountTimer);
  characterCountTimer = setTimeout(() => {
    const count = Array.from($("prompt").value).length;
    $("character-count").textContent = count.toLocaleString("en-US") + (count === 1 ? " character" : " characters");
  }, CHARACTER_COUNT_DELAY_MS);
}
// UX-R1-4: before a message goes to another provider, say that the conversation goes along.
function syncRouteCarryover() {
  const note = $("route-carryover"),
    next = selected(),
    switching = lastRoute?.backend && next?.backend && lastRoute.backend !== next.backend;
  note.hidden = !switching;
  note.replaceChildren();
  if (!switching) return;
  note.append(
    "Next message goes to " + (providerNames[next.backend] || next.backend) + " · " + modelName(next.id) +
      ". The conversation so far goes with it.",
  );
  for (const line of carryoverToolLines(lastRoute.backend, next))
    note.append(Object.assign(document.createElement("span"), { className: "route-carryover-tool", textContent: line }));
}
// D-031: a model that cannot read images never receives one; the composer says so before sending.
let imageRefusedModel = "";
const imageRefusalCodes = new Set(["model_images_unavailable", "images_require_native_service", "local_vision_not_enabled"]);
const imageUnreadableCopy = () =>
  modelName(selected()?.id) + " can't read images. Choose a model that reads images, or remove the image.";
function syncImageWarning() {
  const m = selected(), hasImage = files.some((f) => f.preview_url);
  const refused = !!m && imageRefusedModel === m.id;
  const shown = !!m && ((hasImage && m.capabilities?.images === false) || refused);
  $("image-capability-warning").hidden = !shown;
  $("image-capability-remove").hidden = !hasImage;
  if (shown) $("image-capability-text").textContent = imageUnreadableCopy();
  // A refusal alone is information: only an attached image blocks sending.
  return shown && hasImage;
}
$("image-capability-choose").onclick = () => $("model-trigger").click();
$("image-capability-remove").onclick = () => {
  files = files.filter((f) => !f.preview_url);
  imageRefusedModel = "";
  renderFiles();
  saveView();
  renderProjectFileTree();
  updateComposer();
  $("prompt").focus();
};
function updateComposer() {
  syncComposerProjectButton();
  syncViewSwitch();
  syncComposerPickers();
  syncRouteCarryover();
  void refreshElsewhere();
  syncExecutionMode();
  updateModelPermissions();
  const blocked = syncComposerAvailability();
  const imagesBlocked = syncImageWarning();
  const prompt = $("prompt");
  prompt.style.height = "auto";
  prompt.style.height = Math.min(prompt.scrollHeight, 170) + "px";
  renderPromptHighlights();
  const overLimit = syncDraftLimit(prompt.value);
  scheduleCharacterCount();
  const hasPrompt = !!prompt.value.trim();
  $("send").hidden = busy && !hasPrompt;
  $("cancel").hidden = !busy;
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
    blocked ||
    submitting ||
    cancelling ||
    loading ||
    uploads > 0 ||
    policyPending ||
    !selected() ||
    !prompt.value.trim() ||
    overLimit ||
    imagesBlocked ||
    !supportedExecutionModes().includes(executionMode) ||
    cooldown > 0;
}
function updateLatest() {
  const box = $("messages");
  const latest = $("latest-message");
  latest.hidden = box.scrollHeight - box.scrollTop - box.clientHeight < 150;
  latest.classList.toggle("latest-message-compact", box.clientHeight < 160);
  latest.classList.toggle("latest-message-inline", box.clientHeight < 24);
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
  followingStream = true;
  updateLatest();
}
$("latest-message").onclick = jumpToLatest;
new ResizeObserver(updateLatest).observe($("messages"));
function setQuotaOpen(open) {
  if (open) {
    closeSidebar();
    if (innerWidth < 1000) setPanelOpen(false, false);
    window.runConsole?.closeForPanel();
    syncWorkspaceModal();
  }
  $("quota-panel").hidden = !open;
  $("quota-toggle").setAttribute("aria-expanded", String(open));
  if (!open && quotaFocus) {
    quotaFocus = null;
    void quota();
  }
  if (open) $("quota-refresh").focus({ preventScroll: true });
  if (open && quotaViewBackend() === "deepseek") void quota();
}
document.addEventListener("pointerdown", (e) => {
  if (!e.target.closest("#quota-panel, #quota-toggle")) setQuotaOpen(false);
});
document.addEventListener("keydown", (e) => {
  if (
    e.defaultPrevented ||
    !interfaceReady ||
    document.querySelector("dialog[open]") ||
    document.querySelector("[popover]:popover-open")
  )
    return;
  if (
    (e.ctrlKey || e.metaKey) &&
    !e.altKey &&
    (e.key === "/" || e.key.toLowerCase() === "k")
  ) {
    e.preventDefault();
    if (e.key === "/") {
      if (innerWidth <= 620 && $("sidebar").classList.contains("open") || innerWidth < 1000 && !$("activity-panel").hidden || (innerWidth <= 700 || innerHeight <= 500) && document.querySelector("#run-console:not([hidden])")) return;
      $("prompt").focus();
    }
    else openConversationSearch();
  }
  if (e.key === "Escape") {
    if ($("attention-popover").hidden && $("quota-panel").hidden && document.querySelector("#run-console:not([hidden])")) return;
    if (!$("attention-popover").hidden) {
      $("attention-popover").hidden = true;
      $("attention-bell").setAttribute("aria-expanded", "false");
      $("attention-bell").focus();
      e.preventDefault();
      return;
    }
    if (!$("quota-panel").hidden) {
      e.preventDefault();
      const meter = [...$("provider-quotas").querySelectorAll(".provider-quota-meter")]
        .find((node) => node.dataset.provider === quotaFocus);
      setQuotaOpen(false);
      if (quotaReturnsToSettings) {
        quotaReturnsToSettings = false;
        $("settings-dialog").showModal();
        $("settings-quota").focus();
      } else (meter || $("settings")).focus();
    } else if (innerWidth <= 620 && $("sidebar").classList.contains("open")) {
      e.preventDefault();
      closeSidebar();
      $("menu").focus();
    } else if (!$("activity-panel").hidden) {
      e.preventDefault();
      setPanelOpen(false);
      $("panel-toggle").focus();
    } else if ($("sidebar").classList.contains("open")) {
      e.preventDefault();
      closeSidebar();
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
  prefs.set("reading_size", size);
}
applyReadingSize(prefs.get("reading_size", "15"));
$("reading-size").onchange = () => applyReadingSize($("reading-size").value);
$("visual-markers-toggle").checked = visualMarkersOn();
$("visual-markers-toggle").onchange = (event) => {
  prefs.set("visual_markers", event.target.checked);
  refreshVisualMarkers();
};
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
const gateChoiceKey = id => "gate-choice-draft:" + id;
function pendingGateStatus() {
  const gate = document.querySelector('.gate-card[data-state="pending"]');
  if (!gate) return;
  const text = gate.dataset.publish === "true" ? "Waiting for publication approval" : "Waiting for your choice";
  status(text);
  $("activity-state").textContent = text;
  if (active) { active.chip.textContent = text; setActivitySummary(active, text); }
}
function finishGate(id, state, data = {}) {
  retireDraft(gateChoiceKey(id));
  const box = document.getElementById("gate-" + id);
  if (!box) return;
  if (box.contains(document.activeElement) || box.dataset.restoreFocus === "true")
    $("prompt").focus({ preventScroll: true });
  box.dataset.state = state;
  if (box.classList.contains("maestro-plan-card")) {
    if (data.choice) box.dataset.choice = data.choice;
    renderPlanOutcome(box);
    return;
  }
  if (state === "resolved") {
    const choices = data.choice === undefined ? [] : Array.isArray(data.choice) ? data.choice : [data.choice];
    box.querySelectorAll("input").forEach(input => { input.checked = choices.includes(input.value); });
  }
  box.querySelectorAll("button,input").forEach(node => { node.disabled = true; });
  const title = box.querySelector("h3");
  if (title) title.textContent = state === "resolved"
    ? (box.dataset.publish === "true" ? (data.choice === "deny" ? "Publication denied" : data.choice === "approve" ? "Publication approved" : "Publication decision recorded") : "Answered")
    : box.dataset.publish === "true" ? (state === "invalidated" ? "Publication approval closed" : "Publication approval expired")
    : state === "invalidated" ? "Question closed" : "Question expired";
  const note = box.querySelector('[role="status"]');
  if (state === "resolved" && data.choice === undefined && box.dataset.publish !== "true") {
    title.textContent = "Answered in another session";
    note.textContent = "The recorded answer is not available yet. Reload to see it.";
    return;
  }
  note.textContent = state === "resolved"
    ? (box.dataset.publish === "true" ? (data.choice === "deny" ? "Publication denied" : data.choice === "approve" ? "Publication approved" : "Publication decision recorded") : "Answered") + (data.resolved_by ? " by " + data.resolved_by : "") + "."
    : box.dataset.publish === "true" ? "This publication approval is no longer active; ask again for a fresh approval before publishing."
    : state === "invalidated"
      ? "This question is no longer active. Send a message to ask again."
      : "This question expired. Send a message to ask again.";
}
function restoreGates(gates = []) {
  for (const gate of Array.isArray(gates) ? gates : []) {
    if (!gate?.gate_id) continue;
    showGate(gate);
    if (gate.state && gate.state !== "pending")
      finishGate(gate.gate_id, gate.state, gate);
  }
}
function showGate(data) {
  if (document.getElementById("gate-" + data.gate_id)) return;
  if (data.kind === "maestro_plan" && data.plan?.steps) {
    showMaestroPlan({ ...data.plan, gate_id: data.gate_id, state: "pending" });
    return;
  }
  if (data.publish && data.effect_id) { showPublishGate(data); return; }
  const box = document.createElement("section"), title = document.createElement("h3"),
    note = document.createElement("p"), fields = document.createElement("fieldset"),
    legend = document.createElement("legend"), submit = document.createElement("button");
  box.id = "gate-" + data.gate_id;
  box.className = "approval-card gate-card";
  box.dataset.publish = String(!!data.publish);
  box.dataset.state = "pending";
  title.textContent = "Your choice is needed";
  legend.textContent = data.question;
  fields.append(legend);
  for (const option of data.options || []) {
    const label = document.createElement("label"), input = document.createElement("input"),
      text = document.createElement("span"), description = document.createElement("small");
    input.type = data.multi_select ? "checkbox" : "radio";
    input.name = "gate-choice-" + data.gate_id;
    input.value = option.id;
    input.checked = (readDraft(gateChoiceKey(data.gate_id)) || []).includes(option.id);
    text.textContent = option.label;
    description.textContent = option.description || "";
    label.append(input, text, description);
    fields.append(label);
  }
  note.setAttribute("role", "status");
  submit.className = "btn";
  submit.textContent = "Confirm choice";
  submit.disabled = !fields.querySelector("input:checked");
  fields.onchange = () => {
    const choices = [...fields.querySelectorAll("input:checked")].map(input => input.value);
    const key = gateChoiceKey(data.gate_id), snapshot = JSON.stringify(choices);
    draftViews.set(key, snapshot);
    unsavedDrafts.set(key, snapshot);
    flushDrafts();
    submit.disabled = !choices.length;
  };
  submit.onclick = async () => {
    if (box.dataset.state !== "pending") return;
    const choices = [...fields.querySelectorAll("input:checked")].map(input => input.value);
    if (!choices.length) return;
    box.dataset.state = "submitting";
    box.dataset.restoreFocus = String(box.contains(document.activeElement));
    box.querySelectorAll("button,input").forEach(node => { node.disabled = true; });
    note.textContent = "Sending your choice…";
    try {
      const result = await post("/v1/approvals/" + data.gate_id, { choice: data.multi_select ? choices : choices[0] });
      finishGate(data.gate_id, "resolved", { choice: data.multi_select ? choices : choices[0], ...result });
    } catch (error) {
      if (error.code === "gate_already_resolved") finishGate(data.gate_id, "resolved");
      else if (["gate_invalidated", "gate_expired"].includes(error.code)) finishGate(data.gate_id, error.code.slice(5));
      else if (box.dataset.state === "submitting") {
        box.dataset.state = "pending";
        note.textContent = "Couldn't send your choice. " + error.message;
        box.querySelectorAll("button,input").forEach(node => { node.disabled = false; });
        if (box.dataset.restoreFocus === "true") submit.focus();
      }
    } finally { delete box.dataset.restoreFocus; }
  };
  box.append(title, fields, submit, note);
  if (data.publish) appendPublishEvidence(box, data);
  $("messages").append(box);
  pendingGateStatus();
  box.scrollIntoView({ block: "nearest" });
}

// OP-R1-21: "mediated" and "unenforced" are protocol words; say what they mean for the user.
function publicationLabel(enforcement) {
  return enforcement === "mediated" ? "Sent through KeepHarness" : "Not controlled by KeepHarness";
}
function appendPublishEvidence(container, data) {
  const evidence = document.createElement("div");
  evidence.className = "publish-evidence";
  evidence.tabIndex = 0;
  evidence.setAttribute("role", "region");
  evidence.setAttribute("aria-label", "Publication evidence");
  const add = (label, value, pre = false) => {
    if (value == null) return;
    const node = document.createElement(pre ? "pre" : "p");
    node.textContent = label + ": " + (typeof value === "string" ? value : JSON.stringify(value, null, 2));
    evidence.append(node);
  };
  add("Operation", data.operation);
  add("Destination", data.destination);
  add("Publication", publicationLabel(data.enforcement));
  add("Risk", data.risk);
  add("Integration", data.integration);
  add("Jira site", data.endpoint);
  add("Evidence", data.evidence, true);
  add("Arguments", data.arguments, true);
  add("Arguments digest", data.arguments_digest);
  add("Artifact preview", data.artifact_preview, true);
  add("Artifact digest", data.artifact_digest);
  add("Approval", "An enrolled human session is required. This decision applies once to this exact request and artifact.");
  container.append(evidence);
}

function showPublishGate(data) {
  const box = document.createElement("section"), title = document.createElement("h3"), note = document.createElement("p");
  box.id = "gate-" + data.gate_id;
  box.className = "approval-card gate-card publish-gate-card";
  box.dataset.tour = "publish-gate";
  box.dataset.state = "pending";
  box.dataset.publish = "true";
  title.textContent = "Publish approval";
  box.append(title);
  appendPublishEvidence(box, data);
  note.setAttribute("role", "status");
  for (const [choice, label] of [["approve", "Approve"], ["deny", "Deny"]]) {
    const action = document.createElement("button");
    action.className = "btn";
    action.type = "button";
    action.textContent = label;
    action.onclick = async () => {
      if (box.dataset.state !== "pending") return;
      box.dataset.state = "submitting";
      box.dataset.restoreFocus = String(box.contains(document.activeElement));
      box.querySelectorAll("button").forEach(node => { node.disabled = true; });
      note.textContent = "Sending your decision…";
      try {
        const result = await post("/v1/approvals/" + encodeURIComponent(data.gate_id), { choice });
        if (box.dataset.state === "submitting") finishGate(data.gate_id, "resolved", result);
      } catch (error) {
        if (box.dataset.state !== "submitting") return;
        if (error.code === "gate_already_resolved") finishGate(data.gate_id, "resolved");
        else if (["gate_invalidated", "gate_expired"].includes(error.code)) finishGate(data.gate_id, error.code.slice(5));
        else if (box.dataset.state === "submitting") {
          box.dataset.state = "pending";
          note.textContent = "Couldn't send your decision. " + error.message;
          box.querySelectorAll("button").forEach(node => { node.disabled = false; });
          if (box.dataset.restoreFocus === "true") action.focus();
        }
      } finally { delete box.dataset.restoreFocus; }
    };
    box.append(action);
  }
  box.append(note);
  $("messages").append(box);
  status("Waiting for publication approval");
  box.scrollIntoView({ block: "nearest" });
}

function expireApproval(id) {
  const box = document.getElementById("approval-" + id);
  if (!box) return;
  if (box.contains(document.activeElement) ||
      (box.dataset.restoreFocus === "true" && document.activeElement === document.body)) {
    $("prompt").focus({ preventScroll: true });
  }
  box.dataset.state = "expired";
  box.querySelector("h3").textContent = "Approval expired";
  box.querySelectorAll("button,input").forEach((node) => (node.disabled = true));
  const progress = box.querySelector('[role="status"]');
  progress.hidden = false;
  progress.textContent = userErrors.approval_expired;
  status("Approval expired");
}

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
    data.request.message ||
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
      .join(" ");
  command.hidden = !command.textContent;
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
      box.dataset.restoreFocus = String(hadFocus);
      box
        .querySelectorAll("button,input")
        .forEach((node) => (node.disabled = true));
      progress.hidden = false;
      progress.textContent = "Sending your decision…";
      try {
        const answers = approved
          ? Object.fromEntries(fields.map(([id, input]) => [id, { answers: [input.value] }]))
          : {};
        await post("/v1/approvals/" + data.approval_id, {
          approved,
          answers,
          scope,
        });
        if (hadFocus) $("prompt").focus({ preventScroll: true });
        if (box.dataset.state !== "expired") box.remove();
      } catch (e) {
        if (e.code === "approval_already_resolved") {
          progress.textContent = e.message;
          box.querySelectorAll("button,input").forEach(node => node.remove());
          if (hadFocus) $("prompt").focus({ preventScroll: true });
        } else if (e.code === "approval_expired") expireApproval(data.approval_id);
        else if (e.code === "approval_session_required" || e.code === "approval_session_expired") {
          // Approvals need an owner-enrolled browser; say how, here and in the status line.
          progress.textContent = e.message;
          box.dataset.enrollment = "required";
          status(e.message);
        } else if (box.dataset.state !== "expired") progress.textContent = "Couldn't confirm your decision. " + e.message;
      } finally {
        deciding = false;
        if (box.dataset.state !== "expired") {
          box.querySelectorAll("button,input").forEach((node) => (node.disabled = false));
          if (hadFocus && box.isConnected) button.focus();
        }
        delete box.dataset.restoreFocus;
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
    send: "Send",
    cancel: "Stop",
    reload: "Reload screen",
  }[id];
  b.replaceChildren(HarnessUI.icon(name));
  if (label) {
    if (["send", "cancel"].includes(id)) {
      const text = document.createElement("span");
      text.textContent = label;
      b.append(text);
    } else b.append(document.createTextNode(label));
  }
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
  button.replaceChildren(HarnessUI.icon(name));
  if (label) button.append(document.createTextNode(label));
}
for (const node of document.querySelectorAll(".brandmark,.welcome-icon"))
  node.replaceChildren(HarnessUI.icon("stack-2"));
for (const button of document.querySelectorAll("[data-settings]")) {
  button.textContent = button.textContent.replace(/^[^A-Za-zÀ-ÿ]+/, "");
  button.prepend(
    HarnessUI.icon(
      button.dataset.settings === "appearance"
        ? "adjustments"
        : button.dataset.settings === "customize"
          ? "stack-2"
          : button.dataset.settings === "models"
            ? "server"
            : button.dataset.settings === "archived"
            ? "archive"
            : button.dataset.settings === "system"
            ? { providers: "plug", home: "pulse", runs: "list", catalogs: "archive", connection: "server" }[
                button.dataset.adminSection
              ]
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
    invalid_pdf: "the PDF is invalid or damaged",
    document_tools_unavailable: "PDF extraction requires bwrap (bubblewrap) on the server",
    pdf_extraction_failed: "its text could not be extracted from the PDF",
    document_text_unavailable: "the document contains no readable text",
    document_text_limit: "the document's text exceeds the size limit",
    audio_decode_failed: "the audio could not be decoded",
    audio_no_speech: "no speech was recognized in the audio",
    audio_duration_limit: "the audio exceeds four hours",
    invalid_audio: "the audio format was not recognized",
    video_decode_failed: "the video frames could not be decoded",
    video_processing_timeout: "video processing exceeded the allowed time",
    image_size_limit: "the image exceeds 100 MiB",
    file_too_large: "it exceeds the 100 MiB per-file limit",
    tool_output_limit: "reading it produced more text than allowed",
    xml_entities_denied: "the XML contains entities that are not allowed",
    selection_scan_limit: "the folder has too many files to scan",
    path_not_authorized: "the path is not authorized for this project",
    invalid_filename:
      "the filename contains a path or control characters, or is too long",
  };
  if (!Object.hasOwn(reasons, code)) return;
  $("welcome")?.remove();
  const notice = assistant("", selected()?.id),
    names = [].concat(filename);
  notice.chip.textContent = "File skipped";
  notice.body.textContent =
    names.length === 1
      ? `File “${names[0]}” was skipped because ${reasons[code]}.`
      : `${names.length} files were skipped because ${reasons[code]}: ${names.map((name) => `“${name}”`).join(", ")}.`;
  $("messages").scrollTop = $("messages").scrollHeight;
}
function attachmentError(code) {
  if (imageRefusalCodes.has(code)) return imageUnreadableCopy() + " The image was not attached.";
  return {
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
    invalid_image: "The image is incomplete or corrupted. It was not attached.",
    image_validation_unavailable:
      "The local image validator is unavailable. The image was not attached.",
    image_validation_timeout:
      "Image validation took too long. Try a smaller image.",
    invalid_filename:
      "The filename contains a path, control characters, or exceeds 160 characters.",
    upload_limit:
      "The file or the project's storage exceeded the allowed limit. Settings › Archived chats shows the storage used.",
    audio_transcription_unavailable:
      "Local audio transcription is not installed on this server.",
    audio_duration_limit: "Upload audio of up to 4 hours.",
    invalid_audio:
      "Couldn't recognize the audio. Try WAV, MP3, M4A, OGG, or FLAC.",
    audio_transcription_failed:
      "Local transcription failed; the audio was not attached.",
    image_capability_unavailable:
      "Couldn't check this server's vision support. Try again once it's available.",
    select_model_for_image: "Choose a model that reads images before attaching one.",
    image_size_limit: "Images can be up to 100 MiB.",
    unsupported_binary_format:
      "This binary format doesn't have a reader available yet. Upload a compatible image, a PDF with text, an Office/OpenDocument document, or a text file.",
    binary_denied: "This file contains binary data with no reader available.",
    invalid_document: "The document is invalid or corrupted.",
    document_expansion_limit:
      "The document exceeds the safe decompression limit.",
    invalid_pdf: "The PDF is invalid or damaged.",
    document_tools_unavailable: "PDF extraction is unavailable. Install bwrap (bubblewrap) on the server.",
    pdf_extraction_failed: "Couldn't extract the text from the PDF.",
    unsafe_document_xml:
      "The document contains XML declarations that are not allowed.",
    xml_entities_denied: "The file contains XML entities that are not allowed.",
    document_text_unavailable: "The document contains no readable text.",
    document_text_limit: "The document's text exceeds the size limit.",
    audio_decode_failed: "Couldn't decode the audio.",
    audio_no_speech: "No speech was recognized in the audio.",
    file_too_large: "The file exceeds the 100 MiB per-file limit.",
    tool_output_limit: "Reading the file produced more text than allowed.",
    attachment_source_unavailable:
      "The file changed or disappeared after it was selected. Select it again.",
    file_limit: "The limit of 20 attachments per message was reached.",
    sensitive_file: "Hidden or sensitive files can't be attached.",
    symlink_denied: "Symbolic links can't be attached.",
    selection_scan_limit:
      "The folder has too many files to scan. Select a smaller folder.",
  }[code];
}

document.addEventListener("click", (event) => {
  document
    .querySelectorAll(".conversation-actions[open]")
    .forEach((actions) => {
      if (!actions.contains(event.target)) actions.open = false;
    });
});

// Shared native popovers for the three concrete composer controls.
// OP-R2-2: one label per access mode, the same in the composer menu and the header chip.
const accessLabel = () => $("access-mode").selectedOptions[0]?.textContent || "Ask for approval";
// D11: the menu offers Full access only to the owner, once turned on in the admin.
function offerFullAccess(offered) {
  fullAccessOffered = offered;
  const option = $("access-menu").querySelector('[data-access="full"]');
  option.hidden = option.disabled = !offered;
  syncAccessMode();
}
function syncAccessMode() {
  if ($("access-mode").value === "full" && !fullAccessOffered)
    $("access-mode").value = "ask";
  const mode = $("access-mode").value;
  $("access-label").textContent = accessLabel();
  $("access-mode-notice").textContent =
    "Access: " + $("access-label").textContent;
  $("access-trigger").dataset.mode = mode;
  $("header-access").textContent = accessLabel();
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
function moreModels(group) {
  let more = group.querySelector(".model-more");
  if (more) return more;
  more = document.createElement("details");
  more.className = "model-more";
  const summary = document.createElement("summary"),
    list = document.createElement("div");
  summary.textContent = "More models";
  list.className = "model-more-options";
  more.append(summary, list);
  group.querySelector(".model-provider-options").append(more);
  return more;
}
// A menu entry is reachable only while every group around it is open.
function menuEntryVisible(entry) {
  let group = (entry.tagName === "SUMMARY" ? entry.parentElement.parentElement : entry).closest("details");
  while (group) {
    if (!group.open) return false;
    group = group.parentElement.closest("details");
  }
  return true;
}
function renderPicker(id) {
  if (id === "access") {
    syncAccessMode();
    return;
  }
  const descriptions = {
    none: "No reasoning step.",
    configured: "Use the provider's configured default.",
    low: "Brief reasoning for simple tasks.",
    medium: "Intermediate reasoning effort.",
    high: "More reasoning for complex tasks.",
    xhigh: "Very high reasoning effort.",
    max: "Maximum effort offered by the model.",
    ultra: "The most intense reasoning level offered by the model.",
  };
  const providers = { ...HarnessUI.providerNames, qwen: HarnessUI.providerNames.local };
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
  // Only the newest model of each Claude family stays on top; older ones sit under "More models".
  const legacy = new Set(),
    families = new Set();
  for (const option of claude) {
    const family = option.value.split("-")[1];
    if (families.has(family)) legacy.add(option.value);
    families.add(family);
  }
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
          heading.append(providerModelIcon(backend));
          const label =
            HarnessUI.providerNames[backend] ||
            model?.backend ||
            "Others";
          group.setAttribute("role", "group");
          group.setAttribute("aria-label", label);
          heading.className = "model-provider-heading";
          heading.append(document.createTextNode(label));
          const chevron = HarnessUI.icon("chevron-left");
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
        const more = legacy.has(option.value) ? moreModels(groups.get(backend)) : null;
        (more?.querySelector(".model-more-options") ||
          groups.get(backend).querySelector(".model-provider-options")
        ).append(button);
        if (option.selected) {
          if (more) more.open = true;
          groups.get(backend).open = true;
          groups
            .get(backend)
            .querySelector("summary")
            .setAttribute("aria-expanded", "true");
        }
      }
      return button;
    });
  // "More models" closes each list.
  for (const group of groups.values()) {
    const more = group.querySelector(".model-more");
    if (more) more.parentElement.append(more);
  }
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
      ].filter(menuEntryVisible),
      index = options.indexOf(document.activeElement);
    if (id === "model" && ["ArrowLeft", "ArrowRight"].includes(event.key)) {
      const group = document.activeElement.closest("details[data-provider]");
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
  const focus = projectTreeFocus(list);
  list.replaceChildren();
  renderProjectFileEntries(list, projectDirectory.roots, projectFolderTree);
  restoreProjectTreeFocus(list, projectFolderTree, focus);
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
            HarnessUI.icon("folder"),
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
  // F-59: re-list on every expand; folders may have changed outside the app.
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
      HarnessUI.icon("home"),
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
    remove.append(HarnessUI.icon("x"), document.createTextNode("Remove"));
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
    HarnessUI.icon(projectId ? "pencil" : "folder-plus"),
    document.createTextNode(projectId ? "Save changes" : "Create project"),
  );
  $("project-create").title = projectId
    ? "Save the name and folders while keeping the conversations"
    : "Create the project; folders are optional";
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
  if (!button.querySelector("svg")) button.prepend(HarnessUI.icon(icon));
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
      $("project").value = current;
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

// The workspace uses the same project/model resource contract as the composer.

async function refreshWorkspaceResources() {
  const target = $("workspace-resources");
  if (!target) return;
  const request = ++workspaceResourceRequest, engine = resourceEngine();
  const project = $("project").value;
  target.textContent = "Loading resources…";
  $("workspace-resources-count").textContent = "0";
  workspaceCatalog.clear();
  if (!engine.backend) {
    target.textContent = "Select a model to see resources.";
    refreshVisualMarkers();
    return;
  }
  try {
    const query = new URLSearchParams({ project_id: project, backend: engine.backend,
      model: engine.model, execution_mode: engine.execution_mode });
    const data = await json("/v1/resources?" + query, { signal: AbortSignal.timeout(5000) });
    if (request !== workspaceResourceRequest) return;
    const items = Array.isArray(data.items) ? data.items : [];
    target.replaceChildren();
    $("workspace-resources-count").textContent = String(items.length);
    for (const item of items) {
      const key = item.kind + ":" + item.name;
      if (!workspaceCatalog.has(key)) workspaceCatalog.set(key, item.id);
      const row = document.createElement("div"), name = document.createElement("span"), badge = document.createElement("span");
      row.className = "workspace-row";
      row.tabIndex = -1;
      row.dataset.resourceId = item.id;
      row.dataset.resourceRevision = item.revision;
      name.textContent = item.name; name.className = "workspace-item-name";
      const catalog = catalogResourceMeta(item);
      row.title = [item.name, item.description, item.kind, item.scope, item.origin, catalog?.title].filter(Boolean).join(" · ");
      badge.className = "workspace-source";
      badge.textContent = [item.scope, item.origin, catalog?.short].filter(Boolean).join(" · ");
      row.append(name, badge); target.append(row);
    }
    refreshVisualMarkers();
    if (pendingResourceFocus) focusResourceRow(pendingResourceFocus);
    pendingResourceFocus = "";
    if (!items.length) target.textContent = "No resources for this project and model.";
    for (const warning of data.warnings || []) {
      const note = document.createElement("p"); note.textContent = warning; target.append(note);
    }
  } catch {
    if (request !== workspaceResourceRequest) return;
    target.textContent = "Couldn't load resources. Change the model or reopen the panel to retry.";
    refreshVisualMarkers();
  }
}
function renderWorkspaceTasks(jobs) {
  const target = $("workspace-background-tasks");
  if (!target) return;
  const active = jobs.filter(item => !["completed", "failed", "cancelled", "interrupted"].includes(item.state));
  target.replaceChildren();
  $("workspace-background-tasks-count").textContent = String(active.length);
  for (const item of active) {
    const row = document.createElement("button"), name = document.createElement("span"), state = document.createElement("span");
    row.type = "button"; row.className = "workspace-row";
    name.className = "workspace-item-name";
    name.textContent = item.title || item.work_item || item.job_id;
    state.className = "workspace-source"; state.textContent = item.state;
    row.title = [name.textContent, item.wait_reason, item.state].filter(Boolean).join(" · ");
    row.onclick = () => window.runConsole?.openRun(item.job_id);
    const identity = document.createElement("span");
    identity.className = "workspace-model"; identity.textContent = item.model || item.backend || "";
    identity.title = [item.backend, item.model].filter(Boolean).join(" / ");
    row.append(providerModelIcon(item.backend, item.model), name, identity, state); target.append(row);
  }
  if (!active.length) target.textContent = "No background tasks.";
}
for (const names of ACCORDION_GROUPS) {
  const heads = names.map((name) => $("workspace-" + name + "-head"));
  heads.forEach((head, index) => {
    head.addEventListener("click", () => {
      if (head.getAttribute("aria-disabled") === "true") return;
      selectAccordionSection(names[index], { focus: true });
    });
    head.addEventListener("keydown", (event) => {
      const target = { ArrowDown: index + 1, ArrowUp: index - 1, Home: 0, End: heads.length - 1 }[event.key];
      if (target === undefined) return;
      event.preventDefault();
      heads[(target + heads.length) % heads.length].focus();
    });
  });
  const sections = prefs.get("workspace_sections", {});
  selectAccordionSection(names.find((name) => sections[name]?.open === true) || names[0], { persist: false });
}
function updateWorkspaceCounts() {
  $("workspace-project-files-count").textContent = String($("authorized-project-roots").querySelectorAll(".authorized-root-card").length);
  $("workspace-activity-count").textContent = String($("activity-events").querySelectorAll("li[data-state]").length);
}
for (const id of ["authorized-project-roots", "activity-events"])
  new MutationObserver(updateWorkspaceCounts).observe($(id), { childList: true, subtree: true });
document.addEventListener("harness:ready", refreshWorkspaceResources);
