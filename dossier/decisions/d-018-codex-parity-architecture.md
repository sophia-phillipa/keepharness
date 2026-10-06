# D-018 — Build Codex-app parity on the existing shell, Settings dialog, admin and integrations API

Status: accepted. Date: 2026-10-05. Decided by: Sophia (answers to the design's product questions, [D-019](d-019-parity-product-answers.md)); design by the software-design-architect pass on main `dd775f2`. Spec: [Codex-app parity design](../codex-parity-design.md).

## Context

Sophia wants KeepHarness usable daily as a Codex-app replacement: Back and Forward, Settings as a submenu with Admin inside it, a Codex-like Customize screen, cross-provider tool warnings and a prompt to continue in another desktop app. Settings was already a modal that framed the admin panels; there was no back/forward; `GET /v1/integrations` answered for one backend only.

## Decision

1. Back/Forward: an app-owned view-history stack in `agent_service/ui.js`, not `window.history`.
2. Settings: the topbar button opens a submenu of sections; the admin's panels are ordinary Settings items; the separate admin window goes away.
3. Customize: a new admin panel in `control/admin.js` over the admin's existing endpoints, framed in Settings.
4. Cross-provider warnings: an additive `elsewhere` field on `/v1/integrations`, computed server-side.
5. Continuation prompt: a deterministic, redacted server-side template built from `portable_history`; no model call.

## Rationale

- Admin iframe `src` changes add entries to the joint session history and dialogs are not URL routes, so `history.back()` would walk iframe sections; Chromium exposes no `canGoForward` on `window.history`. A small stack is testable in Playwright without Electron.
- Install, remove, login and allow already live behind the admin's owner session and CSRF guard; proxying them through `agent_service` would add a second privileged write path.
- One request instead of one per provider, the reasons table stays in one place, and it is pytest-able; an additive field does not break existing clients.
- The continuation prompt must work exactly when a provider is failing, cost nothing and be auditable for redaction.

## Alternatives

- `window.history` or the Navigation API: native semantics, but polluted by iframe navigations and every dialog would need to become a route. Rejected.
- Customize rebuilt in the harness origin with a proxy to `control`: one DOM, but a new privileged write path. Rejected for 0.16.0; revisit if the iframe blocks a required interaction.
- Browser fan-out of `/v1/integrations` per backend: no server change, but N requests per menu open and duplicated reasons logic. Rejected.
- A model-written summary for the continuation: shorter, but needs a working provider and spends tokens. Rejected as default.

## Impact

Topbar, Settings nav, Plugins menu, a continuation dialog, an admin Customize panel, desktop menu and first-run target; one additive API field and one new GET route. Artifacts: `agent_service/ui.js`, `agent_service/index.html`, `agent_service/integrations_view.py`, `agent_service/conversation_context.py`, `agent_service/routes/conversations.py`, `control/admin.js`, `desktop/main.cjs`, [release notes](../releases/v0.16.0.md).

## Follow-up

WP1–WP8 in the [spec](../codex-parity-design.md). Revisit the iframe choice if Customize fails visual review twice; revisit the app-owned history if Codex keys history on something the stack cannot model.
