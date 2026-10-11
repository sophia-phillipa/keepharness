# Interface language: English and Brazilian Portuguese

> Status: ACCEPTED 2026-10-11. [D-057](../dossier/decisions/d-057-interface-language.md) is accepted and Sophia took the recommended option in all seven decisions of §5; packages run in the §3.2 order.
> Job: When I switch the interface to Brazilian Portuguese, every label, message, error, dialog, menu and admin screen follows without a reload, and English stays one switch away, so I can work in my own language without losing anything.
> Scope: `harness_ui/assets/i18n.js` (new), `harness_ui/assets/locales/` (new), `harness_ui/__init__.py`, `agent_service/ui.js`, `agent_service/run-console.js`, `agent_service/index.html`, `agent_service/tour.js`, `agent_service/terminal-pane.js`, `harness_ui/assets/settings-search.js`, `agent_service/ui_state.py`, `agent_service/ui-prefs.js`, `control/` (admin, customize, first-run, catalogs, index.html), `desktop/main.cjs`, `desktop/preload.cjs`, `tests/`, `.conventions-allow`.
> Invariant docs: [AGENTS.md](../AGENTS.md) (everything in English; the only Portuguese file is `README.pt-BR.md`, so a Portuguese catalog needs an exception, open decision 2), [naming-model](../dossier/naming-model.md) (names), [D-023](../dossier/decisions/d-023-wp6-preferences-in-backend-store.md) and [ui-state.md](ui-state.md) (preferences in the backend store, no new localStorage keys). [D-036](../dossier/decisions/d-036-wp9-interface-language.md) is still proposed; D-057 supersedes it only on acceptance.
> Decision rationale: JSON catalogs are plain data that the browser, the Electron main process and the Python tests all read, so a new language is a data-only change.
> Kill criteria: remove this page if Sophia rejects D-057. Once P1 to P15 have shipped, move the behavior to the release notes and `ui-state.md`, and keep here only the catalog format.

## 1. Why

Sophia asked for this on 2026-10-06: a language selector in Settings translates the whole interface, including errors, notices, toasts, dialogs, empty states, the admin panel and the desktop menus, with English as the default and the fallback ([WP9](../dossier/codex-parity-design.md#wp9-interface-language-english-and-brazilian-portuguese-l-frontend--desktop--small-python), line 77).

| Fact | Evidence |
| --- | --- |
| The interface strings are hard-coded English literals: about 2,970 user-visible literals in total (method and table in §2). | `agent_service/ui.js`, `agent_service/index.html`, `agent_service/run-console.js`, `control/admin.js`, `desktop/main.cjs` (design-pass count, §2) |
| Server error codes are already mapped to English text in the browser, in one map. | `agent_service/ui.js:1215` (`const userErrors = {`), used at `:1897` |
| The server's user-facing error class is `UserMessageError`, a `HarnessError` subclass; the admin panel has no class of its own. | `agent_service/errors.py:32` |
| Dates and numbers use the browser's default or a fixed English tag: 15 `toLocaleString` lines in `ui.js`, 7 in `control/admin.js`, and one call fixed to `"en"`. | `agent_service/ui.js:2367`, `:2412`, `:3319` (`toLocaleString("en")`); `control/admin.js` (7 lines) |
| Preferences live in the backend store, read and written through `GET` and `PATCH /v1/ui-state`; flat keys are allowed in `SCHEMA`. | `agent_service/ui_state.py:185-188` (`wide_layout`, `editor_open`, `terminal_open`), `docs/ui-state.md:5` |
| The client's `CONVERSIONS` map keeps only keys that have an entry; a key with no old localStorage key uses the `{ old, read, write }` shape. | `agent_service/ui-prefs.js:77`, `:199`, `:202` |
| The UI has no framework and no build step; there is no i18n runtime, no `locales/` folder and no `tests/test_locale_catalogs.py` yet. | `harness_ui/assets/` (no `i18n.js`, no `locales/`), `tests/` (no locale test); [D-036](../dossier/decisions/d-036-wp9-interface-language.md) Context |

## 2. How

### 2.1 Approach

One JSON catalog per locale, plus a small runtime. Chosen over gettext/`.po`, a framework i18n library and server-side rendering of strings (see [D-057](../dossier/decisions/d-057-interface-language.md) Alternatives). The design pass ranked this approach first with the JEV at confidence 1.00.

- **Catalogs:** `harness_ui/assets/locales/en.json` (source of truth), `pt-br.json`, and `index.json` listing each locale with its tag, file, native name and `complete` flag.
- **Keys:** flat dotted keys `area.component.purpose`; each segment is snake_case, as the naming model requires for JSON keys. Server codes use `error.<code>`.
- **Values:** `{name}` interpolation. Plurals are objects `{one, other, ...}` resolved with `Intl.PluralRules`. Numbers and dates go through `Intl.NumberFormat` and `Intl.DateTimeFormat` with the active tag.
- **Runtime:** `harness_ui/assets/i18n.js`, loaded right after `agent_service/ui-prefs.js`. It reads the English and the active catalog synchronously, like `ui-prefs.js`, so the first paint is already translated. API: `t(key, params)`. Static HTML carries `data-i18n="key"` (the English text stays inside, so a missing key still shows English); a switch walks those attributes.

Catalog shape (contract, not final text):

```
harness_ui/assets/locales/index.json
{ "locales": [
    { "tag": "en",    "file": "en.json",    "name": "English",              "complete": true },
    { "tag": "pt-BR", "file": "pt-br.json", "name": "Português (Brasil)",   "complete": false } ] }

harness_ui/assets/locales/en.json
{ "composer.send.label": "Send", "run.status.count": { "one": "{count} run", "other": "{count} runs" } }
```

### 2.2 Preference key

- Key `language` in the backend UI state (`agent_service/ui_state.py` `SCHEMA`), with its `CONVERSIONS` entry in `agent_service/ui-prefs.js`. No localStorage key.
- Value: a BCP-47 tag matching a pattern, not a fixed list (WP9 spec). The write is strict; the read is tolerant, so an unknown or old value counts as missing.
- Missing key: the browser language if it matches a locale with `complete: true`, otherwise English (open decision 1).
- Fallback order at runtime: active locale, then `en`, then the key itself. The key shown is a bug signal that the tests must catch.
- Switching sets `<html lang>` and re-translates the open page (see §2.6).

### 2.3 What is not translated

Kept in English on purpose (same list as [D-036](../dossier/decisions/d-036-wp9-interface-language.md) Decision):

- provider and model output, and user content;
- provider-facing text: prompts, system notices, the continuation handoff;
- logs and stderr shown in dialogs;
- identifiers, error codes (the text of `error.<code>` is translated, not the code), and CLI output;
- the persisted protocol id `sem-projeto` (AGENTS.md: do not rename);
- provider catalog names and descriptions (WP9 spec).

### 2.4 Intl

- Plurals: `Intl.PluralRules(active tag)`; a category missing from a catalog falls back to `other`.
- Dates and numbers: replace the 22 `toLocaleString` lines (15 in `ui.js`, 7 in `control/admin.js`), including the fixed `"en"` at `ui.js:3319`, with the active tag.
- No text is concatenated across keys: each sentence is one key with placeholders, because word order differs between languages.

### 2.5 Desktop

- `desktop/main.cjs` builds menus and dialogs with explicit labels from the same JSON catalogs (plain data, read by the Electron main process).
- `desktop/preload.cjs` exposes `keepharnessDesktop.setLanguage(tag)` (the bridge is at `desktop/preload.cjs:6`). The tag is validated against `index.json`; the menus are then rebuilt.
- The preference comes from the backend. Before the agent answers, the menus stay in English. The desktop writes no copy of the preference (see §4, risk 2).
- Desktop today: about 44 menu and label strings and 12 dialog strings (design-pass count).

### 2.6 Admin

- The embedded admin gets the tag through `&lang=` in its iframe URL, as `theme=` is passed today, and reloads its iframe on a switch (WP9 spec). Whether that reload is acceptable is open in [D-036](../dossier/decisions/d-036-wp9-interface-language.md) Open item 4.
- The standalone admin reads the same backend preference (open decision 4).
- `control/index.html` loads `i18n.js` (P10, see §3), so the admin halves can use `t()` as they are converted.

### 2.7 Switching without a reload

Update `HarnessPrefs` `language`, load the catalog, set `<html lang>`, walk `[data-i18n]` and the `data-i18n-title`, `data-i18n-placeholder` and `data-i18n-aria-label` attributes, then run registered `onChange` hooks for composed text, `document.title` and formatter caches. Toasts already on screen keep their text (WP9 spec).

### 2.8 Contracts

- `t(key, params)` returns a string for every key present in `en.json`; a key absent from `en.json` is a test failure.
- `index.json` `complete: true` means every `en.json` key exists in that locale file with the same placeholders. Only `complete: true` locales are offered or auto-detected.
- `language` accepts only a well-formed tag on write (422 `ui_state_invalid_value` on a malformed value, like every ui-state key); a stored value that is not in `index.json` behaves as missing.
- Server codes are never translated on the server; the client maps `error.<code>`, and the English sentence from the server is the fallback.

The status line takes an explicit tone: `status(text, tone)` with `tone` `"error"`, `"progress"` or `"neutral"`. Translated text passes its tone, because a translated sentence does not match the English regexes that choose the tone when the tone is omitted. Those regexes are only the fallback for untranslated callers, and later packages pass the tone explicitly.

### 2.9 Failure modes

| Failure | Behavior | Guard |
| --- | --- | --- |
| A key missing from `pt-br.json` | English text shows (fallback). | `tests/test_locale_catalogs.py` for `complete: true` locales. |
| A key missing from `en.json` | The key text shows; this is a bug. | `tests/test_locale_catalogs.py`, every used key in code exists in `en.json`. |
| Placeholder mismatch in a locale | The test fails. At runtime an unfilled `{name}` shows as written. | Placeholder parity in `tests/test_locale_catalogs.py`. |
| Catalog file missing or not JSON | English stays active; the switch is refused. | Catalog test, and the `index.json` check. |
| `language` value unknown to this build | Read as missing: browser language or English. | P1 tests (strict write, tolerant read). |
| Desktop menus before the agent answers | English menus until the first `setLanguage`. | P13 Playwright or operator step. |
| Two windows switch to different languages | The later `PATCH` wins for the single key (per-key merge, D-023). | `tests/ui-state-restart.spec.cjs` extended in P14. |
| A toast or dialog already on screen | Keeps its old text until closed. | P14 spec. |
| A tag that is well-formed but has no catalog | English. | P2 test. |

### 2.10 Integration points

| Touches | Effect |
| --- | --- |
| `ui_state.SCHEMA`, `ui-prefs.js` `CONVERSIONS` | One new key, `language` (P1). |
| `harness_ui/__init__.py` (`ASSETS`, the allow-list by file name, `:10-21`) | Entries for `i18n.js` and the catalogs (P2). |
| `scripts/check_conventions.py` | `.json` is in both `KEBAB_EXTENSIONS` (`:31`) and `TEXT_EXTENSIONS` (`:32`), so `pt-br.json` is name-linted and scanned for Portuguese words. The `.conventions-allow` entry (`:48` `ALLOW_FILE`) is added in the commit that creates `pt-br.json` (P3). JSON has no comments, so the per-line marker `conventions: allow-pt` (`:56`) cannot be used. |
| `dossier/naming-model.md` | Dotted catalog keys need a rule (snake_case segments joined by dots); the model has none today (§4, risk 4). |
| `AGENTS.md` | Portuguese catalog exception, only if open decision 2 is yes. Not edited by this design. |
| `docs/ui-state.md` | The `language` key is documented in P1. |
| `tests/layout-lint.spec.cjs` | A pt-BR run for longer strings (P15). |
| `docs/operator-suite.md` | One step per surface and per desktop menu (P13 and later). |
| `desktop/main.cjs`, `desktop/preload.cjs` | Menus, dialogs, `setLanguage` (P13). |

### 2.11 Test protocol

| Layer | Tests |
| --- | --- |
| pytest | `tests/test_ui_state.py` (P1: `language` strict on write, tolerant on read); `tests/test_locale_catalogs.py` (new, P2; extended in P15): every used key exists in `en.json`; every `en.json` key exists in each `complete: true` locale and no extra keys; placeholders match; `index.json` lists existing files only; no unused keys; `tests/test_ui_error_map.py` (P4). |
| Playwright | One spec per surface that switches to pt-BR, named `tests/language-<surface>.spec.cjs` (new): settings search (P3), sidebar and composer (P5), conversation (P6), settings and dialogs (P7), static page and tour (P8), run console (P9), admin (P10 and P11), first run (P12, extends `tests/first-run-wizard.spec.cjs`), switch without reload (P14). Errors: `tests/harness-error-messages.spec.cjs` (P4). |
| Layout | `tests/layout-lint.spec.cjs` run in pt-BR as well as English (P15). |
| Accessibility | `<html lang>` follows the switch; `aria-label` and `title` strings are translated, not only text. |
| Visible pass | Desktop app on Sophia's screen, English and pt-BR, before any merge (D-029). |

Commands follow the repository: `.venv/bin/python -m pytest -q tests/test_locale_catalogs.py tests/test_ui_state.py`; `PYTHON="$PWD/.venv/bin/python" ./scripts/test-ui.sh` before a merge to `main`.

## 3. What

### 3.1 Measured user-visible strings

Method: `rg` over each file for double-quoted or backticked literals that start with a capital letter, code lines excluded. These are design-pass estimates, not a parser count; the method is repeatable in P1 for a baseline. The goal from [D-036](../dossier/decisions/d-036-wp9-interface-language.md) (planned sub-issue 9.7) is zero untranslated literals at P15.

| Area | File | Literals (estimate) |
| --- | --- | --- |
| Chat, sidebar, composer, settings, dialogs | `agent_service/ui.js` | ~1,460 |
| Server error codes to text (`userErrors`, `:1215`) | `agent_service/ui.js` | ~340-360 (inside the `ui.js` figure; counted once) |
| Admin panel | `control/admin.js` | ~570 |
| Run console | `agent_service/run-console.js` | ~200 |
| HTML pages | `agent_service/index.html` | ~505 |
| Customize | `control/customize.js` | ~83 |
| First run | `control/first-run.js` | 33 |
| Catalogs | `control/catalogs.js` | 21 |
| Terminal pane | `agent_service/terminal-pane.js` | 16 |
| Tour | `agent_service/tour.js` | 12 |
| Settings search | `harness_ui/assets/settings-search.js` | 15 |
| Desktop | `desktop/main.cjs` | ~56 (about 44 labels and 12 dialogs) |
| **Total** | | **~2,970** (rows summed; `userErrors` counted once). The design pass reported ~2,700-3,300. |

[D-036](../dossier/decisions/d-036-wp9-interface-language.md) estimated ui.js ~1,300, admin ~540 and desktop ~40 with a different method; the figures are not comparable.

### 3.2 Implementation order (one implementer per package)

A package starts only when D-057 is accepted. Each package has one Done-when line. P1 and P2 ship alone. P3 creates `pt-br.json` and its `.conventions-allow` entry in the same commit. P15 is the only package that sets `complete: true`. Each package in a Haiku brief receives its Files and one Done-when line.

| # | Package | Files | Done when |
| --- | --- | --- | --- |
| P1 | Preference key | `agent_service/ui_state.py`, `agent_service/ui-prefs.js`, `docs/ui-state.md`, `tests/test_ui_state.py` | `language` accepts only a well-formed tag on write, reads an unknown value as missing, has its `CONVERSIONS` entry, and `tests/test_ui_state.py` passes. |
| P2 | Runtime and catalog skeleton | `harness_ui/assets/i18n.js` (new), `harness_ui/assets/locales/en.json` (new), `harness_ui/assets/locales/index.json` (new), `harness_ui/__init__.py`, `tests/test_locale_catalogs.py` (new) | `t()` returns the `en.json` text for a skeleton key and the key itself for an unknown key, and `tests/test_locale_catalogs.py` passes. |
| P3 | Pilot: settings search | `harness_ui/assets/settings-search.js`, `harness_ui/assets/locales/en.json`, `harness_ui/assets/locales/pt-br.json` (new), `.conventions-allow`, `tests/language-settings-search.spec.cjs` (new) | The 15 settings-search literals are `t()` keys with unchanged English text, and `pt-br.json` passes the catalog test and the conventions check with its new allow entry. |
| P4 | Error messages | `agent_service/ui.js` (`userErrors`, `:1215`), `harness_ui/assets/locales/en.json`, `harness_ui/assets/locales/pt-br.json`, `tests/test_ui_error_map.py`, `tests/harness-error-messages.spec.cjs` | Every `userErrors` message is an `error.<code>` key, an unknown code still shows the English server sentence, and both error tests pass. |
| P5 | `ui.js`: sidebar and composer | `agent_service/ui.js`, `harness_ui/assets/locales/en.json`, `harness_ui/assets/locales/pt-br.json`, `tests/language-sidebar-composer.spec.cjs` (new) | Every sidebar and composer literal in `ui.js` is a `t()` key, and the spec passes in English and for the pt-BR keys present. |
| P6 | `ui.js`: conversation and run status | `agent_service/ui.js`, `harness_ui/assets/locales/en.json`, `harness_ui/assets/locales/pt-br.json`, `tests/language-conversation.spec.cjs` (new) | Every conversation header, message area and run-status literal in `ui.js` is a `t()` key, and the spec passes in English and for the pt-BR keys present. |
| P7 | `ui.js`: settings and dialogs | `agent_service/ui.js`, `harness_ui/assets/locales/en.json`, `harness_ui/assets/locales/pt-br.json`, `tests/language-settings.spec.cjs` (new) | Every settings and dialog literal in `ui.js` outside the search is a `t()` key, and the spec passes in English and for the pt-BR keys present. |
| P8 | Static HTML, tour, terminal pane | `agent_service/index.html`, `agent_service/tour.js`, `agent_service/terminal-pane.js`, `harness_ui/assets/locales/en.json`, `harness_ui/assets/locales/pt-br.json`, `tests/language-static.spec.cjs` (new) | Static text in `index.html` carries `data-i18n` with its English kept inside, and the tour and terminal-pane strings are `t()` keys with unchanged English text. |
| P9 | Run console | `agent_service/run-console.js`, `harness_ui/assets/locales/en.json`, `harness_ui/assets/locales/pt-br.json`, `tests/language-run-console.spec.cjs` (new) | Every run-console literal is a `t()` key and the English console is unchanged in its spec. |
| P10 | Admin, first half | `control/admin.js` (first half, split at a function boundary), `control/index.html` (loads `i18n.js`), `harness_ui/assets/locales/en.json`, `harness_ui/assets/locales/pt-br.json`, `tests/language-admin.spec.cjs` (new) | Every literal in the first half of `control/admin.js` is a `t()` key, the admin page loads the runtime, and the admin spec passes in English for those areas. |
| P11 | Admin, second half | `control/admin.js` (second half), `harness_ui/assets/locales/en.json`, `harness_ui/assets/locales/pt-br.json`, `tests/language-admin.spec.cjs` (extend) | Every literal in the second half of `control/admin.js` is a `t()` key, and the admin spec covers the whole screen in English. |
| P12 | Customize, first run, catalogs | `control/customize.js`, `control/first-run.js`, `control/catalogs.js`, `harness_ui/assets/locales/en.json`, `harness_ui/assets/locales/pt-br.json`, `tests/first-run-wizard.spec.cjs` (extend) | Every literal in these three files is a `t()` key, and the first-run wizard spec passes in English. |
| P13 | Desktop | `desktop/main.cjs`, `desktop/preload.cjs` (`setLanguage`), `harness_ui/assets/locales/en.json`, `harness_ui/assets/locales/pt-br.json`, `docs/operator-suite.md` | Menus and dialogs are built from `t()` keys in the same catalogs, rebuild on `setLanguage`, and the operator-suite desktop step confirms the pt-BR menus on an installed build. |
| P14 | Selector and live switch | `agent_service/index.html` (Settings › Appearance, `#settings-appearance`, `:45`), `agent_service/ui.js`, `harness_ui/assets/i18n.js`, `tests/language-switch.spec.cjs` (new), `tests/ui-state-restart.spec.cjs` (extend) | Settings › Appearance has one language select that saves `language` through `/v1/ui-state` and re-translates the open page without a reload, and the spec proves that no reload happens and that toasts already on screen keep their text. |
| P15 | pt-BR review and release | `harness_ui/assets/locales/pt-br.json`, `harness_ui/assets/locales/index.json`, `tests/test_locale_catalogs.py`, `tests/layout-lint.spec.cjs`, `dossier/releases/v<VERSION>.md` (new, version set at release) | `index.json` marks `pt-BR` `complete: true` after Sophia's review, and the catalog test and the pt-BR layout lint pass. |

Note on P10: `control/index.html` moves from P12 to P10 because the admin halves call `t()` and need the runtime loaded first.

### 3.3 Test protocol and visible pass

Covered in §2.11. The visible pass (desktop app on Sophia's screen, English and pt-BR, each surface) runs before any merge (D-029).

## 4. Risks

1. **D-036 does not match the repo.** D-036 is proposed; D-057 replaces it on acceptance.

   | D-036 says | Repo today, or this design |
   | --- | --- |
   | Catalogs `harness_ui/assets/locale-en.json`, `locale-pt-BR.json`, index `locales.json` | This design: `harness_ui/assets/locales/en.json`, `pt-br.json`, `index.json` (kebab-case file names, naming model). |
   | API `t(key, args)`, `t.el`, `t.number`, `t.date` | This design: `t(key, params)` and `data-i18n`; `Intl` used directly. |
   | `check_conventions.py` has a per-file literal-string ratchet | Not in the repo: `scripts/check_conventions.py` has the file-name lint, the Portuguese-word scan and the README heading parity. The ratchet is not in P1 to P15; adding it is a separate change. |
   | `tests/layout-lint.spec.cjs` has a pt-BR dimension | Not present; P15 adds the run. |
   | `tests/test_locale_catalogs.py` exists | Not present; created in P2. |
   | The admin has a `UserMessageError` that gains `code` and `params` | Only `agent_service/errors.py:32` exists; the admin has no such class. |
   | ui.js ~1,300, admin ~540, desktop ~40 | Re-estimated at ~1,460, ~570, ~56 (§3.1); different method. |

2. **No desktop preferences cache exists.** The design pass named a `userData/preferences.json` cache; `rg` finds no such file in the repo. The desktop uses `userData` for `logs/main.log` (`desktop/main.cjs:72`) and `window-state.json` (`:143`). The backend file `<state_dir>/ui-state/<owner hash>/preferences.json` is the only store (`docs/ui-state.md:5`). Guard: P13 reads the language from the backend and never writes a copy, because a copy would break D-023.

3. **Admin storage.** `control/` has no `localStorage` call. The theme is persisted in `localStorage` for surfaces other than the harness (`harness_ui/assets/theme.js:21` and `:34`, where `HarnessPrefs.set` runs only for `surface === 'harness'`). The WP9 spec's "the standalone admin keeps the last seen tag in its own origin" would repeat that pattern and add a localStorage key, which D-023 and `docs/ui-state.md:3` forbid. Open decision 4 settles it.

4. **Naming model has no rule for dotted keys.** `dossier/naming-model.md` requires snake_case JSON keys; the dotted key form needs a rule. Proposed change, for Sophia to accept: "dotted segments, each snake_case". Not edited here.

5. **AGENTS.md has no Portuguese catalog exception.** Only open decision 2 allows it. AGENTS.md is a rule document and is not edited here.

6. **Workspace docs drift, out of scope.** `docs/workspace-toggles.md:18` says no interactive terminal exists, and `:19` says no project-file editor exists. Both are on `main`: `agent_service/terminal-pane.js` and `agent_service/index.html:10`, and the editor and wide-layout toggles in `agent_service/ui.js:12120-12195`. The spec needs a reconcile pass; this design does not depend on it.

7. **Translation quality.** The pt-BR register (open decision 6) and review by Sophia gate P15. A translation that is complete but poor would ship only with her `complete: true`.

## 5. Open decisions

Answered 2026-10-11: Sophia took the recommended (first) option in all seven items.

1. **First-run language.** Recommended: browser language among `complete: true` locales, English otherwise. Alternative: always English, as the WP9 spec's wording reads.
2. **Portuguese catalog exception in AGENTS.md.** Recommended: yes, limited to `harness_ui/assets/locales/pt-br.json`. Alternative: no exception; Portuguese stays out of the repository.
3. **Selector location.** Recommended: Settings › Appearance (`index.html:45`). Alternative: its own Language section in Settings (WP9 open question 2).
4. **Standalone admin.** Recommended: reads the same backend preference. Alternative: keeps its own choice, which needs a localStorage key and conflicts with D-023.
5. **When D-036 becomes superseded.** Recommended: only after D-057 is accepted. Alternative: mark it now.
6. **pt-BR register.** Recommended: "você", informal-neutral, reviewed by Sophia. Alternative: a more formal register, chosen with the reviewer. <!-- conventions: allow-pt -->
7. **Catalog load.** Recommended: synchronous, like `ui-prefs.js`, so the first paint is translated. Alternative: asynchronous fetch on demand, with a first paint in English.

## See also

[D-057](../dossier/decisions/d-057-interface-language.md), [D-036](../dossier/decisions/d-036-wp9-interface-language.md), [WP9 spec](../dossier/codex-parity-design.md#wp9-interface-language-english-and-brazilian-portuguese-l-frontend--desktop--small-python), [D-023](../dossier/decisions/d-023-wp6-preferences-in-backend-store.md), [D-029](../dossier/decisions/d-029-visible-pass-before-merge.md), [ui-state.md](ui-state.md), [layout-lint.md](layout-lint.md), [workspace-toggles.md](workspace-toggles.md), [naming model](../dossier/naming-model.md), [AGENTS.md](../AGENTS.md).
