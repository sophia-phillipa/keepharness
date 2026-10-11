/* Interface language runtime. Loads the catalogs synchronously, like ui-prefs.js, so the first paint is
 * already translated. Lookup order: active catalog, then English, then the key itself. A missing or
 * non-JSON catalog leaves English active. Not wired into any page yet; `t` is the only API the UI uses. */
(() => {
  "use strict";
  const BASE = "/assets/locales/";
  const SOURCE_TAG = "en";
  const SOURCE_FILE = "en.json";
  const ATTRIBUTES = [
    ["data-i18n-title", "title"],
    ["data-i18n-placeholder", "placeholder"],
    ["data-i18n-aria-label", "aria-label"],
  ];
  const listeners = [];

  function readJson(file) {
    try {
      const request = new XMLHttpRequest();
      request.open("GET", BASE + file, false);
      request.send();
      if (request.status !== 200) return null;
      const data = JSON.parse(request.responseText);
      return data && typeof data === "object" && !Array.isArray(data) ? data : null;
    } catch {
      return null;
    }
  }

  const index = readJson("index.json");
  const complete = (Array.isArray(index?.locales) ? index.locales : []).filter(
    (locale) => locale?.complete === true && typeof locale.tag === "string" && typeof locale.file === "string",
  );
  const exact = (candidate) => complete.find((locale) => locale.tag.toLowerCase() === candidate.toLowerCase());
  const primary = (tag) => tag.split("-")[0].toLowerCase();

  // HarnessPrefs language first (only a complete tag), then the browser languages: exact, then primary subtag.
  function choose() {
    const stored = window.HarnessPrefs?.get("language");
    const storedMatch = typeof stored === "string" ? exact(stored) : undefined;
    if (storedMatch) return storedMatch;
    for (const language of navigator.languages ?? []) {
      const match = exact(language) ?? complete.find((locale) => primary(locale.tag) === primary(language));
      if (match) return match;
    }
    return undefined;
  }

  const english = readJson(SOURCE_FILE) ?? {};
  let active = { tag: SOURCE_TAG, catalog: english };
  const choice = choose();
  if (choice) {
    const catalog = choice.file === SOURCE_FILE ? english : readJson(choice.file);
    if (catalog) active = { tag: choice.tag, catalog };
  }
  const rules = { active: new Intl.PluralRules(active.tag), english: new Intl.PluralRules(SOURCE_TAG) };

  // A plural object picks its form by Intl.PluralRules; a form that is missing falls back to `other`.
  function pick(value, rule, count) {
    if (typeof value === "string") return value;
    if (!value || typeof value !== "object") return undefined;
    const text = value[rule.select(count)] ?? value.other;
    return typeof text === "string" ? text : undefined;
  }

  function lookup(catalog, key, rule, count) {
    return Object.hasOwn(catalog, key) ? pick(catalog[key], rule, count) : undefined;
  }

  // Undefined when neither catalog has the key, so `apply` keeps the English text already in the page.
  function translate(key, params) {
    const count = Number(params?.count);
    const text =
      lookup(active.catalog, key, rules.active, count) ?? lookup(english, key, rules.english, count);
    if (text === undefined) return undefined;
    return text.replace(/\{(\w+)\}/g, (placeholder, name) =>
      params && Object.hasOwn(params, name) ? String(params[name]) : placeholder,
    );
  }

  window.HarnessI18n = {
    t: (key, params) => translate(key, params) ?? key,
    tag: () => active.tag,
    apply(root = document) {
      for (const element of root.querySelectorAll("[data-i18n]")) {
        const text = translate(element.getAttribute("data-i18n"));
        if (text !== undefined) element.textContent = text;
      }
      for (const [attribute, target] of ATTRIBUTES) {
        for (const element of root.querySelectorAll(`[${attribute}]`)) {
          const text = translate(element.getAttribute(attribute));
          if (text !== undefined) element.setAttribute(target, text);
        }
      }
    },
    onChange(callback) {
      listeners.push(callback);
    },
  };
  document.documentElement.lang = active.tag;
})();
