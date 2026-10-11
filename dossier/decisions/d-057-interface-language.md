# D-057 — Interface language: JSON catalogs per locale with a t() helper

Status: accepted (2026-10-11, all seven recommendations). Decided by: Sophia. Issue: #16 (WP9). Design: [docs/interface-language.md](../../docs/interface-language.md). Relates to [D-036](d-036-wp9-interface-language.md) and [codex-parity-design.md WP9](../codex-parity-design.md#wp9-interface-language-english-and-brazilian-portuguese-l-frontend--desktop--small-python).

## Context

WP9 asks for English and Brazilian Portuguese across the harness UI, the admin panel and the desktop app. A count of user-visible strings (method and table in the design, §3.1) gives about 2,700–3,300 strings, most of them in `agent_service/ui.js`, `control/admin.js`, `agent_service/index.html` and the `userErrors` map. UI preferences live in the backend UI state, never in new localStorage keys (D-023, D-055). D-036 records earlier WP9 answers, but some of its claims do not match the repository (no UI-string ratchet in `check_conventions.py`, no pt-BR layout-lint dimension, no catalog test).

## Options

1. **One JSON catalog per locale and a small `t(key, params)` helper** (`harness_ui/assets/locales/{en,pt-br,index}.json`, `harness_ui/assets/i18n.js`), loaded synchronously like `ui-prefs.js`; only locales marked `complete: true` can be chosen. Plurals and dates through `Intl`.
2. **gettext `.po` files** compiled for the browser. Mature tooling, but adds a build step and a compiler dependency to a build-free frontend.
3. **A framework i18n library** (for example i18next). Rich features, but a new runtime dependency for needs that `Intl` and a lookup cover.
4. **Server-side string rendering.** The server would template every string; it does not fit a frontend that builds most of its DOM in JavaScript.

## Decision (proposed)

Option 1. It keeps the frontend build-free, adds no dependency, lets English stay the source and default, and makes completeness testable (`tests/test_locale_catalogs.py`). The JEV picked it among the four with confidence 1.00.

## Consequences

- A new preference key `language` in the backend UI state; a missing key follows the browser language among complete locales, with English as the fallback.
- Provider and model output, user content, logs, identifiers, CLI output and the `sem-projeto` protocol id are never translated.
- The work ships in 15 packages (design §3.2); pt-BR becomes selectable only in the last one, so nothing ships half translated.
- The Portuguese catalog needs an exception to the English-only rule and an entry in `.conventions-allow`.

## Open decisions for Sophia

The seven items in the design, §5, each with a recommended option: first-run language, the AGENTS.md exception, selector location, the standalone admin, when D-036 is superseded, the pt-BR register and reviewer, and synchronous catalog loading.
