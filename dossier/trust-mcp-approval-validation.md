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
