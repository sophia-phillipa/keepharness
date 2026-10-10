> Status: DRAFT (proposed with [D-056](decisions/d-056-provider-add-actions.md); Create plugin pending Sophia's approval)
> Job: When I am on the Plugins page and want a new marketplace or MCP server in Codex or Claude, I want to hand that action to the provider's own CLI from KeepHarness, so I can extend the real CLI state without a terminal and without KeepHarness pretending to own the action.
> Scope: `control/integration_catalog.py`, `control/operations.py`, `control/routes.py`, `control/customize.js`, `control/admin.css`, their tests
> Invariant docs: [D-034](decisions/d-034-wp3-customize-product-answers.md) §2, [D-038](decisions/d-038-keepharness-facade-over-provider-state.md), [D-039](decisions/d-039-single-owner-facade-policies.md), [D-045](decisions/d-045-plugin-directory-install-actions.md), [D-030](decisions/d-030-wp5-host-hand-off-in-containers.md), [D-020](decisions/d-020-theme-tokens-for-copied-screens.md) (`--th-*` tokens), [D-023](decisions/d-023-wp6-preferences-in-backend-store.md) (no `localStorage` prefs)
> Decision rationale: reuse the catalog read, `operation()` and `/api/integration`; the CLI's own `--help` decides what is offered.
> Kill criteria: remove this spec when KeepHarness gets native plugin/marketplace creation (D-034 §2 "future work") or when both CLIs stop exposing these verbs.

# Provider pass-through Add actions (#27)

## 1. Why

D-034 §2 asks for Codex's `Add` menu on the Plugins page, with every item handed to a provider CLI and shown only when that CLI can perform it. The decision, its alternatives and the JEV outcome are in [D-056](decisions/d-056-provider-add-actions.md).

| Choice | Rationale |
| --- | --- |
| Capabilities travel in the catalog response | No new route; D-045's "no CLI on render beyond the catalog read" holds |
| `--help` probe, memoized per binary stamp | Follows the installed CLI version at a cost of two processes per CLI upgrade |
| Probe failure hides the item | D-034 §2: impossible actions do not appear |
| Source = `owner/repo` or public HTTPS only | Narrowest remote form; no filesystem or SSH key reach |
| Empty working directory for the operation | A relative `owner/repo` cannot resolve to a local folder |
| Create plugin designed but gated | Writes the owner's real `~/.claude/skills`; needs Sophia's approval |

## 2. How

```
Plugins load / Refresh ──POST /api/integration-catalog──▶ catalog(provider, binary)
                                  gather(_run mcp list, _run plugin list --available --json,
                                         probe(binary) ── memo hit? ──▶ no CLI
                                                       └─ miss ─▶ _run plugin --help, _run plugin marketplace --help)
                                  ◀── {items, warnings, actions: ["connector_add", "marketplace_add"]}
customize.js: Add button + menu built from union of actions (no CLI)
Dialog submit ──POST /api/integration {provider, action:"marketplace_add", source}──▶ change_integration
                                  capability re-check (memo only) → operation() → launch(argv, cwd=<state>/operations-cwd)
                                  ◀── job {id, state, output, accepts_input}  (Operations list, as today)
```

### Contracts

**C1 `integration_catalog.parse_subcommands(text: str) -> frozenset[str]`** (new, pure)
- Purpose: subcommand names listed in a CLI help text.
- Guarantees: reads the first 64 KiB only; finds a `Commands:` or `SUBCOMMANDS:` heading (case-insensitive) and takes, from each following line indented by two or more spaces, the first token. Strips `[options]` and `<args>`, splits aliases on `|` and `,`, keeps only `[a-z][a-z0-9-]{0,31}`, and stops at the first non-indented, non-blank line. Holds both commander (Claude, `install|i [options] <plugin>`) and clap (Codex, `add  Install a plugin`) layouts.
- Must-not: raise; execute anything; accept tokens from description columns.
- Errors: no heading or no match → empty set.

**C2 `async integration_catalog.capabilities(provider: str, binary: str) -> frozenset[str] | None`** (new)
- Purpose: action ids the CLI supports for Add; `None` = unknown (probe failed).
- Guarantees: key = `(provider, os.path.realpath(binary), st_size, st_mtime_ns)`; a memo hit runs no process. On a miss, runs `_run(binary, "plugin", "--help")` and `_run(binary, "plugin", "marketplace", "--help")` concurrently (same 15 s bound, stdin closed, stderr dropped, process group killed on timeout). Yields `"marketplace_add"` when `"marketplace" ∈ C1(plugin help)` and `"add" ∈ C1(marketplace help)`, with exit 0 on both. Memoizes only a successful probe (both exits 0 and at least one subcommand parsed).
- Must-not: run at render, menu open or `change_integration`; memoize a failure (the next Refresh retries); add `plugin_create` before P7 is approved.
- Errors: `stat` failure, timeout or non-zero exit → `None`, no memo, no warning text to the UI (the item simply does not appear).
- Synchronous lookup for the server re-check: **`capability_cached(provider, binary) -> frozenset[str] | None`** reads the memo only (stat, no process).

**C3 `catalog(provider, binary, fallback=None) -> dict`** (changed, additive)
- Guarantees: result gains `actions: list[str]`, sorted, subset of `{"connector_add", "marketplace_add"}` (plus `"plugin_create"` after P7). Contains `"connector_add"` iff the `mcp list` read exited 0 and parsed (not the fallback). Contains `"marketplace_add"` iff C2 returned a set holding it. The probe runs inside the same `asyncio.gather` as the two reads, so wall time stays bounded by the existing 15 s. The unsupported-provider and missing-binary early returns give `actions: []`.
- Must-not: change `items` or `warnings`; include argv, help text, paths or environment.

**C4 `operations.marketplace_source(value: object) -> str`** (new, pure)
- Accepts exactly one of:
  - GitHub shorthand: `^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}$`, where the repository part is not `.` or `..`, does not start with `.` and does not end with `.json`;
  - HTTPS URL: `value.startswith("https://")` and `integration_catalog.public_source(value) == value`. This rejects userinfo, query, fragment, controls, whitespace, backslashes, local or non-global hosts and anything over 500 characters, because the sanitizer would change or drop it.
- Returns the value unchanged; raises `UserMessageError("Use a GitHub owner/repo or a public HTTPS address, without credentials, query or fragment.")` otherwise (non-string, empty, leading `-`, `/`, `~`, `.`, `file:`, `ssh:`, `git@`, `http:`).
- Must-not: normalize or rewrite the input (no `.git` suffix, no URL expansion).

**C5 `operation(binary, provider, data) -> list[str]`** (changed)
- New branch, evaluated **before** the `name` check: `action == "marketplace_add"` → `[binary, "plugin", "marketplace", "add", marketplace_source(data.get("source"))]` for both `codex` and `claude`.
- Must-not: add any flag (`--force`, `--scope`, `--`), read `name` for this action, or change any existing branch's argv.

**C6 `Operations.launch(args, timeout=300, *, env=None, cwd=None, ...)`** (changed, additive)
- Passes `cwd` to `asyncio.create_subprocess_exec`; default `None` keeps today's behavior for every caller.

**C7 `routes.change_integration(request, manager, data)`** (changed)
- For `action == "marketplace_add"`: if `"marketplace_add" not in (capability_cached(provider, binary) or ())`, raise `UserMessageError("Adding a marketplace is not available for this provider. Refresh Plugins and try again.")`. Otherwise launch with `cwd=manager.state / "operations-cwd"`, created with `mkdir(mode=0o700, exist_ok=True)` and expected empty (it is never written by KeepHarness).
- Audit stays `integration:<action>`, written after `operation()` validated the action; never the source or name.
- Response: the job dict only (`id`, `state`, `output`, `accepts_input`), as today.
- Owner-only by `admin_guard` (loopback host, same origin, admin cookie); counted in `LIMITED_OPERATIONS`.

**C8 `customize.js` Add menu** (new UI)
- `#plugins-add` button (`aria-haspopup="menu"`, `aria-expanded`) in `.plugins-toolbar`, rendered only when the union of `integrationCatalogs.get(id).actions` over `view.clis` is non-empty; items in Codex order, only those present: `Add a marketplace` (`marketplace_add`), `Add MCP server` (`connector_add`), and `Create plugin` (`plugin_create`, P7 only). Upload plugin archive and Create MCP App never exist in the DOM.
- Menu: `role="menu"`, items `role="menuitem"`, ArrowUp/ArrowDown wrap, Escape closes and returns focus to `#plugins-add`, reusing the `openMenu`/`closeMenu` pattern.
- Each item opens a native `<dialog>` (`#plugins-add-dialog`): provider `<select>` listing only providers whose `actions` contain the item (hidden, with a text label, when only one); fields: marketplace → `source` text input; MCP server → `name`, transport (`HTTPS` / `Local process`), URL or JSON command list, the same client checks as `admin.js` `integration-run`. Submit posts `request("integration", {...})`, closes the dialog, reports "Started in <Provider>. Track Operations, then Refresh." and calls `pollOperations()`; a 400 shows the server's message inside the dialog (`role="alert"`) and keeps the input.
- Styling: `--th-*` tokens only; contrast ≥ 4.5:1 in Paper and Graphite; works at 390 px.
- Must-not: call any API on render or menu open; write `localStorage`/`sessionStorage`; remember the last provider or source (no preference is needed).

**C9 Create plugin — PENDING SOPHIA'S APPROVAL (P7; excluded from P2–P6)**
- Probe: `"plugin_create"` when provider is `claude` and `{"init","new"} ∩ C1(plugin help)` is non-empty; the verb used is `init` if listed, else `new`. Codex: absent.
- `operation` branch: `[binary, "plugin", verb, name]` with `name` matching `^[a-z0-9][a-z0-9-]{0,63}$`.
- Pre-check in `change_integration`: refuse with "A skill named <name> already exists." when `<claude home>/skills/<name>` exists, with `<claude home>` resolved by the same provider-state reader as D-038 (honors `CLAUDE_CONFIG_DIR`). No `--force`; the CLI never gets the chance to overwrite.
- Effect to accept explicitly: a new folder in the owner's real `~/.claude/skills/`, loaded by every Claude session, including those outside KeepHarness.
- Tests use a fake home only, never the owner's `~/.claude`.

### Error mapping

| Condition | Where | Owner sees |
| --- | --- | --- |
| Not loopback / wrong origin | `admin_guard` | 403 "Management is only available on this machine." / "Unauthorized origin." |
| No admin cookie | `admin_guard` | 401 open hint |
| Provider not `codex`/`claude`, CLI missing | `change_integration` | 400 "Invalid provider." / "CLI not installed." |
| Capability not memoized or not supported | `change_integration` (C7) | 400 "Adding a marketplace is not available for this provider. Refresh Plugins and try again." |
| Invalid source | `marketplace_source` (C4) | 400 C4 message, shown in the dialog |
| Another operation running | `LIMITED_OPERATIONS` | existing 429 with Retry-After |
| CLI exits non-zero | `Operations.launch` | job `failed`, CLI output (ANSI stripped, last 12,000 chars) in Operations |
| CLI hangs | `Operations.launch` | job `failed`, "Timed out. Try again." |
| Probe fails or times out | `capabilities` (C2) | item absent; no warning |

### Failure modes

- *Someone moves the `marketplace_add` branch below the `name` check* — every request fails "Invalid name." because the action has no name. P3 has a test for this.
- *Someone "simplifies" C4 by stripping the query instead of rejecting it* — `https://host/repo?token=…` would reach the CLI after the owner believed it was refused, or the CLI would get a different URL than the one typed. C4 compares the sanitizer output with the input for equality.
- *Someone runs the probe in `enter()` or on menu open to "keep it fresh"* — this breaks the D-045 render contract and the request allow-list assertion in `admin-customize.spec.cjs`. Freshness comes from the binary stamp in the memo key.
- *Someone renders the item disabled when the probe fails* — that contradicts D-034 Q2. Absent is the only state.
- *Someone adds `--force` or `--scope user` to "make it work"* — this changes the CLI's own default and can overwrite an existing marketplace. The argv is constant.
- *Someone memoizes probe failures* — one slow first start would hide the item until restart. Only successes are memoized.
- *Someone accepts SSH because the CLI does* — a text field would then reach the owner's SSH keys. That needs a new decision.

### Scenario band (complexity 5 → full, ≥10)

Score: 6+ files (+3), new behavior (+1), crosses the catalog/operations/routes/UI boundary (+1) = 5. Override also applies (API response shape changes).

| # | Category | Trigger | Predicted failure | Mitigation (where) | Verdict |
| --- | --- | --- | --- | --- | --- |
| 1 | happy_path | Codex lists `marketplace`; owner adds `acme/plugins` | — | C2 → C3 → C8 → C5/C7 | safe |
| 2 | edge_case | Source `docs/notes` with a `docs/notes` folder in the admin cwd | CLI reads a local folder | empty `operations-cwd` (C7, C6) | safe |
| 3 | invariant_violation | Source `../../etc` or `/home/x/mkt` or `~/mkt` | local path reaches the CLI | C4 regex (repo cannot start with `.`, owner must start alphanumeric) | safe |
| 4 | invariant_violation | Source `-c=evil` or `--force` | option injection | C4 (no leading `-`, regex) | safe |
| 5 | boundary | `https://u:p@host/r`, `https://host/r?x=1#f`, `https://127.0.0.1/r`, `https://host.local/r` | credentials or local hosts reach the CLI | C4 equality with `public_source` | safe |
| 6 | error_path | `plugin --help` times out | catalog slower, or item disabled | gather keeps the 15 s bound; `None` → absent, not memoized (C2, C3) | safe |
| 7 | edge_case | CLI upgraded while the admin runs | stale memo shows a removed verb | key includes size and mtime (C2); a stale tab is still refused by C7, which re-stats | safe |
| 8 | concurrency | Two tabs submit at once | two CLI writes race on the CLI's config | existing `ADMIN_OPERATION_LIMIT` / 429; the CLI owns its file locking (D-038) | accepted residual |
| 9 | integration | Admin restarted, tab still open, owner submits | launch without a probe | C7 memo miss → 400 "Refresh Plugins" | safe |
| 10 | error_path | CLI prints the URL or an env value in its error | echo in Operations | output is the owner's own CLI output (as for install today); response carries no argv or env; audit carries action only | accepted |
| 11 | integration | `admin-customize.spec.cjs` request allow list | new endpoint breaks the assertion | no new endpoint (C3) | safe |
| 12 | edge_case | DeepSeek only, or no CLI found | empty menu button | button absent when the union is empty (C8) | safe |
| 13 | invariant_violation | Help text description mentions "marketplace" in prose | false positive | C1 reads only the first token of indented lines under the heading | safe |
| 14 | integration | Pre-existing: `plugin_install`/`login` `name` allows `./x` or `a/../b` | local path passed to `plugin install` | out of scope; routed to P5 as a finding | open |

### Integration points

- d=1: `integration_catalog.catalog` ← `routes.read_integration_catalog`, `manager` (installed plugins share `_run`); `operations.operation` ← `routes.change_integration`; `Operations.launch` ← all admin operations (login, model install, integration).
- d=2: `customize.js` `loadCatalogs`/`requestCatalog` and the Providers page share `integrationCatalogs` and `catalogPending`; `admin.js` `integration-run` posts the same `/api/integration`.
- d=3: `LIMITED_OPERATIONS` and `ADMIN_OPERATION_LIMIT`; the request allow list in `tests/admin-customize.spec.cjs`; the real CLI state read by `provider_state` after a marketplace is added (the next Refresh shows its plugins in the directory, D-045).

## 3. What

| Item | Path / value |
| --- | --- |
| Probe and memo | `control/integration_catalog.py` (`parse_subcommands`, `capabilities`, `capability_cached`) |
| Source check and argv | `control/operations.py` (`marketplace_source`, `operation`, `Operations.launch(cwd=)`) |
| Route | `control/routes.py::change_integration` (`POST /api/integration`, action `marketplace_add`, field `source`) |
| Catalog field | `POST /api/integration-catalog` → `actions: list[str]` |
| Empty working directory | `<state>/operations-cwd`, mode 0700 |
| UI | `control/customize.js` (`#plugins-add`, `#plugins-add-dialog`), `control/admin.css` |
| Env vars | none new |

### Test protocol

- Syntax and lint: `.venv/bin/python -m ruff check control tests` (if configured), `node --check control/customize.js`.
- Unit: `.venv/bin/python -m pytest -q tests/test_integration_catalog.py tests/test_control.py` (plus `tests/test_add_actions.py` if P3 creates it); patch `control.integration_catalog._run` with commander- and clap-shaped help fixtures. No real CLI.
- Browser: `PYTHON="$PWD/.venv/bin/python" ./scripts/test-ui.sh` (or the single spec `tests/admin-customize.spec.cjs`) with `/api/integration-catalog` stubbed to return `actions`.
- Full Python and browser suites before merge to `main`.

## 4. Contracts checklist for review (P5)

- [ ] No CLI process at render, menu open or dialog open (spec request log).
- [ ] `operation()` argv for `marketplace_add` is exactly five elements; no flags.
- [ ] C4 rejects every row of the boundary table above (parametrized test).
- [ ] Response and audit carry no argv, source, name or environment.
- [ ] Probe failure → item absent; never `disabled`.
- [ ] No `localStorage`/`sessionStorage` writes added; only `--th-*` colors.
- [ ] `plugin_create` appears nowhere in the diff unless P7 was approved.

## 5. Work packages

Each package is one branch and one implementer brief, with exactly one `Done when:` line. P1 is this design.

**P2 — Capability probe (pytest).** `parse_subcommands`, `capabilities`, `capability_cached`, the `actions` field in `catalog()` (`connector_add`, `marketplace_add` only). Tests: commander and clap help fixtures; description prose is not parsed; non-zero exit, timeout and garbage → `None`, nothing memoized; memo hit runs no `_run` (call count); stamp change re-probes; `actions` empty on missing binary or unsupported provider; `connector_add` absent when `mcp list` fell back.
Done when: `.venv/bin/python -m pytest -q tests/test_integration_catalog.py` passes with the new probe tests, and an existing catalog test still asserts no command or secret in the result.

**P3 — `marketplace_add` argv branches (pytest).** C4, C5, C6, C7. Tests: exact argv for codex and claude; branch reached without `name`; parametrized accept/reject table (row 3–5 inputs, `http:`, `git@`, `ssh://`, `file:`, `owner/.hidden`, `owner/x.json`, 101-char repo, non-string); capability memo miss → 400; launch receives `cwd=<state>/operations-cwd`; owner-only refusal of `/api/integration` (`marketplace_add`) without cookie → 401 and with a non-loopback host → 403; audit string has no source.
Done when: `.venv/bin/python -m pytest -q tests/test_control.py tests/test_admin_security.py` passes including the new marketplace_add, refusal and owner-only tests.

**P4 — Add menu UI (Playwright, `tests/admin-customize.spec.cjs`).** C8. Named scenarios: `add-menu-absent-without-actions`, `add-menu-lists-only-supported-items` (upload and MCP App not in the DOM), `add-marketplace-posts-exact-body` (`{provider, action: "marketplace_add", source}`), `add-marketplace-provider-picker-only-capable`, `add-marketplace-server-error-stays-in-dialog`, `add-mcp-server-https-and-stdio-bodies`, `add-menu-keyboard-and-focus-return`, `add-dialog-paper-and-graphite-aa-contrast`, `add-dialog-mobile-390`; the existing request allow-list assertion stays unchanged.
Done when: `npx playwright test tests/admin-customize.spec.cjs` passes, with the named scenarios, under `scripts/test-ui.sh`.

**P5 — Security review gate (Opus).** `backend-review-engineer` reviews the P2–P4 diff against section 4 and D-056 (option injection, path and SSH reach, echo, owner-only, render-time CLI calls); routes scenario 14 as a separate issue.
Done when: the review returns APPROVED with no open blocking or major finding, recorded in the PR.

**P6 — Visible pass (D-029).** On Sophia's screen: Codex and Claude each show `Add a marketplace` and `Add MCP server`; DeepSeek contributes nothing (SKIP with the reason "no catalog command"); a rejected source shows the message in the dialog; one accepted source starts an operation that completes and appears after Refresh. The test source and its later removal (`plugin marketplace remove`) need Sophia's go-ahead, because this writes the real CLI state (D-038).
Done when: the visible-pass log lists Codex, Claude and DeepSeek with PASS or SKIP-with-reason for every step and no unexplained failure.

**P7 — Create plugin (Claude). PENDING SOPHIA'S APPROVAL; not part of P2–P6.** C9 end to end with pytest (fake home) and one Playwright scenario.
Done when: Sophia has approved D-056's Create plugin item and `pytest` plus the P7 Playwright scenario pass against a fake Claude home.

## See also

[D-034](decisions/d-034-wp3-customize-product-answers.md), [D-038](decisions/d-038-keepharness-facade-over-provider-state.md), [D-045](decisions/d-045-plugin-directory-install-actions.md), [Codex parity design WP3](codex-parity-design.md#wp3-customize-screen-l-admin-frontend--small-python), [Codex app inventory](research/codex-app-inventory-2026-10.md).
