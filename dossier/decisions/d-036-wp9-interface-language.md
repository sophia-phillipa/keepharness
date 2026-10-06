# D-036 — Interface language through per-locale JSON catalogs and a DOM-attribute runtime

Status: proposed · 2026-10-06 · design by the orchestrator, pending Sophia's approval · spec: [WP9](../codex-parity-design.md#wp9-interface-language-english-and-brazilian-portuguese-l-frontend--desktop--small-python)

## Context

About 2,700 user-visible literals across `agent_service/ui.js`, `run-console.js`, `index.html`, the admin panel (`control/`) and `desktop/` are hard-coded in English (regex estimates: ui.js ~1,300, admin.js ~540, desktop main ~40); 340 server error codes are already mapped to text client-side (`userErrors` in ui.js). The UI is framework-free with no build step; preferences live in the WP6 store ([D-023](d-023-wp6-preferences-in-backend-store.md)).

## Decision

- One JSON catalog per locale in `harness_ui/assets/` (`locale-en.json` as the source of truth, `locale-pt-BR.json`, and an index `locales.json` with each tag and native name). Flat keys `area.component.purpose`; errors `error.<code>`; `{name}` interpolation; plural values as `{one, other, …}` objects resolved by `Intl.PluralRules`; numbers and dates through `Intl` (replacing the `toLocale*` calls with a fixed `"en-US"`).
- A shared `harness_ui/assets/i18n.js`, loaded right after `ui-prefs.js`, reads the English and active catalogs synchronously (the `ui-prefs.js` pattern) so the first paint is already translated. API: `t(key, args)`, `t.el(node, key, args, attr?)` (writes the text and stores `data-i18n` / `data-i18n-args`), `t.number`, `t.date`.
- Switching language needs no reload: `HarnessPrefs.set("language")`, load the catalog, update `<html lang>`, walk `[data-i18n]` and the `data-i18n-title|placeholder|aria-label` attributes, then run registered `onChange` hooks for composed text, `document.title` and formatter caches. Toasts already on screen stay as they are.
- Static `index.html` text carries `data-i18n="key"` and keeps its English text, so a missing key still shows English. Fallback order: active locale, English, the key itself (only on a bug, caught by tests).
- The preference `language` (BCP-47 pattern, not a fixed list) is added to the `ui_state.py` allow-list. The embedded admin gets it through `&lang=` in its iframe URL (as `theme=` today) and reloads its iframe on a switch; the standalone admin keeps the last seen tag in its own origin. The desktop main gets it through a preload call `keepharnessDesktop.setLanguage(tag)`, validates it against the index, rebuilds the menus with explicit labels, and caches it in `userData/preferences.json` (atomic write) for dialogs shown before the service starts; the package script copies the catalogs into the asar.
- Servers send codes, clients translate: the harness already returns `{code, message}`; the admin's `UserMessageError` gains optional `code` and `params`, keeping the English sentence as fallback and for logs.
- English stays: logs, stderr in dialogs, identifiers, error codes, `sem-projeto`, all provider-facing text (prompts, system notices, continuation handoff), provider catalog names and descriptions, and user content.

## Rationale

JSON is the only format the renderer, the Electron main and the Python tests all read as plain data, so a new language is a data-only change. Attribute-backed nodes make "no reload" incremental: every string converted through the helper is re-translated for free.

## Alternatives

- JS-wrapped catalogs: not readable as data by the Electron main or the tests.
- An i18n library: a new dependency and likely a build step.
- Server-side translation by `Accept-Language`: duplicates catalogs in Python and mixes in provider-facing text.
- Re-rendering only through per-view hooks: misses static text.
- Reloading the page on a switch: forbidden by the spec.

## Impact

`ui_state.py` schema, `ui-prefs.js`, the `harness_ui` asset allow-list (`harness_ui/__init__.py`), every UI file, `UserMessageError`, desktop preload/main/package script, `check_conventions.py` (a ratchet lint on literal UI strings with a per-file baseline), `layout-lint.spec.cjs` (a pt-BR dimension). Tests: `tests/test_locale_catalogs.py` (every used key exists in `en`; every `en` key exists in `pt-BR` and no extra; same placeholders; `locales.json` matches the files; pt-BR equal to English only on an allow-list), a switch spec (no reload, no `[data-i18n]` left in English, admin iframe `lang=pt-BR`, back to English restores), and desktop `main.test.cjs` (pt-BR menu, fallback, invalid tag rejected).

Planned sub-issues, in order: 9.1 runtime, catalogs, serving, `language` pref, selector, catalog tests (M); 9.2 error maps and admin `code`/`params` (M); 9.3 `index.html` codemod, Settings, sidebar, composer (M); 9.4a and 9.4b `ui.js` and `run-console.js` by area (M each); 9.5 admin panel (M); 9.6 desktop (S); 9.7 baseline at zero, pt-BR review, release notes (S). Implementation comes after WP3.

## Open

1. On first run, follow the OS language, or stay in English until chosen?
2. Selector under Appearance, or its own Language section in Settings?
3. pt-BR register (informal second person) and who signs off the translation (assumed: Sophia).
4. Is reloading the embedded admin iframe on a language switch acceptable?
