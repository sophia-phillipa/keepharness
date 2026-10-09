/* Durable UI preferences: the harness keeps them in its own store (GET/PATCH /v1/ui-state) instead of
 * the browser's per-origin localStorage. Loaded before ui.js, which reads preferences at the top level,
 * so the first read is one synchronous request. Any answer that is not a version 1 store (a 403,
 * a mocked page, a network error) leaves "local mode": the previous localStorage behaviour, unchanged. */
(() => {
  "use strict";
  const DEBOUNCE_MS = 400;
  const BACKOFF_MIN_MS = 2000;
  const BACKOFF_MAX_MS = 30000;
  const ENDPOINT = "/v1/ui-state";
  const KEEPALIVE_BYTES = 60 * 1024; // browsers allow 64 KB of keepalive requests in flight

  const store = {
    get(key) {
      try {
        return localStorage.getItem(key);
      } catch {
        return null;
      }
    },
    set(key, value) {
      try {
        if (value === null) localStorage.removeItem(key);
        else localStorage.setItem(key, value);
        return true;
      } catch {
        return false;
      }
    },
    keys(prefix) {
      try {
        return Object.keys(localStorage).filter((key) =>
          key.startsWith(prefix),
        );
      } catch {
        return [];
      }
    },
  };
  const parse = (key) => {
    try {
      return JSON.parse(store.get(key)) ?? undefined;
    } catch {
      return undefined;
    }
  };
  const number = (key) => {
    const text = store.get(key);
    const value = text === null || text === "" ? NaN : Number(text);
    return Number.isFinite(value) ? value : undefined;
  };
  const text = (key) => store.get(key) ?? undefined;
  const flag = (key) => {
    const value = store.get(key);
    return value === null ? undefined : value === "1";
  };
  const asJson = (value) => (value === null ? null : JSON.stringify(value));
  const renamed = (map, from, to) => {
    if (!map || typeof map !== "object") return map;
    const result = {};
    for (const [id, entry] of Object.entries(map)) {
      if (entry && typeof entry === "object" && from in entry) {
        const { [from]: moved, ...rest } = entry;
        result[id] = { ...rest, [to]: moved };
      } else result[id] = entry;
    }
    return result;
  };

  // One entry per store key: the old localStorage keys it replaces, how to read them into the store's
  // shape, and how to write the store's shape back (local mode only). `keep` marks a cache that stays.
  const WIDTHS = {
    sidebar: "sidebar-width",
    activity_panel: "activity-panel-width",
  };
  const SECTIONS = "workspace-section-";
  const CONVERSIONS = {
    theme: {
      keep: true,
      old: () => ["keepharness:theme:harness"],
      // theme.js owns this cache, including the pre-0.15 key; it writes it itself.
      read: () =>
        text("keepharness:theme:harness") ?? text("tail-harness:theme:harness"),
      write: () => [],
    },
    sidebar_collapsed: {
      old: () => ["sidebar-collapsed"],
      read: () => flag("sidebar-collapsed"),
      write: (value) => [["sidebar-collapsed", value ? "1" : "0"]],
    },
    panel_order: {
      old: () => ["panel-order"],
      read: () => text("panel-order"),
      write: (value) => [["panel-order", value]],
    },
    panel_widths: {
      old: () => Object.values(WIDTHS),
      read: () => {
        const result = {};
        for (const [field, old] of Object.entries(WIDTHS)) {
          const value = number(old);
          if (value !== undefined) result[field] = value;
        }
        return Object.keys(result).length ? result : undefined;
      },
      write: (value) =>
        Object.entries(WIDTHS).map(([field, old]) => [
          old,
          Number.isFinite(value?.[field]) ? String(value[field]) : null,
        ]),
    },
    reading_size: {
      old: () => ["reading-size"],
      read: () => text("reading-size"),
      write: (value) => [["reading-size", value]],
    },
    chat_selection: {
      old: () => ["chat-selection"],
      read: () => parse("chat-selection"),
      write: (value) => [["chat-selection", asJson(value)]],
    },
    project_list_preferences: {
      old: () => ["project-list-preferences"],
      read: () =>
        renamed(parse("project-list-preferences"), "hideIcon", "hide_icon"),
      write: (value) => [
        [
          "project-list-preferences",
          asJson(renamed(value, "hide_icon", "hideIcon")),
        ],
      ],
    },
    project_expanded: {
      old: () => ["project-expanded"],
      read: () => parse("project-expanded"),
      write: (value) => [["project-expanded", asJson(value)]],
    },
    right_panel_view: {
      old: () => ["right-panel-view"],
      read: () => text("right-panel-view"),
      write: (value) => [["right-panel-view", value]],
    },
    activity_open: {
      old: () => ["activity-open"],
      read: () => flag("activity-open"),
      write: (value) => [["activity-open", value ? "1" : "0"]],
    },
    conversation_activity: {
      old: () => ["conversation-activity"],
      read: () => parse("conversation-activity"),
      write: (value) => [["conversation-activity", asJson(value)]],
    },
    conversation_scroll: {
      old: () => ["conversation-scroll"],
      read: () => parse("conversation-scroll"),
      write: (value) => [["conversation-scroll", asJson(value)]],
    },
    tour_seen: {
      old: () => ["keepharness-tour-seen"],
      read: () => text("keepharness-tour-seen"),
      write: (value) => [["keepharness-tour-seen", value]],
    },
    run_console_height: {
      old: () => ["run-console-height"],
      read: () => number("run-console-height"),
      write: (value) => [
        ["run-console-height", value === null ? null : String(value)],
      ],
    },
    workspace_sections: {
      old: () => store.keys(SECTIONS),
      read: () => {
        const result = {};
        for (const key of store.keys(SECTIONS)) {
          const value = parse(key);
          if (value && typeof value === "object")
            result[key.slice(SECTIONS.length)] = value;
        }
        return Object.keys(result).length ? result : undefined;
      },
      write: (value) => {
        const next = value || {};
        const gone = store
          .keys(SECTIONS)
          .filter((key) => !(key.slice(SECTIONS.length) in next))
          .map((key) => [key, null]);
        return [
          ...gone,
          ...Object.entries(next).map(([name, entry]) => [
            SECTIONS + name,
            asJson(entry),
          ]),
        ];
      },
    },
    last_section: { old: () => [], read: () => undefined, write: () => [] },
    visual_markers: { old: () => [], read: () => undefined, write: () => [] },
    always_on_top: { old: () => [], read: () => undefined, write: () => [] },
  };

  const clone = (value) =>
    value !== null && typeof value === "object"
      ? JSON.parse(JSON.stringify(value))
      : value;

  let server = false;
  let limits = {};
  let values = {}; // server mode: the confirmed-or-pending value of every key
  const memory = {}; // local mode: keys with no old localStorage key (last_section, visual_markers)
  const pending = {}; // latest value per key not yet confirmed (null clears)
  const baseline = {}; // JSON of the last delivery the server confirmed, per key
  const inflight = {}; // JSON of the keepalive delivery still on its way, per key: exit events send only the delta
  const migrating = new Set();
  let timer = 0;
  let sending = null;
  let again = false;
  let backoff = 0;
  let readOnly = false;
  const noticed = new Set();
  const queuedNotices = [];
  let notice = null;

  function announce(code) {
    if (noticed.has(code)) return;
    noticed.add(code);
    if (notice) notice(code);
    else queuedNotices.push(code);
  }

  function prune(key, value) {
    const cap = limits.max_items?.[key];
    if (!cap || value === null || typeof value !== "object") return value;
    if (Array.isArray(value)) return value.slice(-cap);
    const entries = Object.entries(value);
    return entries.length > cap
      ? Object.fromEntries(entries.slice(-cap))
      : value;
  }

  function schedule(delay = DEBOUNCE_MS) {
    if (!server || readOnly) return;
    clearTimeout(timer);
    timer = setTimeout(() => {
      flush();
    }, delay);
  }

  const encoded = (key) => JSON.stringify(pending[key] ?? null);
  const dirty = () =>
    Object.keys(pending).filter(
      (key) =>
        encoded(key) !== (baseline[key] ?? "null") &&
        encoded(key) !== inflight[key],
    );

  function drop(key, why) {
    console.warn("ui-state: dropped " + key + " (" + why + ")");
    delete pending[key];
    migrating.delete(key);
  }

  function accepted(key, sent) {
    baseline[key] = sent;
    if (migrating.delete(key) && !CONVERSIONS[key].keep)
      for (const old of CONVERSIONS[key].old()) store.set(old, null);
  }

  async function deliverEach(keys, keepalive) {
    for (const key of keys) {
      const result = await deliver([key], keepalive);
      if (result !== "ok") return result;
    }
    return "ok";
  }

  // Sends one batch. "ok": nothing more to retry for it; "retry": transient failure; "stop": the store is closed.
  async function deliver(keys, keepalive) {
    const sent = {};
    for (const key of keys) sent[key] = prune(key, pending[key] ?? null);
    const payload = JSON.stringify({ values: sent });
    if (
      keepalive &&
      keys.length > 1 &&
      new TextEncoder().encode(payload).length > KEEPALIVE_BYTES
    )
      return deliverEach(keys, keepalive);
    if (keepalive)
      for (const key of keys) inflight[key] = JSON.stringify(sent[key]);
    let response;
    try {
      response = await fetch(ENDPOINT, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: payload,
        keepalive,
      });
    } catch {
      return "retry";
    } finally {
      if (keepalive) for (const key of keys) delete inflight[key];
    }
    if (response.ok) {
      backoff = 0;
      for (const key of keys) accepted(key, JSON.stringify(sent[key]));
      return "ok";
    }
    const body = await response.json().catch(() => ({}));
    const code = body?.code;
    if (response.status === 409 && code === "ui_state_read_only") {
      readOnly = true;
      for (const key of Object.keys(pending)) delete pending[key];
      announce(code);
      return "stop";
    }
    if (response.status === 403) {
      server = false;
      for (const key of Object.keys(pending)) delete pending[key];
      return "stop";
    }
    if (response.status === 413 || response.status === 422) {
      if (response.status === 422 && keys.includes(body?.field)) {
        drop(body.field, code);
        announce(code);
        return "ok";
      }
      if (keys.length > 1) return deliverEach(keys, keepalive);
      drop(keys[0], code || "payload_limit");
      if (response.status === 422) announce(code);
      return "ok";
    }
    if (response.status >= 500 || response.status === 429) return "retry";
    for (const key of keys) drop(key, "HTTP " + response.status);
    return "ok";
  }

  async function drain(keepalive) {
    let batch = dirty();
    while (batch.length && server && !readOnly) {
      const result = await deliver(batch, keepalive);
      if (result === "retry") {
        backoff = Math.min(
          Math.max(backoff * 2, BACKOFF_MIN_MS),
          BACKOFF_MAX_MS,
        );
        schedule(backoff + Math.random() * backoff * 0.25);
        return;
      }
      if (result === "stop") return;
      batch = dirty();
    }
  }

  function flush({ keepalive = false } = {}) {
    clearTimeout(timer);
    timer = 0;
    if (!server || readOnly) return Promise.resolve();
    if (keepalive) return drain(true); // the page is going away: do not wait for a request in flight
    if (sending) {
      again = true;
      return sending;
    }
    sending = drain(false).finally(() => {
      sending = null;
      if (again) {
        again = false;
        schedule(0);
      }
    });
    return sending;
  }

  // True when the change is kept: queued for the store, or written to localStorage in local mode.
  function set(key, value) {
    const conversion = CONVERSIONS[key];
    if (!conversion) return false;
    value = value === undefined ? null : clone(value);
    if (!server) {
      if (value === null) delete memory[key];
      else memory[key] = value;
      return conversion
        .write(value)
        .map(([old, text]) => store.set(old, text))
        .every(Boolean);
    }
    value = prune(key, value);
    if (value === null) delete values[key];
    else values[key] = value;
    pending[key] = value;
    schedule();
    return true;
  }

  function get(key, fallback = null) {
    const conversion = CONVERSIONS[key];
    if (!conversion) return fallback;
    const value = server ? values[key] : (conversion.read() ?? memory[key]);
    return value === undefined || value === null ? fallback : clone(value);
  }

  function boot() {
    let data;
    try {
      const request = new XMLHttpRequest();
      request.open("GET", ENDPOINT, false);
      request.send();
      if (request.status === 200) data = JSON.parse(request.responseText);
    } catch {}
    if (
      !data ||
      data.version !== 1 ||
      !data.values ||
      typeof data.values !== "object" ||
      Array.isArray(data.values)
    )
      return;
    server = true;
    readOnly = data.read_only === true;
    limits = data.limits && typeof data.limits === "object" ? data.limits : {};
    values = Object.fromEntries(
      Object.entries(data.values).filter(([key]) => key in CONVERSIONS),
    );
    for (const key of Object.keys(values))
      baseline[key] = JSON.stringify(values[key]);
    if (readOnly) {
      announce("ui_state_read_only");
      return;
    }
    for (const [key, conversion] of Object.entries(CONVERSIONS)) {
      if (key in values) continue; // the server wins; the old keys stay untouched
      const old = conversion.read();
      if (old === undefined || old === null) continue;
      values[key] = prune(key, old);
      pending[key] = values[key];
      migrating.add(key);
    }
    if (migrating.size) schedule(0);
  }

  // The first-paint theme cache can lag behind the store (another window or a new origin).
  function reconcileTheme() {
    const theme = window.HarnessTheme;
    const chosen = values.theme;
    if (!server || !theme || !chosen || theme.surface !== "harness") return;
    if (
      !theme.themes.some((item) => item.id === chosen) ||
      store.get(theme.key) === chosen
    )
      return;
    theme.apply(chosen, false);
    store.set(theme.key, chosen);
  }

  boot();
  reconcileTheme();
  addEventListener("pagehide", () => {
    flush({ keepalive: true });
  });
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") flush({ keepalive: true });
  });

  window.HarnessPrefs = {
    get,
    set,
    flush,
    ready: Promise.resolve(),
    get server() {
      return server;
    },
    onNotice(callback) {
      notice = callback;
      for (const code of queuedNotices.splice(0)) callback(code);
    },
  };
})();
