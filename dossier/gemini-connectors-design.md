> Status: DRAFT (design for review; no code yet)
> Job: When I run a Gemini conversation in one of my projects, I want Gemini to use the MCP connectors already configured in my own Gemini CLI settings, with the same owner approval gates I get from Codex and Claude Code, so I can use my connectors from KeepHarness without giving up control over what runs.
> Scope: `adapters/gemini/` (policy.py, native.py; a new small `connectors.py`), `control/integrations.py`, `agent_service/integrations_view.py`, the Gemini branch of the approval handler in `agent_service/services/conversation_service.py`, `tests/test_gemini*.py`, the Plugins/Connectors UI. Out of scope: Codex and Claude behavior (unchanged), DeepSeek, local models, scheduled-run policy (unchanged: no connectors), project-scope Gemini settings (see Open questions).
> Invariant docs: `dossier/decisions/d-039-single-owner-facade-policies.md` (trust follows the CLIs; read-only item 6), `dossier/trust-mcp-approval-validation.md` (#44 trust and MCP approval), `dossier/releases/v0.16.0.md` lines 28, 49-50 and 2299-2301 (#46 fail-closed; host connectors ask before every call).
> Decision rationale: Gemini has no per-project approval store that KeepHarness can write to safely (the CLI's `trust: true` skips all tool confirmations), so approvals are KeepHarness-owned, digest-bound and default-deny. This is the one deliberate deviation from the D-039 "CLI state first" principle, recorded as an open decision (Open question 1).
> Kill criteria: remove this design if (a) the P0 spike shows the CLI's ACP permission requests cannot identify the MCP server (then only the allow-list path remains and the design is re-scoped to "no connectors"); (b) the Gemini CLI is no longer the CLI the owner uses (the MCP docs state that Gemini CLI was replaced by Antigravity CLI for some users on 2026-06-18, unverified here); (c) a Gemini CLI version lets `trust: true` or an unapproved project server run without any ACP permission request and admin policy cannot prevent it.

# Gemini native connectors through the facade (issue #71)

## 1. Why (motivation and context)

**Problem.** Since #46, Gemini runs with no MCP connectors. Fail-closed is the current behavior:

- `adapters/gemini/policy.py:89-90` always passes `--allowed-mcp-server-names keepharness-none-<uuid>`, a placeholder that matches no server, so the CLI starts none.
- `adapters/gemini/native.py:182` maps the ACP permission kind `other` (connectors) to `False`, and `native.py:184-196` only approves it through the owner callback when access is `ask` or `auto`, and only for `execute`. In practice connector calls are rejected.
- `agent_service/integrations_view.py:68-69` says "Read-only access turns connectors off for Gemini" and `integrations_view.py:38` lists only `codex` and `claude` as `NATIVE_BACKENDS`, so every Gemini connector is shown with the generic "does not use host connectors" reason (`integrations_view.py:34, 83-85`).
- Release notes confirm this is intentional and temporary: `dossier/releases/v0.16.0.md:2299-2301` ("connectors are off (fail-closed) ... native Gemini connectors are a follow-up").

**What the owner wants.** Gemini should use the connectors she already configured in the Gemini CLI, under the same approval gates as Codex and Claude Code. In those CLIs the rule is that host connectors ask before every call (`dossier/releases/v0.16.0.md:28`), and project MCP servers need an explicit approval that KeepHarness writes into the CLI's own store (`dossier/decisions/d-039...md:16`, `dossier/trust-mcp-approval-validation.md:10-18`).

**Key constraint discovered.** Gemini CLI settings have a per-server `trust` flag, which per the Gemini CLI MCP docs "bypasses all tool call confirmations for this server" (https://geminicli.com/docs/tools/mcp-server/). Writing it would silently remove the gate. The same docs describe only a user-scope and a project-scope `settings.json` with an `mcpServers` object, plus user-side `mcp.allowed` / `mcp.excluded`, and no per-project approval store. Claude and Codex keep approvals in their own stores (`adapters/claude/state.py:990-996`, `adapters/codex/state.py:91`); for Gemini there is no equivalent that KeepHarness can use safely. That is the root of the trust-model question.

**Relation to other systems.** The owner is single-owner (D-039, decision point 1). KeepHarness is a facade: it reads the CLI's real state and runs the CLI's own MCP client. The design keeps that: KeepHarness never starts a connector itself, it only decides which names the CLI is allowed to start and which calls the owner approves.

### Decisions in this spec (with rationale)

| # | Decision | Rationale | Rejected alternative |
|---|---|---|---|
| D1 | Connector definitions are read from the Gemini user settings file, read-only. | Matches the existing reader at `control/integrations.py:21-28`; no new config format. | Copying connectors into a KeepHarness config (duplicates the owner's source of truth). |
| D2 | Default-deny. A connector is usable only after an owner approval bound to (project, connector name, definition digest). | Fail-closed, like #46. The digest makes a changed definition re-ask. | Approve-all-once (a later edit to the server command would run unreviewed). |
| D3 | Approval state lives in a KeepHarness owner-state file, digest-only, HMAC-keyed. Nothing is written into Gemini's settings. | Gemini CLI documents no per-project approval store; writing `trust: true` removes the gate. | Writing `trust: true` into `~/.gemini/settings.json` (rejected, bypasses confirmations and is global, not per project). |
| D4 | Per-call approval is kept. An approved connector still asks on every call in `ask` and `auto` modes. | Same as Codex/Claude ("host connectors ask before every call", release notes line 28). | A persistent per-call rule (`rule_key` only covers command execution today, `agent_service/approval_policy.py:14-27`). |
| D5 | The CLI's own `--allowed-mcp-server-names` is the first fence; the ACP permission request is the second. | Unapproved servers never start; approved ones still need a per-call decision. Two independent checks. | One fence only (either alone leaves a gap if the other is misconfigured). |
| D6 | Scheduled runs keep no connectors. | Already the behavior (`agent_service/services/conversation_service.py:2775-2776` denies unattended requests; release notes line 50). | Allowing connectors in unattended runs (no owner present to ask). |

### Ponytail: deliberately not introduced

- No KeepHarness MCP proxy or broker in front of Gemini. The CLI already runs its MCP transports; a proxy would add a process, a protocol and a failure surface for no gain.
- No new SQLite table or migration. A single JSON file under the owner-state directory with atomic writes is enough (the same pattern as the other owner state files).
- No new dependency. `hashlib` and `hmac` are standard library.
- No cross-provider connector framework. The existing `integrations_view.py` shape (items, reasons, warnings) is extended, not generalized.
- No per-tool allow-list in v1 (see Open question 6).
- No project-scope Gemini connectors in v1 (Open question 2).
- No writing of Gemini trust or approval fields into any Gemini settings file.

## 2. How (architecture and design)

### 2.1 Data flow

```
~/.gemini/settings.json (owner, read-only)
   |  control/integrations.py:21-28 configurations()  [extend to digest]
   v
connectors.inventory()  ->  name, transport, status, definition_digest (no env/header values)
   |                                  \
   |  agent_service/integrations_view   \  cross-provider name warnings (read_inventory warnings list)
   v                                     
Owner approves (route) -> owner-state file: {project_id, name, digest_hmac, approved_at}
   |
   v  at each Gemini run start
connectors.allowed(project, home) -> approved names whose current digest matches, trust != true
   |                                  \
   v                                   v
policy.prepare(..., allowed)        conversation approval handler (per request)
  --allowed-mcp-server-names <names>   kind "other": check allowed() again (fail closed),
  or placeholder when empty            then owner callback if access is ask/auto
  mcpName = "<name>" ask_user rules    full: auto only when backend unrestricted
   |
   v
gemini --acp (CLI runs its own MCP client for the allowed servers only)
```

### 2.2 Trust model (recommendation, default A)

Approval key: `(project_id, connector_name, digest)` where `digest = HMAC-SHA256(local_key, canonical_json(spec))`. `canonical_json` covers `command`, `args`, `url`, `httpUrl`, `headers`, `env` (keys and values), `cwd`, `timeout`, `trust`, `includeTools`, `excludeTools`. The local key is a random 32-byte value in the owner-state directory with mode 0600, so a low-entropy secret in `env` cannot be brute-forced from the stored digest.

Lifecycle:

1. **Discover.** Inventory shows the connector as `not approved`.
2. **Approve.** The owner approves in the Plugins/Connectors UI. The route records the digest that the UI displayed. If the file changed between display and approval, the route refuses (`gemini_connector_changed`) and the UI reloads.
3. **Use.** At run start and on each ACP permission request, the stored digest must equal the digest of the current file contents. Otherwise the connector is not allowed.
4. **Change.** Any change to the definition changes the digest. The connector drops out of the allowed set and shows `changed since approval`. It is re-approved only by the owner.
5. **Remove.** If the connector disappears from the file, its approval record is pruned on the next inventory read. Re-adding it asks again.
6. **Revoke.** The owner can revoke at any time. Revocation takes effect on the next ACP permission request (fail-closed check, not only at run start). The run continues without that connector.
7. **Refused definitions.** A connector whose spec sets `trust: true` is never enabled by KeepHarness. The UI says the owner must turn that flag off in the Gemini settings if she wants the connector through KeepHarness. KeepHarness never writes that flag.

Why one gate for Codex, Claude and Gemini: the owner-facing gate is the same set of objects Claude and Codex already expose: a `trust` block plus an `mcp_approvals` list of `{server, approved, enabled}` (`control/provider_state.py:613-631`), served from the owner route. Gemini adds its rows to that same shape, backed by the owner-state file instead of a CLI store. The runtime half is the same approval handler (`conversation_service.py:2769-2830`).

### 2.3 Reaching `--allowed-mcp-server-names` and ACP permission requests

- `policy.prepare` (today `adapters/gemini/policy.py:25`) gains an `allowed_connectors` argument (default empty tuple, so current callers keep the fail-closed behavior). If non-empty, the argument value is the sorted list of approved names joined with commas. If empty, the placeholder stays.
- The admin policy file (`policy.py:52-60`) keeps `toolName = "*"` deny at priority 900 and the existing `ask_user` rules. For each allowed connector it adds a rule with `mcpName = "<name>"` and `decision = "ask_user"` (or `allow` only under full access with an unrestricted backend). The Gemini docs recommend `mcpName` for MCP policy and state that the highest priority wins; the exact interaction with `toolName = "*"` must be confirmed in P0.
- In `adapters/gemini/native.py`, the `other` branch (`native.py:182` and `184-196`) is changed: a connector request is approved only if (i) the server name can be read from the ACP request, (ii) that name is in `connectors.allowed()` right now, and (iii) the mode is `ask` or `auto`, or `full` with an unrestricted backend. Otherwise the request is rejected (`native.py:198` sends `reject_once`). If the server name cannot be read, the request is rejected (fail closed).
- `session/new` and `session/load` send `"mcpServers": []` (`native.py:298, 317`). The CLI therefore loads servers from its own settings. KeepHarness does not inject servers over ACP. This is the reason the allow-list is the first fence.
- `read_only` keeps connectors off (default, Open question 4). The current code already rejects `other` in `read_only` (`native.py:184-196` only approves read, search and fetch there).

### 2.4 Read-only list and cross-provider warnings

- Source: user scope only, `~/.gemini/settings.json` `mcpServers` (`control/integrations.py:21-28`). Project scope is shown only as a count with a note, not read (Open question 2).
- The inventory keeps today's public keys (`control/integrations.py:32-46` returns id, name, kind, transport, status). The design adds `status` values `not approved`, `approved`, `changed since approval`, `refused (trust: true)`, `disabled` and `definition_digest` as a short prefix only.
- `integrations_view.read_inventory` (`agent_service/integrations_view.py:104-117`) already filters with `PUBLIC_KEYS` and returns a `warnings` list. The design adds a Gemini entry to that list and extends `route_limits` (`integrations_view.py:68-69`) so Gemini no longer gets the generic reason when the owner has approved a connector.
- Cross-provider warnings: `bare_name` (`integrations_view.py:172-180`) already matches the same tool name across providers. When a Gemini connector name equals a Codex or Claude connector name with a different transport or digest, show a warning. Never merge the two; never auto-approve one from the other.
- Usage attribution (`integrations_view.py:140-146`) keeps Gemini tools under "other tools", since Gemini tool names carry no server. No change in v1.

## 3. What (implementation reference)

### Contracts (function-level guarantees)

All signatures are proposals; names are provisional until `dossier/naming-model.md` is checked in P2.

**`connectors.inventory(home: Path) -> list[ConnectorItem]`** (new, `adapters/gemini/connectors.py`)
- Purpose: read user-scope `mcpServers` and return public items.
- Guarantees: read-only; never writes; returns no `env` or `headers` values; never raises on a malformed file (returns `[]` and a warning code).
- Must-not: start any server; read any file other than `settings.json` in the Gemini home; read credential files (`oauth_creds.json`, `auth.json`, `*.key`).
- Error handling: `OSError`, `ValueError`, non-dict `mcpServers` produce an empty list and warning `gemini_connectors_unreadable`, matching `control/integrations.py:23-27`.

**`connectors.digest(spec: dict, local_key: bytes) -> str`**
- Guarantees: deterministic (canonical JSON with sorted keys); changes if any listed field changes; output is hex HMAC-SHA256.
- Must-not: store or return the spec values; accept a missing key (raises `gemini_connector_key_missing`).

**`connectors.approve(project_id, name, digest_shown, owner) -> ApprovalRecord`**
- Precondition: owner route only (same gate as other owner writes).
- Guarantees: idempotent for the same digest; refuses if the current digest differs from `digest_shown` (`gemini_connector_changed`); atomic write (temp file, rename, mode 0600) through the existing private-file helper in `adapters/shared/private_files.py` (function name to confirm in P2).
- Must-not: write to any Gemini file.

**`connectors.revoke(project_id, name, owner) -> None`**
- Guarantees: removes the record; the next `allowed()` call excludes it.

**`connectors.allowed(project_id, home) -> tuple[str, ...]`**
- Guarantees: returns only names that are present now, have a record for this project, whose current digest equals the recorded digest, and whose `trust` is not `true`. Sorted.
- Must-not: cache across calls that span a revoke; the ACP path calls it per request.

**`policy.prepare(config, home, permissions, access_mode, allowed_connectors=())`** (`policy.py:25`, changed)
- Guarantees: with an empty `allowed_connectors`, output is byte-identical to today (placeholder, no `mcpName` rules). With names, `--allowed-mcp-server-names` lists exactly those names; one `mcpName` rule per name.
- Must-not: include any name that is not in `allowed_connectors`.
- Error handling: a name that is not a valid server identifier raises `gemini_connector_name_invalid` (ToolError, as the other `gemini_*` errors in `policy.py`).

**`AcpConnection._handle_request` kind `other`** (`native.py:182-198`, changed)
- Guarantees: rejects when the server name is unknown, not in `connectors.allowed()` at this moment, or the mode is not allowed. Calls the owner callback only in `ask` and `auto` for an allowed name.
- Must-not: approve from the request title alone (`native.py:193` currently builds the kind from the title; the server identity must come from structured fields, verified in P0).

**Approval handler, Gemini branch** (`conversation_service.py:2774-2779`, changed minimally)
- Guarantees: `rule_key` stays `None` for Gemini connector calls (no persistent per-call rule). The schedule check (`:2775-2776`) still runs first.

### Failure modes (what a future implementer will try, and why it breaks)

1. **Building the argv from `config` server names.** Passing every `mcpServers` key to `--allowed-mcp-server-names` would start unapproved servers. Only `connectors.allowed()` output may reach `prepare`.
2. **Dropping the placeholder when the list is empty.** An empty `--allowed-mcp-server-names` may be read by the CLI as "allow all". Keep the placeholder (`policy.py:89-90`).
3. **Enabling a connector by writing `trust: true`.** The CLI then skips its confirmations, and the ACP permission request may never come. KeepHarness must never write that field, and must refuse to enable a spec that already has it (`refused (trust: true)`).
4. **Approving by name only.** A changed command under the same name runs unreviewed. The digest must bind the approval (D2).
5. **Digest over a stale read.** The CLI reads its settings file itself, so there is a window between KeepHarness's digest check and the CLI's read. Mitigation: check the digest at run start and on each permission request; residual risk is recorded in the risk register.
6. **Server identity from the title.** `native.py:193` builds the kind from the ACP title. Using the title to match a connector name lets a crafted title pass. Identity must come from structured fields, else fail closed.
7. **Caching the allowed set for the run.** A revoke would then be ignored until the run ends. Re-check on each permission request.
8. **Leaking secrets through the view.** Returning `env` or `headers` values in the Plugins/Connectors list or the logs. Only the digest prefix and the public keys leave `connectors.inventory`.
9. **Project-scope servers.** The CLI may also load `.gemini/settings.json` from a project. Those servers are not in the allow-list, so the placeholder blocks them; a future change that adds project scope must add its own trust gate (Open question 2).
10. **Treating read-only as "connectors on".** D-039 item 6 says read-only no longer disables connectors, but `integrations_view.py:68-69` still does for Gemini. Changing this silently would be a policy change; it needs the owner's decision (Open question 4).
11. **Scheduled runs with approved connectors.** A schedule has no owner present. `conversation_service.py:2775-2776` denies it; a refactor that moves the schedule check below the allow-list check would let an approved connector run unattended.

### Integration points

**Dependencies (upstream of the change)**
- `adapters/gemini/policy.py:25` prepare; `:52-60` rule builder; `:78` removes `GEMINI_CLI_HOME` so the CLI uses the owner's real Gemini home (the source of the settings file).
- `adapters/gemini/native.py:182-198` ACP permission mapping; `:256` the `prepare` call inside `run_acp` (`:243`); `:298, :317` `mcpServers: []`.
- `control/integrations.py:21-28` user-scope reader; `:32-46` inventory.
- `agent_service/integrations_view.py:33-42` constants; `:68-69` Gemini read-only branch; `:83-85` `item_reason`; `:104-117` `read_inventory` with `PUBLIC_KEYS` and warnings; `:172-180` `bare_name`.
- `agent_service/approval_policy.py:6` MODES; `:14-27` `rule_key` (commandExecution only); `:42-57` `full_approval_allowed` (returns False for non-Claude kinds).
- `agent_service/services/conversation_service.py:2769-2830` approval handler (schedule check `:2775-2776`, rule reuse, full-mode branch around `:2802-2818`).

**Dependents (downstream of the change)**
- `agent_service/routes/projects.py:58` the `integrations` route that serves the Plugins/Connectors list.
- `control/manager.py:140` `.integrations()` (indexed by Graphify as the integrations entry point).
- `control/provider_state.py:613-631` `_security_metadata` (`trust` and `mcp_approvals` shape that Gemini rows should follow).
- `dossier/releases/v0.16.0.md` (release note this change will supersede for Gemini).

**Second ring (one more hop)**
- `agent_service/services/conversation_service.py` run-start path: the policy must be prepared from the same `allowed` snapshot as the ACP check (the run's `permissions` and `access_mode`).
- Scheduled-run code (`docs/scheduled-tasks.md`, which states Gemini schedules run without connectors) must stay true.
- The Plugins/Connectors UI component: the owner approval control must follow the existing trust control pattern (`tests/project-trust-ui.spec.cjs`).

**Configuration touchpoints**
- Gemini user settings: `~/.gemini/settings.json` (read). Documented scope and keys: https://geminicli.com/docs/tools/mcp-server/.
- Owner-state directory: new file `gemini-connectors.json` plus a local key file, mode 0600 (both under the control state directory; exact path to confirm in P2 against `control/persistence.py`).
- Environment: `GEMINI_CLI_SYSTEM_SETTINGS_PATH` (`policy.py:33`, test override) and the existing removals at `policy.py:70-79`.

### Test protocol

Rules: no network, no real accounts, no real `oauth_creds.json` or `auth.json`. Every Gemini test uses a fake executable in `tmp_path` and a throwaway HOME, the same pattern as `tests/test_gemini_contracts.py:93-150` (real-process fake) and `tests/test_gemini.py:46-60` (ACP relay with a fake executable), and `fake_acp` (`tests/test_gemini_contracts.py:223-225`).

- **Fake CLI fixture (new).** A Python script in `tmp_path` that: reads `$HOME/.gemini/settings.json` from the throwaway HOME; writes its argv as a JSON line to a log; when a `session/request_permission` case is requested by the test, emits a permission request whose `kind` is `other` and whose structured fields carry the server name (the shape to be confirmed in P0); answers `initialize`, `session/new`, `session/prompt` as the existing fakes do.
- **Unit tests** (`tests/test_gemini_connectors.py`, new): digest determinism and sensitivity (each listed field changes the digest; key rotation invalidates); `allowed()` (absent, changed, removed, `trust: true`, malformed file); `approve()` refuses a changed digest; `revoke()` takes effect on the next call; atomic write leaves the old file on failure; the owner-state file contains no `env` or `headers` values (assert with a secret-looking sentinel).
- **Policy tests** (`tests/test_gemini_contracts.py`, extended): empty `allowed_connectors` gives byte-identical argv to today (regression); one name gives exactly that name in `--allowed-mcp-server-names` and one `mcpName` rule; an invalid name raises `gemini_connector_name_invalid`; the placeholder stays when the list is empty.
- **ACP permission tests** (`tests/test_gemini_contracts.py`, replacing `test_connector_calls_are_denied_without_asking` at line 79): approved and `ask` mode calls the owner callback once per call; approved and `auto` also asks; unapproved is rejected without calling the callback; unknown server identity is rejected; revoke during a run is rejected at the next request; `read_only` rejects; scheduled run (`schedule_id`) is denied unattended.
- **Process-level test.** The fake CLI records argv; assert `--allowed-mcp-server-names` contains only approved names during a real run of the fake.
- **UI test.** `tests/*.spec.cjs` for the Plugins/Connectors approval (states: not approved, approved, changed, refused, revoke), modeled on `tests/project-trust-ui.spec.cjs`. Per Sophia's rule, the visual operator pass is visible on her screen, not batched.
- **Spike test (P0, manual, not in CI).** Run the installed Gemini CLI with a throwaway HOME and a placeholder server (a local `python -c` MCP stub that prints nothing sensitive), with no login. Record: argv parsing of `--allowed-mcp-server-names`; whether `mcpName` ask_user rules take effect; whether permission requests carry the server name; whether `trust: true` suppresses permission requests. Store the transcript under `dossier/research/` only if it is needed later.

Validation command (for implementers): `.venv/bin/python -m pytest -q tests/test_gemini.py tests/test_gemini_contracts.py tests/test_gemini_connectors.py` (the repo's pytest convention, AGENTS.md).

## 4. Package split (P0 to P6)

**P0. Spike: verify the CLI behavior the design depends on.**
- Scope: throwaway-HOME runs of the installed Gemini CLI (no login, no owner data). Answers the four questions in the test protocol and the CLI version. Output: a short note in `dossier/research/`.
- Done when: a written note states, for the installed version, (1) `--allowed-mcp-server-names` filters servers, (2) `mcpName` ask_user takes effect alongside `toolName = "*"` deny, (3) the ACP permission request has a structured server field, (4) `trust: true` suppresses the permission request, and each answer cites the CLI output or docs line.

**P1. Read-only connector inventory with digests.**
- Scope: `connectors.inventory` and `connectors.digest` (needs the local key file, created on first use); `integrations_view` Gemini entries with status and warnings; no approval, no run behavior change.
- Done when: the Plugins/Connectors list shows Gemini connectors as `not approved` with a digest prefix; a test asserts no `env` or `headers` value appears in the API JSON; a cross-provider name warning test passes; no Gemini file is written (byte-compare the settings file before and after).

**P2. Owner approval store and owner route.**
- Scope: `connectors.approve`, `connectors.revoke`, `connectors.allowed`, owner-state file with atomic writes via the private-file helper; owner route wired like the existing owner writes; pruning on removal.
- Done when: unit tests for approve, revoke, changed digest, removal, malformed file and atomic failure pass; the route refuses without the owner gate (test with the existing owner-gate fixture); the Gemini settings file is unchanged after approve and revoke.

**P3. Pass allowed names to the CLI.**
- Scope: `policy.prepare(..., allowed_connectors=())` (`policy.py:25`); `--allowed-mcp-server-names` and `mcpName` rules; `trust: true` refusal; the run-start snapshot.
- Done when: empty input gives byte-identical argv to today (regression test); approved names give exact argv entries; the process-level fake CLI test shows only approved names started; P0 answers (1) and (2) are cited in the test comments.

**P4. ACP permission gate for connectors.**
- Scope: `native.py:182-198` `other` branch; per-request `connectors.allowed()`; server identity from structured fields only; `read_only` and scheduled-run behavior preserved; owner callback reused (`conversation_service.py:2774-2779`).
- Done when: the ACP permission test set in the test protocol passes; a revoke during a fake run is rejected at the next request; an unknown identity is rejected without calling the callback; `rule_key` remains `None` for Gemini (test asserts no persisted rule row).

**P5. Plugins/Connectors approval UI and tests.**
- Scope: approve, revoke and changed-state controls in the existing Plugins/Connectors screen; warning copy in English; Playwright spec for the five states; visible operator pass.
- Done when: the UI spec passes in light and dark themes with the contrast check used by `tests/project-trust-ui.spec.cjs`; the approval control keeps focus after Enter; the operator pass is visible on Sophia's screen, not batched.

**P6. Docs, decision record and release note.**
- Scope: `dossier/releases/v0.<next>.md` (behavior, acceptance criteria, migration: none, validation actually run); a proposed decision record for the owner-state deviation from D-039 point 4 (id to be assigned by `product-record-curator`); README and `README.pt-BR.md` in sync if the Gemini connector claim changes (AGENTS.md convention); update the #46 release note cross-reference.
- Done when: the release note lists acceptance criteria that each map to a test name; the decision record exists as `proposed` and is only filed after Sophia accepts it; both README files change in the same commit with the same headings.

Golden path: P0, P1, P2, P3, P4, P5, P6 in that order. P1 and P2 can be reviewed independently of the CLI behavior; P3 and P4 must not start until P0 is written up.

## Open questions and recommended defaults

1. **Trust model.** Default: A (default-deny per connector, digest-bound approval, HMAC-keyed KeepHarness owner-state file, nothing written to Gemini settings). Note: the local JEV consultation abstained (confidence 0.48 under a 0.90 threshold for a high-risk decision), so this default is a local recommendation, not an external verdict. Record it as a proposed decision, since it deviates from D-039 point 4.
2. **Scope of connectors.** Default: user scope only. Project-scope `.gemini/settings.json` is shown as a count with a note and is not enabled. A later decision is needed, because a project file comes from the repository, not from the owner.
3. **Per-call approval.** Default: every call asks in `ask` and `auto`, as for Codex and Claude. `full` auto-approves only when the Gemini backend is `unrestricted`, the same rule as the full-mode branch in `conversation_service.py:2802-2818`.
4. **Read-only mode.** Default: connectors stay off in `read_only` (current behavior at `integrations_view.py:68-69`). This conflicts with D-039 point 6 ("Read only no longer disables connectors"). It needs the owner's decision; the default keeps the fail-closed state until she decides.
5. **`trust: true` in the owner's settings.** Default: never enable such a connector; show `refused (trust: true)` and tell the owner to change her Gemini settings if she wants it through KeepHarness. KeepHarness never writes the flag.
6. **`includeTools` and `excludeTools`.** Default: included in the digest; the CLI applies them; KeepHarness does not reinterpret them and does not add a per-tool allow-list in v1.
7. **Revocation timing.** Default: effective at the next ACP permission request; the run continues without that connector and no connector call is approved after the revoke.
8. **Digest key.** Default: HMAC-SHA256 with a random 32-byte local key, mode 0600, so the stored digest cannot be brute-forced for low-entropy secrets.
9. **Server identity.** Default: taken only from structured ACP fields; if absent, reject (fail closed). P0 decides whether the fallback is needed at all.
10. **CLI replacement and version.** Default: pin the CLI version validated in P0 (`policy.py:3` is validated against Gemini CLI 0.60.0; the installed CLI was not on PATH during this design). When the CLI version differs, connectors show `unverified CLI` and stay off until P0 is re-run. Also check the Antigravity CLI replacement notice before P3.
11. **Scheduled runs.** Default: no connectors, unchanged (`conversation_service.py:2775-2776`; release notes line 50).
12. **Cross-provider names.** Default: warn on the same name with a different transport or digest; never merge or auto-approve across providers.
13. **Decision record.** Default: file a proposed decision (id from `product-record-curator`) that states the owner-state deviation from D-039 point 4 and the reason (no documented per-project Gemini approval store; `trust: true` bypasses confirmations). File only after Sophia accepts it.

## Risk assessment (software-risk-playbook)

**Complexity score.** File-count bucket: 6 or more files (+3). New behavior (+1). Crosses module boundaries (`adapters`, `control`, `agent_service`) (+1). Concurrency-sensitive: revocation during a live run (+1). Covered by invariant documents (D-039, #44 trust doc, #46 release notes) (+1). No data migration (+0). Total 7, so the complexity-5 floor applies: full assessment, at least 10 scenarios. The override also applies: the change touches an approval (security) surface.

**Impact map.**
- d=1: `policy.prepare`, `AcpConnection._handle_request`, `connectors.inventory`, `integrations_view.route_limits` and `read_inventory`, `approval_policy.rule_key`.
- d=2: `run_acp` (`native.py:243`), the run-start path in `conversation_service.py`, `routes/projects.py:58`, the Plugins/Connectors UI.
- d=3 (surprising): the same gate is read by Claude's `project_security` (`claude/native.py:72-93`) through `ProviderStateService`; changing the owner-state shape affects the trust snapshot used for all three providers; the schedule check ordering in the approval handler.

**Feasibility checks against live code.**
- The placeholder and its argv exist (`policy.py:89-90`). Confirmed.
- `other` is mapped to `False` and approval is limited to `execute` (`native.py:182, 184-196`). Confirmed.
- `rule_key` covers only command execution, so Gemini connectors have no persistent rule today (`approval_policy.py:14-27`). Confirmed; the design keeps it so.
- ACP sends an empty `mcpServers` (`native.py:298, 317`), so the CLI, not KeepHarness, starts MCP servers. Confirmed; this is why the allow-list is the first fence.
- Whether the CLI's permission request carries structured server identity, and how `mcpName` rules interact with `toolName = "*"`: not confirmed. These are P0 items. The design does not depend on them until P0 answers.
- The `trust` flag semantics and the per-project absence of an approval store are based on the Gemini CLI MCP docs page, not on an installed CLI. Marked as P0 items.

## Scenario band

| # | Category | Trigger | Predicted failure | Mitigation (where) | Verdict |
|---|---|---|---|---|---|
| 1 | happy_path | Owner approves a connector, runs in `ask` mode | Connector call works after a per-call approval | `connectors.allowed` plus `native.py` `other` branch | Holds (test P4) |
| 2 | edge_case | Connector command edited between display and approval | Approval binds an unreviewed command | `approve()` refuses on digest mismatch (`gemini_connector_changed`) | Holds (P2 test) |
| 3 | concurrency | Revoke while a run is in progress | Stale allowed set keeps calling the server | Per-request `allowed()` check in the ACP branch | Holds (P4 test); residual window noted |
| 4 | invariant_violation | Someone passes all `mcpServers` names to `--allowed-mcp-server-names` | Unapproved servers start | Only `allowed_connectors` reaches `prepare` (`policy.py:25`); placeholder when empty | Holds (P3 regression test) |
| 5 | invariant_violation | Owner settings contain `trust: true` | CLI skips confirmations, no permission request | Refused definition, never enabled; never written by KeepHarness | Holds if P0 confirms the behavior; otherwise refuse |
| 6 | error_path | ACP request title spoofs a connector name | Connector approved by a crafted title | Structured identity only; else reject (`native.py:193` not used for identity) | Holds (P4 test) |
| 7 | boundary | Empty approved list | Empty `--allowed-mcp-server-names` read as "allow all" | Keep placeholder (`policy.py:89-90`) | Holds (P3 test) |
| 8 | integration | Schedule run with an approved connector | Unattended connector call | Schedule check first (`conversation_service.py:2775-2776`) | Holds (P4 test) |
| 9 | integration | Same name in Codex and Gemini with different definitions | Confusion or auto-approval across providers | Warning only, no merge (`integrations_view.py:172-180`) | Holds (P1 test) |
| 10 | error_path | Malformed or unreadable `settings.json` | Crash, or connectors enabled from partial data | `connectors.inventory` returns empty plus warning (`control/integrations.py:23-27` pattern) | Holds (P1 test) |
| 11 | edge_case | CLI replaced by Antigravity CLI for the owner | Behavior unknown, gates bypassed | Version pin and "unverified CLI" state; kill criterion (b) | Open; needs P0 and owner check |
| 12 | edge_case | Owner's settings and project settings both define the same server name | CLI picks one; approval applies to the wrong definition | Digest of user-scope spec only; project scope not allowed (Open question 2) | Holds for v1 by scope |

**Predicted failures registry (summary).**
- F1 (high): approval by name only; mitigation `connectors.digest` in `approve()`.
- F2 (high): argv built from all configured names; mitigation `policy.prepare` input contract.
- F3 (high): `trust: true` silently removes confirmations; mitigation refusal plus P0 check.
- F4 (medium): TOCTOU between KeepHarness digest check and CLI read; mitigation check per request, residual risk accepted and recorded.
- F5 (medium): title-based identity; mitigation structured identity, fail closed.
- F6 (medium): read-only policy conflict with D-039; mitigation owner decision (Open question 4).
- F7 (low): secret leakage via digest or view; mitigation digest-only and `PUBLIC_KEYS`.

## Test plan summary

Covered in section 3 (Test protocol). The regression anchors are `test_gemini_contracts.py` (placeholder argv, connector denial replaced by the new contract), `test_gemini.py` (ACP relay) and the existing Gemini schedule behavior.

## See also

- `dossier/decisions/d-038-keepharness-facade-over-provider-state.md`, `dossier/decisions/d-039-single-owner-facade-policies.md`, `dossier/provider-facade-design.md`
- `dossier/trust-mcp-approval-validation.md` (#44)
- `dossier/releases/v0.16.0.md` (#46 fail-closed, lines 28, 49-50, 2299-2301)
- `dossier/naming-model.md` (new error codes and file names to check in P2)
- Gemini CLI MCP servers: https://geminicli.com/docs/tools/mcp-server/ ; policy engine: https://geminicli.com/docs/reference/policy-engine/
