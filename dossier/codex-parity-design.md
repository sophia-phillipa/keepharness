# Codex-app parity design (0.16.0)

Status: accepted and partly implemented. Decisions: [D-018](decisions/d-018-codex-parity-architecture.md) to [D-029](decisions/d-029-visible-pass-before-merge.md). Behavior as shipped: [0.16.0 release notes](releases/v0.16.0.md). Codex reference screens: [Codex app inventory](research/codex-app-inventory-2026-10.md).

Kind: explanation and reference (why the parity work is shaped this way, and the contracts each work package must keep). The release notes are the record of what actually shipped and how it was validated; this document does not repeat them.

## Why

Sophia wants KeepHarness usable every day as a replacement for the Codex desktop app. Her asks (2026-10-05): Back and Forward at the top; Settings as a submenu that jumps to a section, with Admin as ordinary Settings items; a tools screen identical to Codex's Customize; a warning when the current provider lacks a tool another provider has connected; a prompt to continue a conversation in Claude Desktop or ChatGPT Desktop; durable UI preferences; visual markers for what the model is doing; quota meters for every connected provider.

## How

- Extend what exists: the app shell (`agent_service/ui.js`), the Settings dialog that already frames the admin, the admin's catalog and integration endpoints, and `GET /v1/integrations`. No new process, no new dependency, no schema migration ([D-018](decisions/d-018-codex-parity-architecture.md)).
- Where a concept exists in Codex, do exactly what Codex does; only KeepHarness-only concepts follow the design's own recommendations ([D-019](decisions/d-019-parity-product-answers.md)).
- Copy Codex's layout, structure, spacing and wording, never its colors ([D-020](decisions/d-020-theme-tokens-for-copied-screens.md)); never remove or hide a KeepHarness-only feature ([D-021](decisions/d-021-keep-keepharness-only-features.md)).
- Every package: failing test first, an Opus review, review fixes, then a visible pass in the real app on Sophia's screen before merge ([D-029](decisions/d-029-visible-pass-before-merge.md)).

## What: work packages

Sizes: M = 1–2 days of agent work, L = more. Status is as of main `9bff3f8`.

| WP | Scope | Status |
|---|---|---|
| WP1 | Back and Forward navigation | Merged (`779e36b`); [release notes](releases/v0.16.0.md#chat-campaign-fixes-batch-2-and-codex-parity-wave-1) |
| WP2 | Settings submenu; Admin inside Settings | Merged (`83c3c37`); [release notes](releases/v0.16.0.md#wp2-settings-submenu) |
| WP3 | Customize screen (Plugins / Skills) like Codex | Not started |
| WP4 | Cross-provider connected-tools warnings | Backend merged (`fe4489f`), UI merged (`acc8dfa`); [release notes](releases/v0.16.0.md#wp4-cross-provider-tool-warnings-ui) |
| WP5 | Continue in ChatGPT or Claude desktop | Backend merged (`fe4489f`), UI and desktop bridge merged (`9bff3f8`); release notes in the wave-1 section |
| WP6 | Durable UI preferences in a backend store | Merged (`91aaa5e`); [backend](releases/v0.16.0.md#wp6-durable-ui-preferences-backend), [frontend](releases/v0.16.0.md#wp6-durable-ui-preferences-frontend) |
| WP7 | Visual markers | On branch `feat/wp7-markers`, not merged |
| WP8 | Quota meters for every connected provider | Requested, not started |

### WP1 Back and Forward (M, frontend)

- Contract: an app-owned, in-memory view history (`navigate(view, {record})`, `back()`, `forward()`), cap 50, a new navigation drops forward entries, equal views are not pushed; never `history.pushState`/`history.back()`; admin iframe navigations never push. Buttons before the sidebar toggle, `Ctrl/Cmd+[` and `]`, mouse buttons 3 and 4. Session-only on purpose: Codex starts with both buttons disabled.
- Acceptance: `tests/harness-back-forward.spec.cjs`.

### WP2 Settings submenu (M, frontend + desktop)

- Contract: `openSettings(section = lastSection): boolean`; the submenu is built from the dialog's own nav buttons (`role="menu"`, arrow keys, Esc returns focus); `Ctrl/Cmd+,` opens the dialog directly at the last section; System items exist only on a local host. The separate admin window and the "Admin panel" link go away. Must not change admin auth, cookies, the iframe origin check or `data-admin-section` values.
- Acceptance: `tests/settings-submenu.spec.cjs`, `desktop/main.test.cjs`.

### WP3 Customize screen (L, admin frontend + small Python)

- Decision: no mockup step; copy Codex's Customize and Settings › Plugins as closely as possible ([D-022](decisions/d-022-wp3-copy-codex-customize.md)).
- Contract: a new admin panel `customize` over the existing admin catalog and `/api/integration` endpoints, framed in Settings › Plugins and opened by the rail button; chips Plugins | Apps | MCPs | Skills, Add menu, per-row switches, card grid and detail view. The only KeepHarness additions: provider badges on each card and a "Providers" block in the detail view (one row per provider: installed and allowed / installed, not allowed / not installed). "Your agents" stays its own Settings item "Agents" (Codex has no agents screen). Must not run any CLI on render beyond the existing catalog read, return commands, environment or credential URLs, or change `/api/integration` semantics.
- Acceptance (planned): `tests/admin-customize.spec.cjs` with a fixture catalog; pytest for any new read endpoint (no secret fields, CLI failure gives warnings, not 500).
- Open: survey gaps G2 (Manage target, Personal tab, Create MCP App, Skills card menus) and G3 (other Settings pages) from the design were partly settled by inventory pass 3; the rest is unverified.

### WP4 Cross-provider tool warnings (M, Python + frontend)

- Contract: `GET /v1/integrations` gains an always-present `elsewhere` list (max 50) of `{key, label, here: "enable"|"absent", providers: [{backend, allowed, effective_capable}]}`; family key = lowercase name without `mcp:`/`plugin:` prefix and `@marketplace` suffix; no commands, URLs, environment or ids. Plugins menu section "On other providers", a dot on the Plugins chip, and extra lines in the route carry-over note. Codex shows no such warning, so this is a KeepHarness-only concept.
- Decision on the "Enable" action, wording and labels: [D-025](decisions/d-025-wp4-enable-opens-providers.md).

### WP5 Continue in another app (M, Python + frontend + desktop)

- Contract: `GET /v1/conversations/{id}/continuation?target=claude|chatgpt[&include_paths=0|1]` returns a deterministic, redacted prompt (cap 24,000 characters; no model call); a "Continue in another app…" dialog copies it or saves it as `.md`, then offers to open the installed desktop app.
- Decision on the desktop hand-off: [D-026](decisions/d-026-wp5-desktop-hand-off.md).

### WP6 Durable UI preferences (M, Python + frontend)

- Contract: `GET`/`PATCH /v1/ui-state`, local owner only, one store per owner in the state folder (never keyed by port or origin), atomic 0600 writes, allow-listed keys, tolerant reads, `localStorage` only as the theme's first-paint cache and for disposable per-launch state.
- Decisions: [D-023](decisions/d-023-wp6-preferences-in-backend-store.md) (store design), [D-024](decisions/d-024-wp6-limits-and-merge.md) (limits, merge and older builds).

### WP7 Visual markers (frontend + small Python)

- Contract: deterministic icons and names on run steps (file, edit, run, search, list, web, MCP, skill, agent, plan) from structured tool events, plus chips in assistant prose only for exact catalog names or known project paths, never inside code blocks. Tabler icons, `--th-*` tokens, an Appearance toggle `visual_markers` (on by default). UI strings stay English; the pt-BR README calls the feature "Marcadores Visuais". <!-- conventions: allow-pt -->
- Decision: [D-027](decisions/d-027-wp7-deterministic-visual-markers.md).

### WP8 Quota meters for every connected provider (frontend + small Python)

- Scope: one meter per connected or enabled provider in the rail; a provider that reports no quota shows a neutral "n/a" meter with the reason in its tooltip instead of disappearing; check that Claude's quota reaches the rail when Claude is connected.
- Decision: [D-028](decisions/d-028-wp8-quota-meters-every-provider.md).

### WP9 Interface language: English and Brazilian Portuguese (L, frontend + desktop + small Python)

- Requested by Sophia on 2026-10-06. A language selector in Settings translates the whole interface, including every message the app shows: errors, notices, toasts, dialogs, empty states, the admin panel and the desktop menus. English stays the default and the fallback for any missing string. The first extra language is `pt-BR`, and the design must make adding another language a data-only change: one catalog file per locale, keys instead of literal strings, plural and number/date formatting through `Intl`, and no layout that assumes English text length.
- The language is a preference in the WP6 store (`window.HarnessPrefs`, D-023), applied without a reload and also by the desktop main process (native menus and dialogs).
- Out of scope: the models' replies and the text sent to providers (prompts, system notices, continuation handoff), which follow the conversation, not the UI language. The repository rule "everything in English" still holds for code, identifiers and docs; translations live only in the locale catalogs.
- Needs a design (catalog format, string extraction from `agent_service/ui.js`, the admin panel and `desktop/`, a test that fails on an untranslated key) and a decision record before any code.

## Waves

- Wave 1: WP4 and WP5 backends (Python) in parallel with WP1 (frontend).
- Wave 2: WP2, then WP4 and WP5 UI, then WP7, one frontend implementer at a time (`agent_service/ui.js` is a single large file).
- Wave 3: WP6 after WP2; WP3 after WP2 and WP4's family key.
- WP8 was added in window 7 (2026-10-05); it is not yet placed in a wave.
- WP9 comes after WP3 (Sophia, 2026-10-06), so the Customize screens are built first and translated with the rest.

## Theme rule

KeepHarness has 8 palettes (`:root[data-palette=...]` in `harness_ui/assets/themes.css`). Every Codex-copied screen uses only the `--th-*` tokens; acceptance includes at least one light and one dark palette, text contrast at least 4.5, and no hard-coded colors in the diff ([D-020](decisions/d-020-theme-tokens-for-copied-screens.md)).

## KeepHarness-only features (protected in every review)

Multi-provider routing (Codex, Claude Code, DeepSeek, Gemini, local models) with per-conversation model and effort switch and route carry-over; 8 palettes; Space pages per project; harness agents (`@@name`) and the "/" palette across providers; workflows and the step engine; run console and Runs rail; access modes and approval cards; isolation indicator; provider quota meters; Attention bell with filters; Catalogs and vault; Connection / MCP and Connect a client; tailnet sharing and guest scope; backup and restore; schedules; the continuation prompt and cross-provider tool warnings; visual markers ([D-021](decisions/d-021-keep-keepharness-only-features.md)).

## Open questions

1. Does `codex://threads/new?prompt=` only prefill or also send? Unverified; until a manual check, ChatGPT always opens a blank new thread and the prompt stays on the clipboard ([D-026](decisions/d-026-wp5-desktop-hand-off.md)).
2. `HANDOFF_URL_LIMIT = 16000` is a conservative guess, not measured on Sophia's machine.
3. The Electron session: the desktop reference keeps it ephemeral; KeepHarness uses the persistent default session. Revisit only after every preference lives in the WP6 store.
4. WP3 survey gaps (see WP3) and WP8 wave placement.
5. Other Codex gaps outside this set (command menu `Ctrl+K` for commands and settings, thread row Pin, shortcuts dialog, composer `+` menu, Settings search, model popover) are unscheduled.

## Backlog (requested, not scheduled)

- Always on top (Sophia, 2026-10-06): a checkable "Always on top" item in the app menu that keeps the KeepHarness window above every other open window. Desktop only: `BrowserWindow.setAlwaysOnTop(true/false)` from the main process, state saved as a preference through the WP6 store (`window.HarnessPrefs`, D-023) and restored at launch. To check before building: whether Wayland (KDE) honours the request for a non-focused window, and whether Codex has an equivalent to copy (D-021: no KeepHarness feature is dropped either way).
