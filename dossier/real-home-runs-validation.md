# Real-home native runs validation (#45)

All tests use temporary fake `HOME`, `CODEX_HOME` and `CLAUDE_CONFIG_DIR`.
No owner CLI state or credentials are fixtures. No cloud inference is performed.

## Named acceptance evidence

| Requirement | Evidence |
| --- | --- |
| Native homes and all four presets, attended and scheduled | `tests/test_real_home_runs.py::test_spawned_native_cli_presets_use_owner_home` (16 spawned cases) and `test_native_presets_keep_real_home_and_orchestration` (16 builder cases) assert inherited environment, native modes and absent legacy filters. |
| Absent home overrides stay absent | `tests/test_real_home_runs.py::test_unset_native_home_overrides_stay_unset` (both providers); `tests/test_env.py::test_runtime_config_carries_no_provider_homes_or_allow_list`. |
| No personal instruction injection or reads for Codex/Claude | `tests/test_scoped_home_security.py::test_native_home_and_sessions_ignore_retired_opt_in` and the preset matrix. |
| Native resources without the retired opt-in | `tests/test_resources.py::test_native_resources_use_cli_home_for_every_preset_and_schedule` (32 cases) and `test_scheduled_run_resolves_native_owner_prompts`. |
| Scheduled execution retains owner orchestration and network policy | `tests/test_schedule_internet.py::test_codex_scheduled_runs_keep_owner_network_grant` and `test_claude_scheduled_runs_keep_owner_web_tools` exercise `ConversationService.infer`; the Gemini control retains its existing behavior. |
| Codex hooks pending review are visible and never bypassed | `tests/test_real_home_runs.py::test_codex_pending_hooks_are_visible_without_bypassing_review`, `test_codex_hook_warning_before_rpc_reply_is_visible_once` (initialize and thread-start), and the spawned matrix's forbidden-bypass assertion. |
| Unsupported versions warn without blocking native runs | `tests/test_real_home_runs.py::test_run_version_drift_warns_and_continues` and `test_tested_cli_has_no_version_warning` cover both providers. |
| Owner can read hook and version notices | `tests/chat-campaign-batch1.spec.cjs` asserts both messages in conversation activity and confirms hostile markup remains text. |
| Project trust remains effective before a run | `tests/test_provider_trust.py::test_native_run_executes_hook_env_and_mcp_only_after_acceptance` and the trust transaction/deadline suites. |
| Disabled MCP servers are not reintroduced | `tests/test_provider_trust_review.py::test_approved_disabled_project_mcp_never_executes`, `tests/test_provider_trust.py::test_project_mcp_cannot_replace_harness_effects`, and `tests/test_real_home_runs.py::test_native_reader_never_overwrites_owner_mcp_entry` (enabled and disabled collisions). |
| DeepSeek, Local and cloud-scoped retirement remain separate | `tests/test_scoped_home_security.py::test_deepseek_keeps_its_dedicated_home`, `tests/test_deepseek_isolation.py`, `tests/test_local_sandbox.py`, `tests/test_cloud_scoped_retirement.py`. |

## Review and red-first evidence

An independent reviewer inspected the changes and rechecked the five corrected
findings: native Codex feature filters, actual schedule dispatch, DeepSeek
instructions, startup RPC warnings and reader-name collisions. The focused
recheck found no remaining blocking findings.

Before implementation, the first adapter batch had 17 failures and the resource
matrix had 32 failures. The valid browser run failed its new hook-notice assertion
before the rendering fix. An earlier browser attempt could not find Chromium
under the fake home and is excluded from red-first evidence. Startup RPC warning
and reader-collision regressions each failed two cases before their fixes.

## Recorded validation

- Adapter/runtime/schedule campaign: 432 passed, 0 failed (24 affected files).
- State campaign: 168 passed and two obsolete mock-target fixture errors; after
  fixing the fixtures, all 49 tests in the corrective campaign passed, including
  both affected cases.
- Resource campaign: 130 passed, 0 failed (eight affected files/selected nodes).
- Boundary campaign: 70 passed, 0 failed, 2 skipped. The skips are the optional
  installed-Codex transaction cases, requiring `KEEPHARNESS_NATIVE_CODEX`.
- Warning browser regression: passed.
- Conventions tests: 25 passed, 0 failed; checker: zero name errors/warnings,
  Portuguese hits or guest hits.

The complete UI suite ran once against implementation commit `54af4af`:
**166 PASS FILE, 0 FAIL FILE**, exit code 0. No flake or isolated retry occurred.
This includes the notice regression, trust/MCP UI, cloud-mode retirement,
visual checks and the persona matrix. No production file changed during the run.
The preceding warning-rendering unit is commit `64c9191`.

Logs are retained locally under
`/home/sophia/.cache/codex-runs/keepharness/w11-45/implement-evidence/` and
`/home/sophia/.cache/codex-runs/keepharness/w11-45/root-evidence/`.
The temporary homes and test roots are removed after recording the results.
No push, main merge or full Python-suite execution is part of this task.

## Commands

Interpreter: `/home/sophia/.cache/keepharness-gauntlet/venv-lock/bin/python`.
Every run had a hard timeout. `pytest-timeout` was unavailable, so the shell
`timeout` supplied the bound. The full Python suite was not run.

The implementation campaigns used pytest's autouse fake-home fixture and their
own temporary directory. Exact commands (line breaks are for readability):

```sh
TMPDIR=/tmp/claude-w45-TCe40t timeout 15m /home/sophia/.cache/keepharness-gauntlet/venv-lock/bin/python -m pytest -q --tb=short --basetemp=/tmp/claude-w45-TCe40t/pytest-final \
  tests/test_real_home_runs.py tests/test_native.py tests/test_hook_scope.py tests/test_provider_trust.py tests/test_provider_trust_review.py tests/test_env.py tests/test_access_mode_bounds.py tests/test_adapters.py tests/test_scoped_home_security.py tests/test_scheduler.py tests/test_approval_policy.py tests/test_deepseek_isolation.py tests/test_adapter_stderr_capture.py tests/test_runtime_config_golden.py tests/test_native_plugin_inventory.py tests/test_claude_delegation_tools.py tests/test_schedule_internet.py tests/test_adapter_conformance.py tests/test_model_permissions.py tests/test_attachment_excerpt.py tests/test_effect_transport_modes.py tests/test_ask_user_question_mapping.py tests/test_shared_projects.py tests/test_project_folders.py
# 432 passed

TMPDIR=/tmp/claude-w45-TCe40t timeout 15m /home/sophia/.cache/keepharness-gauntlet/venv-lock/bin/python -m pytest -q --tb=short --basetemp=/tmp/claude-w45-TCe40t/pytest-state \
  tests/test_provider_state_codex.py tests/test_provider_state_claude.py tests/test_provider_state_home_isolation.py tests/test_effect_codex_registration.py tests/test_native_session_missing.py
# 168 passed, 2 fixture errors; corrected below

TMPDIR=/tmp/claude-w45-TCe40t timeout 15m /home/sophia/.cache/keepharness-gauntlet/venv-lock/bin/python -m pytest -q --tb=short --basetemp=/tmp/claude-w45-TCe40t/pytest-more \
  tests/test_native_session_missing.py tests/test_provider_login.py tests/test_claude_title_sync.py tests/test_claude_catalog.py
# 49 passed, including both corrected cases
```

The root campaigns additionally set fake homes for server subprocesses before
pytest or Node starts. `/tmp/claude-w45-EjLny5/test-env.sh` contained:

```sh
#!/bin/sh
export HOME=/tmp/claude-w45-EjLny5/home
export CODEX_HOME="$HOME/.codex"
export CLAUDE_CONFIG_DIR="$HOME/.claude"
export XDG_CONFIG_HOME=/tmp/claude-w45-EjLny5/config
export XDG_CACHE_HOME=/tmp/claude-w45-EjLny5/cache
export XDG_DATA_HOME=/tmp/claude-w45-EjLny5/data
export TMPDIR=/tmp/claude-w45-EjLny5
export PLAYWRIGHT_BROWSERS_PATH=/home/sophia/.cache/ms-playwright
export NODE_PATH=/home/sophia/kbd-research/node_modules
export PYTHON=/home/sophia/.cache/keepharness-gauntlet/venv-lock/bin/python
exec env -u DISPLAY -u WAYLAND_DISPLAY "$@"
```

```sh
sh /tmp/claude-w45-EjLny5/test-env.sh timeout 15m /home/sophia/.cache/keepharness-gauntlet/venv-lock/bin/python -m pytest -q --tb=short \
  tests/test_resources.py tests/test_resources_gauntlet.py tests/test_native_resource_materialization.py tests/test_gauntlet_round5_resources.py tests/test_gauntlet_round9_resources.py tests/test_gauntlet_round12_resources.py tests/test_gauntlet_round13_resources.py tests/test_scoped_home_security.py::test_user_scope_resources_ignore_retired_personal_setup \
  --basetemp=/tmp/claude-w45-EjLny5/pytest-resources-final
# 130 passed

sh /tmp/claude-w45-EjLny5/test-env.sh timeout 15m /home/sophia/.cache/keepharness-gauntlet/venv-lock/bin/python -m pytest -q --tb=short \
  tests/test_provider_trust_transaction.py tests/test_provider_trust_timeouts.py tests/test_cloud_scoped_retirement.py tests/test_local_sandbox.py \
  --basetemp=/tmp/claude-w45-EjLny5/pytest-boundaries
# 70 passed, 2 optional installed-Codex cases skipped

sh /tmp/claude-w45-EjLny5/test-env.sh timeout 3m node tests/chat-campaign-batch1.spec.cjs
# Passed (hook/version notice text, hostile markup, existing visual assertions)

sh /tmp/claude-w45-EjLny5/test-env.sh timeout 15m /home/sophia/.cache/keepharness-gauntlet/venv-lock/bin/python scripts/check_conventions.py
# 0 name errors, 0 name warnings, 0 Portuguese hits, 0 guest hits

sh /tmp/claude-w45-EjLny5/test-env.sh timeout 15m /home/sophia/.cache/keepharness-gauntlet/venv-lock/bin/python -m pytest -q --tb=short tests/test_conventions.py --basetemp=/tmp/claude-w45-EjLny5/pytest-conventions
# 25 passed

sh /tmp/claude-w45-EjLny5/test-env.sh timeout 60m ./scripts/test-ui.sh
# 166 PASS FILE, 0 FAIL FILE; exit 0; one full run
```
