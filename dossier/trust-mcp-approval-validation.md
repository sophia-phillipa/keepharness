# Trust and project MCP approval validation (#44)

Scope: W11 issue #44, based on `08c9381`, branch
`feat/w11-44-trust-mcp-approval`. Issue #45 (real-home execution and removal
of legacy orchestration filters) and #52 (scoped retirement) remain separate.
Reviewed implementation commits: `1e71ca6` (backend) and `c46a9f8` (UI).

## Contracts and implementation decisions

- Trust in either CLI applies to both providers. An explicit owner action writes
  the exact project's trust into both CLI stores; no KeepHarness trust database
  is introduced.
- Conversation and Plugins surfaces expose trust and project MCP approval.
  Harness routes use the existing owner and write-origin checks; control routes
  retain the loopback-only guard.
- Untrusted Claude runs use `--setting-sources user`, `disableAllHooks`, and
  disabled project MCP names. MCP approval is evaluated separately from trust;
  a rejection in any settings layer wins over an approval.
- The #44 runtime change applies the trust restrictions at the final Claude
  command. While #45 is pending, approved project MCP definitions are included
  in the existing explicit MCP configuration so the legacy strict flag does
  not prevent approved project servers from running.
- The harness serves provider-state actions without writing the control-owned
  seen map. Sanitized own-write receipts bridge successful actions to the
  control service, which suppresses only the matching transitions. This extends
  D-041's single-writer design without an authenticated control-proxy dependency.
  JEV selected receipts over a control proxy at confidence 0.92; the security
  reviewer must verify receipt consumption and later external changes.

## Evidence boundaries

All state is synthetic and uses temporary homes. Marker subprocesses exercise
the generated command contract without cloud inference; they are not a live
Claude account certification. Browser scenarios use fixture API responses.
Seven profiles represent simulated workflows, not research with real users.

Tests use `/home/sophia/.cache/keepharness-gauntlet/venv-lock/bin/python` and
a single task temporary root, with hard timeouts. The full Python suite runs
once at the end, as explicitly requested. The known missing-pip failures are
`test_requirements_can_be_added_to_existing_venv` and
`test_second_identity_build_and_install_side_by_side`.

## Acceptance mapping and results

The named Python tests below are in `tests/test_provider_trust.py`.

| Issue #44 acceptance bullet | Named evidence |
| --- | --- |
| Trusted by either CLI; explicit prompt warns about both; native writes through owner routes | `test_trust_accepts_both_cli_states_exact_project`, `test_either_cli_trust_and_denial_wins`, `test_both_adapters_union_trust_and_codex_only_approvals`, `test_trust_control_gate_refuses_foreign_requests`, `test_harness_security_routes_use_owner_gate`, `test_harness_routes_accept_local_session_and_verified_remote_owner`; `tests/project-trust-ui.spec.cjs` |
| Until accepted: user-only Claude settings, hooks disabled, no project MCP | `test_native_run_executes_hook_env_and_mcp_only_after_acceptance` executes the final native command in three phases and observes marker files |
| Unapproved `.mcp.json` names disabled; hook, env and MCP markers | `test_native_run_executes_hook_env_and_mcp_only_after_acceptance`, `test_either_cli_trust_and_denial_wins`, `test_malformed_approval_settings_fail_closed` |
| Fake homes only, no real owner state | Autouse `isolated_provider_homes` fixture, `tests/test_provider_state_home_isolation.py::test_home_and_provider_dirs_point_at_a_fake_home`; fake homes supplied to browser fixture servers |

Additional F12 evidence:
`test_remote_service_receipt_preserves_control_single_writer_and_external_revert`
and `test_partial_trust_write_keeps_receipt_and_reports_failure`, plus
`test_successful_write_then_read_failure_invalidates_cache` and
`test_external_user_edit_during_trust_write_still_notifies`.
`test_project_mcp_cannot_replace_harness_effects` preserves the harness's own
server when a project declares the same MCP name.

## Simulated profile matrix

Browser evidence: `tests/project-trust-ui.spec.cjs`. All seven profiles passed
again after the UI review corrections.

| Profile | Main path | Error or recovery variation |
| --- | --- | --- |
| P1 beginner | Read the shared trust warning and accept | A validation error explains retry and leaves acceptance available |
| P2 rushed user | Double click trust; only one request | Retry after a conflict while preserving a nonempty conversation draft |
| P3 domain professional | Identify a named MCP server and its approval | Reload and confirm the same server's stored approval |
| P4 accessibility | Accept trust and approve MCP with Enter | Focus stays on the changed approval control |
| P5 mobile | Use Plugins at 390 px without horizontal overflow | Reload and restore project selection and server state |
| P6 engineer | Check owner-route payloads, selected project and no synthetic conversation turn | Conflict leaves the action retryable; effective denial remains visible after an approval request |
| P7 UI/UX | Read the complete trust implications | Warning, muted, button and error text in light/dark themes have contrast of at least 4.5 |

The browser campaign also passed `admin-plugins-switches.spec.cjs`,
`admin-provider-notices.spec.cjs` and `composer-plugins.spec.cjs`.

## Review and final gates

Authors and reviewers are different agents: Astra/high backend and Sol/medium
UI, then cross-review with Ponytail Review. The backend review found an extra
argument in the existing switch path and a successful-write/failed-confirmation
cache edge. The UI review found selected-project writes using `sem-projeto`,
loss of trust metadata on snapshot refresh, an approval message reflecting the
requested rather than effective state, and stale Claude approval state after a
Codex trust response. These are corrected and focused cross-review passed.
Successful Codex trust followed by an unavailable confirmation read records
only layer versions, sanitized prior items and a receipt identifier. Recovery
suppresses project-scope transitions only if that layer version is unchanged;
later external changes still notify. The JEV comparison for recovery returned
no decision; this fallback was checked against the actual app-server output.

Both CLI writes are required by this task. A missing CLI is an actionable
failure, not permission to silently skip its trust write. A partial successful
write is preserved; subsequent writes never roll back another process's bytes.

Targeted backend validation: 272 passed across `test_provider_trust.py`,
`test_provider_state_claude.py`, `test_provider_state_codex.py`,
`test_provider_state_notices.py`, `test_admin_provider_state.py`,
`test_provider_state_run_start.py` and `test_native.py`. A final MCP name-collision
regression failed before its insertion-order fix; the affected trust/native set
then passed all 32 tests. The new trust file contains 19 collected cases.

The final four-spec browser campaign passed. `git diff --check` and
`scripts/check_conventions.py` passed with zero errors, warnings, Portuguese
hits or guest hits.

Full Python suite, run once: **4,317 passed, 27 skipped, 6 failed**, with 14
passing subtests in 266.46 seconds. Two failures are the known missing-pip
cases. Four failures exposed test integration assumptions in
`test_adapter_conformance.py`, `test_adapter_idle_watchdog.py`, and
`test_scoped_home_security.py`: secondary Codex trust probing shared mocked
process transport or initialized a fake sentinel home, and an old opt-in case
had no explicit project trust. The trusted command also expanded the legacy
setting sources prematurely; it now retains `project` or `user,project`, leaving
the #45 migration separate. The fixtures declare exact-project trust and mock
only the secondary Codex reader, keeping their original assertions intact.

After correction, **56 passed** in 9.72 seconds across
`test_adapter_conformance.py`, `test_adapter_idle_watchdog.py`,
`test_scoped_home_security.py`, `test_provider_trust.py` and `test_native.py`.
All four additional full-suite failures passed in this focused run. The only
unresolved failures are the two known missing-pip cases. The full suite was not
repeated, honoring the requested single run; this is combined full-run and
focused-retest evidence, not a claim of a second green full run.

Commands (the browser run also received fake `HOME`, `CODEX_HOME` and
`CLAUDE_CONFIG_DIR`; `TMPDIR` pointed at the same task root throughout):

```sh
PY=/home/sophia/.cache/keepharness-gauntlet/venv-lock/bin/python
timeout 15m "$PY" -m pytest -q tests/test_provider_trust.py tests/test_provider_state_claude.py tests/test_provider_state_codex.py tests/test_provider_state_notices.py tests/test_admin_provider_state.py tests/test_provider_state_run_start.py tests/test_native.py
timeout 15m "$PY" -m pytest -q tests/test_provider_trust.py tests/test_native.py
NODE_PATH=/home/sophia/kbd-research/node_modules PYTHON="$PY" timeout 15m env -u DISPLAY -u WAYLAND_DISPLAY ./scripts/test-ui.sh tests/project-trust-ui.spec.cjs tests/admin-plugins-switches.spec.cjs tests/admin-provider-notices.spec.cjs tests/composer-plugins.spec.cjs
timeout 30m "$PY" -m pytest -q -p no:cacheprovider --basetemp="$TMPDIR/full-pytest"
timeout 15m "$PY" -m pytest -q tests/test_adapter_conformance.py tests/test_adapter_idle_watchdog.py tests/test_scoped_home_security.py tests/test_provider_trust.py tests/test_native.py
timeout 2m "$PY" scripts/check_conventions.py
git diff --check
```

The Maestro pilot is already closed; no window was restarted. Total agent
tokens and cost are unavailable, so no efficiency claim is made.

## Independent security review correction round

The independent review of `a7b6c8e` superseded the initial cross-review verdict:
four major findings and one minor finding were reproduced despite the existing
green targeted suites. Its full-suite baseline was 4,321 passed, 27 skipped and
the two known missing-pip failures. This correction round uses targeted tests
and the four browser specs; it does not repeat that full suite.

| Finding | Named regression and evidence |
| --- | --- |
| R1: Unicode key escaped into a different Codex project | `test_unicode_trust_uses_exact_native_key`, `test_real_codex_parser_preserves_exact_project_paths`: real installed app-server parser, entirely fake homes, exact trust keys for Unicode, quotes, backslashes, spaces, newline and tab |
| R2: stale conversation warning authorizes a different project | `project-trust-ui.spec.cjs`: existing-conversation navigation, delayed responses, A-to-B-to-A and provider changes; `test_security_write_requires_rendered_project_context` on both APIs, both actions and missing/wrong/rebound/current/alias roots; `test_service_rechecks_root_after_waiting_for_writer_lock` |
| R3: approved but owner-disabled MCP can execute | `test_approved_disabled_project_mcp_never_executes`: synthetic MCP marker through `native.run`, covering native opt-out and user/project/local/managed approval denials; approval alone cannot override the owner's disabled state |
| R4: external project edits hidden as own writes | `test_external_project_edit_during_trust_is_never_an_own_transition`, `test_project_change_between_snapshot_and_layer_capture_conflicts`: changed/added project items remain external, and mismatched pre-write reads conflict |
| R5: retry loses an unconsumed own receipt | `test_unconsumed_receipt_survives_partial_trust_retry`, `test_consumed_receipt_is_not_revived_by_a_later_trust`: preserve the first successful trust through a retry without reviving consumed transitions |

Python regression files: `tests/test_provider_trust_review.py` and
`tests/test_provider_security_context.py`. The HTTP contract now requires
`expected_project_root`, captured from the displayed snapshot together with
provider and project id. A canonical alias still round-trips; a changed mapping
is rejected under the writer locks before a CLI action.

Native parser evidence: installed Codex **0.157.1**, no model turn. Loading the
`a7b6c8e` adapter into an isolated temporary subprocess failed exact-key checks
with "Native parser wrote unintended trust keys". The corrected adapter passed
`test_real_codex_parser_preserves_exact_project_paths` for accented text, emoji,
quotes, backslashes and spaces, with no unintended sibling keys. An additional
real-parser probe reproduced the same key redirection for newline and tab;
native quoting now escapes only backslash and quote, and preserves those
literal characters. The final seven-case native probe passes. The first
backend regressions also failed for disabled MCP execution, hidden external
project edits and the lost retry receipt before their fixes.

All probes use fake homes and no cloud inference. The JEV gateway required
approval unavailable under this session's policy, so correction routing used
the local security-risk criteria.

**Final validation.** 331 targeted Python tests passed in 90.38 seconds across
the two new files and `test_provider_trust.py`, `test_provider_state_claude.py`,
`test_provider_state_codex.py`, `test_provider_state_notices.py`,
`test_admin_provider_state.py`, `test_provider_state_run_start.py`,
`test_native.py`, `test_scoped_home_security.py`, `test_adapter_conformance.py`
and `test_adapter_idle_watchdog.py`. After the final native-quoting correction,
all 89 Codex/review tests passed in 38.75 seconds. This includes 14 backend review
cases; all 20 API context cases passed, including immediate read/retry after
root rebinding and a lock-wait rebinding regression. No full suite was rerun in
this correction round.

The final combined browser campaign passed all four requested specs:
`project-trust-ui.spec.cjs`, `admin-plugins-switches.spec.cjs`,
`admin-provider-notices.spec.cjs`, `composer-plugins.spec.cjs`. The trust spec
also verifies focus after conflict refresh and rejects callbacks from previous
provider/navigation generations. One earlier campaign hit an existing notices
focus race: the test observes the intermediate redraw before the asynchronous
refresh restores focus. Neither that code nor its assertions changed; the final
four-spec campaign passed. This intermittent test limitation remains disclosed.

Backend and UI/route authors used Astra/high and reviewed each other's changed
artifacts. Review corrections covered consumed-receipt revival, external
inventory attribution, immediate cache recovery after root rebinding, five
MCP denial layers and focus after a conflict. The final native-quoting delta was
reviewed separately; no open finding remains. Ruff, JavaScript syntax,
`git diff --check` and conventions passed with zero convention errors/warnings.

The shared Git directory was read-only in this session. The implementation
remains on top of `a7b6c8e`; three local commit-message files under
`.codex-commits/` identify backend, UI/routes and documentation units for the
authorized commit fallback. Temporary probe and test state was removed after
recording this evidence.

## Visible-pass correction round (2026-10-08)

Baseline `4c5373e`; implementation `63c643a`. The two UI findings were reproduced
separately before product edits: the disabled row said Approved and had Revoke;
the project had no Revoke trust button. New backend regressions initially had
seven failures (revocation, owner-disabled metadata and five invalid boolean
inputs). The final focused gate passed 485 Python tests and all five requested
browser specs. No full suite, inference, push or merge was performed.

| Finding | Evidence |
| --- | --- |
| Project trust revocation | `test_revoke_trust_writes_both_cli_states_and_suppresses_own_removals`; `test_partial_revoke_retry_preserves_receipt_and_truthful_union`; `test_revoke_stale_binding_never_changes_either_cli`; keyboard/focus checks in `project-trust-ui.spec.cjs` |
| Owner-disabled MCP label | `test_owner_disabled_mcp_metadata_is_distinct_from_approval`; both UI panels assert Disabled by owner and no buttons; existing `test_approved_disabled_project_mcp_never_executes` still passes |
| Codex project plugin read-only | Native codex-cli 0.157.1, fake HOME: `config/read` found the project layer; project `config/batchWrite` returned `configLayerReadonly`, with unchanged bytes. `test_a_trusted_project_layer_overrides_the_user_layer` passed. This is the native limitation already recorded in the facade design, not a fixture defect. |

The seven-profile matrix above passed again, with revocation added to P1's
visible actions, P2's draft/context preservation, P4's keyboard/focus recovery,
P5's narrow-screen checks, P6's exact payloads and P7's contrast checks. P3's
named-server/reload scenario now includes the owner-disabled distinction.
Trusted projects without MCP servers retain their revoke control in both UIs.
These are simulated profiles, not user studies or a real screen-reader test.

Both CLI trust values are written through the existing guarded paths. Executed
markers now also verify that revocation blocks hooks, environment effects and
project MCP. Receipt regressions cover removed project rows, unchanged user
fallbacks, partial-write retries and concurrent edits that must remain external
notices. A native inherited-trust probe confirmed that explicit child untrusted
disables the child's layer and preserves the parent's trust and layer.

Independent artifact review, including Ponytail Review, found no open issue.
Conventions: zero errors or warnings. Chrome plugin tools were unavailable;
Playwright supplied browser validation on fake fixture APIs. The final JEV
coverage query abstained (0.49); acceptance rests on the inspected artifacts and
executed checks. Task-owned temporary directories were removed; evidence logs
and exact command scripts are retained under
`/home/sophia/.cache/codex-runs/keepharness/w44-fix/root-evidence/` and
`/home/sophia/.cache/codex-runs/keepharness/w44-fix/trust-mcp/`.
