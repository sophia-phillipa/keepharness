# D-056 — Provider pass-through Add actions on the Plugins page

Status: proposed (awaiting Sophia). Date: 2026-10-10. Decided by: _pending_. Issue: #27 (parent #14, WP3). Refines [D-034](d-034-wp3-customize-product-answers.md) §2. Builds on [D-038](d-038-keepharness-facade-over-provider-state.md) (the CLIs' real state is the source of truth; D-034 §1 allow list superseded), [D-039](d-039-single-owner-facade-policies.md) (single owner), [D-045](d-045-plugin-directory-install-actions.md) (directory and install actions; its "W7 boundary" section hands the Create/Add/Upload actions to #27) and [D-030](d-030-wp5-host-hand-off-in-containers.md) (constant-argument pass-through). Spec, contracts and work packages: [Provider Add actions](../provider-add-actions.md).

## Context

D-034 §2 says the Plugins page `Add` menu offers Codex's five items (`Create plugin`, `Add a marketplace`, `Upload plugin archive`, `Create MCP App`, `Add MCP server`) only when the provider is installed locally and its CLI can perform the action; otherwise the item is absent, not disabled. D-038 later made KeepHarness a facade over the CLIs' real state, so every Add action writes the owner's real `~/.codex` or `~/.claude` through the CLI. D-045 kept rendering free of CLI calls beyond the existing catalog read.

What the code offers today (checked on `46b7c26`):

- `control/operations.py::operation` builds constant argv lists for `login`, `plugin_install`, `plugin_remove`, `connector_add` and `connector_remove`; it validates `name` first, for every action.
- `control/routes.py::change_integration` (`POST /api/integration`) launches that argv through `Operations.launch` and audits `integration:<action>`. `admin_guard` already enforces loopback, origin and the owner's admin cookie; `/api/integration` counts against `ADMIN_OPERATION_LIMIT`.
- `control/integration_catalog.py::catalog` (`POST /api/integration-catalog`) runs `plugin list --available --json` and `mcp list` once per Plugins load or Refresh, with a 15 s bound and stderr dropped.
- The only `connector_add` form lives in the provider wizard (`#integrations`, hidden outside it); `customize.js` has no Add menu.
- The fake CLIs in `tests/fixtures/` implement no `--help`, `marketplace` or `init` verb; pytest patches `integration_catalog._run`, and `tests/admin-customize.spec.cjs` stubs `/api/**`.

CLI verbs per the prior mapping, not re-run against the real CLIs (their state must not be touched): Codex has `plugin add`, `plugin marketplace add <SOURCE>` and `mcp add`; Claude has `plugin marketplace add`, `plugin init|new <name>` (scaffolds into `~/.claude/skills/<name>/`), `plugin install` and `mcp add`. Neither has an upload or "create MCP App" verb. The capability probe below makes the UI follow whatever the installed CLI actually lists, so this mapping only decides which verbs KeepHarness knows how to call.

## Decision

1. **Per item and provider:**

   | Item | Codex | Claude | Shown when |
   | --- | --- | --- | --- |
   | Add a marketplace | `[codex, "plugin", "marketplace", "add", SOURCE]` | `[claude, "plugin", "marketplace", "add", SOURCE]` | the probe finds `marketplace` in `plugin --help` and `add` in `plugin marketplace --help` |
   | Add MCP server | existing `connector_add` argv, unchanged | existing `connector_add` argv, unchanged | the catalog's `mcp list` read succeeded |
   | Create plugin | absent (no verb) | `[claude, "plugin", "init" or "new", NAME]` — **pending Sophia's approval** | after approval only: the probe finds `init` or `new` in `plugin --help` |
   | Upload plugin archive | absent | absent | never (no CLI verb) |
   | Create MCP App | absent | absent | never (no CLI verb) |

   DeepSeek, Gemini and Local have no catalog command, so they contribute no item.

2. **Marketplace source:** only a GitHub `owner/repo` or a public HTTPS URL. No local path, `file:`, SSH or `git@` form, userinfo, query, fragment, whitespace or control characters, and nothing starting with `-`. The argv is constant apart from the validated `SOURCE`; no `--force`, `--scope` or other flag is ever added. The operation runs with its working directory set to an empty directory that KeepHarness owns, so a relative `owner/repo` cannot resolve to a local folder.
3. **Capability probe:** at catalog read only, in parallel with the existing two reads, run `plugin --help` and `plugin marketplace --help` through the same bounded runner, parse the subcommand names and memoize the result per CLI binary (path, size and modification time). A probe error, timeout or unparseable text hides the item for that provider; it never shows a disabled item. Rendering, opening the menu and opening a dialog run no CLI. The server re-checks the memoized capability before launching, without running a CLI.
4. **Safety:** owner-only through the existing `admin_guard`; the response and the audit entry never echo argv, environment, source or name; errors use the existing `UserMessageError` messages and job states.
5. **UI:** an `Add` button with a menu in the Plugins toolbar (`customize.js`), holding only the available items; the button itself is absent when no item is available. Each item opens a native `<dialog>` whose provider picker lists only the providers able to perform it. Colors come from `--th-*` tokens only; the menu and dialogs are keyboard-operable. No UI preference is stored; if one is ever needed it goes in the WP6 backend store, never `localStorage`.
6. **Create plugin** is designed in full in the spec, but stays out of every implementation package until Sophia approves it, because it creates files in the owner's real `~/.claude/skills`.

## Rationale

- Reusing `operation()`, `Operations.launch`, `/api/integration` and `/api/integration-catalog` adds one argv branch, one probe and one field to an existing response instead of a new route, process runner or store. The rendering contract of D-045 and the request allow list asserted in `admin-customize.spec.cjs` stay intact because the capabilities travel inside the catalog response.
- Parsing `--help` lets the UI follow the installed CLI version: an older CLI without `marketplace` loses the item, a newer one gains it, and KeepHarness never claims an action the CLI cannot perform (D-034 §2). Memoizing per binary stamp keeps the cost to two extra processes per CLI upgrade, not per Refresh.
- Hiding on probe failure, rather than disabling, is D-034 §2's rule. A broken probe can only remove an item, never enable a write.
- The source allow list is the narrowest form both CLIs accept for a remote marketplace. Local paths and SSH would let a pasted string point the CLI at the owner's filesystem or keys. Checking HTTPS URLs means reusing `integration_catalog.public_source`, already reviewed for D-045, and accepting a URL only when the sanitizer returns it unchanged.
- The empty working directory closes the one ambiguity regex validation cannot: whether a CLI treats `docs/notes` as a GitHub repository or a relative folder. That parsing rule could not be verified here (the JEV abstained, confidence 0.28; see Alternatives).

## Alternatives considered

- **Probe at render or menu open:** each Plugins visit would run two more CLI processes and break D-045's "no CLI on render beyond the catalog read". Rejected.
- **Static capability table per provider (no probe):** cheapest, but it shows actions an older or newer CLI lacks, and those fail only after the click. That is the dead control D-034 §2 rejected. Rejected.
- **New `/api/integration-actions` endpoint:** a clean separation, but it is a new route, guard entry and spec allow-list change for one list of strings. Rejected in favor of an additive `actions` field in the catalog response.
- **Pass `owner/repo` verbatim and rely on the CLI parser:** fewer changes, but it depends on unverified CLI behavior when a same-named folder exists in the admin's working directory. Rejected.
- **Expand `owner/repo` to `https://github.com/owner/repo.git`:** removes the ambiguity, but it is unverified that Codex accepts git HTTPS URLs, and it rewrites the owner's input. Rejected; revisit if the empty working directory proves insufficient.
- **Show absent items disabled with a reason:** rejected by Sophia in D-034 Q2.
- **Reuse the wizard's `#integrations` form for Add MCP server:** zero new form code, but the form is hidden outside the provider wizard, so the click would leave the Plugins page. Rejected for a small dialog that posts the same `connector_add` body.
- **Allow SSH and local marketplace sources (the CLIs accept them):** more parity, but it gives a text field a path into the owner's filesystem and SSH keys. Rejected for 1.0; revisit on explicit request.

JEV: one `request_decision` (empty working directory vs verbatim vs URL expansion, risk medium) abstained at confidence 0.28 against the 0.80 required. Local decision: empty working directory, the only candidate that does not depend on unverified CLI behavior.

## Impact

- `control/integration_catalog.py`: probe, help parser, memo, `actions` field in `catalog()`'s result (additive).
- `control/operations.py`: `marketplace_add` branch, dispatched before the `name` check; `Operations.launch` gains an optional `cwd`.
- `control/routes.py::change_integration`: capability re-check and empty working directory for `marketplace_add`.
- `control/customize.js`, `control/admin.css`: Add menu and dialogs.
- Tests: `tests/test_integration_catalog.py`, `tests/test_control.py` (or a new `tests/test_add_actions.py`), `tests/admin-customize.spec.cjs`.
- No schema, settings or persisted-state change; no new dependency, route or store.

## Follow-up actions

- Packages P2–P6 in [Provider Add actions](../provider-add-actions.md#5-work-packages); P7 (Create plugin) only after Sophia approves it.
- Opus security review (P5) is a merge gate, per #27's acceptance.
- Release notes entry in `dossier/releases/` for the version that ships it.
- Revisit if a CLI adds an upload or MCP App verb (add a row and a probe token), if the CLIs change `--help` layout (the parser hides items; P2's fixture tests must be updated), or if Sophia asks for SSH or local marketplace sources.

## Implementation notes (2026-10-10)

Recorded while D-056 stays proposed; they do not change the decision.

- **Add MCP server reuses the provider wizard (deviation from C8).** The shipped menu entry opens the provider wizard at its integrations step (`openWizard` + `showStep(3)`) instead of a separate dialog. The alternative above was rejected only because the form is hidden outside the wizard; opening the wizard brings it into view and avoids a second `connector_add` form that would drift. JEV chose this (option A, confidence 0.96). The P5 review found it safe: it is UI navigation only and posts through the existing `/api/integration` name checks.
- **P5 security review (Opus): approved**, with 0 blockers and 0 majors. Fixed before merge: `operations-cwd` must be a real, empty 0700 directory and is refused otherwise (no symlink), and `_public_source` was renamed to `public_source`. Deferred: deduplicating `openAddMenu`/`openMenu` in `customize.js` (#84), and using the empty working directory for every `/api/integration` action (#85).
