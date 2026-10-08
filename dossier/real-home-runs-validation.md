# Real-home native runs validation (#45)

All tests use temporary fake `HOME`, `CODEX_HOME` and `CLAUDE_CONFIG_DIR`.
No owner CLI state or credentials are fixtures. No cloud inference is performed.

## Named acceptance evidence

| Requirement | Evidence |
| --- | --- |
| Native homes and all four presets, attended and scheduled | `tests/test_real_home_runs.py::test_spawned_native_cli_presets_and_schedule_use_owner_home` (16 spawned cases) and `test_native_presets_keep_real_home_and_orchestration` (16 builder cases) assert inherited environment, native modes and absent legacy filters. |
| Absent home overrides stay absent | `tests/test_real_home_runs.py::test_unset_native_home_overrides_stay_unset` (both providers); `tests/test_env.py::test_native_homes_are_inherited_and_deepseek_home_is_separate`. |
| No personal instruction injection or reads for Codex/Claude | `tests/test_real_home_runs.py::test_run_settings_never_reads_personal_files` and the preset matrix. |
| Native resources without the retired opt-in | `tests/test_resources.py::test_native_resources_use_cli_home_for_every_preset_and_schedule` (32 cases) and `test_scheduled_run_resolves_native_owner_prompts`. |
| Scheduled execution retains owner orchestration and network policy | `tests/test_schedule_internet.py::test_codex_scheduled_runs_keep_owner_network_grant` and `test_claude_scheduled_runs_keep_owner_web_tools` exercise `ConversationService.infer`; the Gemini control retains its existing behavior. |
| Codex hooks pending review are visible and never bypassed | `tests/test_real_home_runs.py::test_codex_pending_hooks_are_visible_without_bypassing_review`, `test_codex_hook_warning_before_rpc_reply_is_visible_once` (initialize and thread-start), and the spawned matrix's forbidden-bypass assertion. |
| Unsupported versions warn without blocking native runs | `tests/test_real_home_runs.py::test_run_version_drift_warns_and_continues` and `test_tested_cli_has_no_version_warning` cover both providers. |
| Owner can read hook and version notices | `tests/chat-campaign-batch1.spec.cjs` asserts both messages in conversation activity and confirms hostile markup remains text. |
| Project trust remains effective before a run | `tests/test_provider_trust.py::test_native_run_executes_hook_env_and_mcp_only_after_acceptance` and the trust transaction/deadline suites. |
| Disabled MCP servers are not reintroduced | `tests/test_provider_trust_review.py::test_approved_disabled_project_mcp_never_executes`, `tests/test_provider_trust.py::test_project_mcp_cannot_replace_harness_effects`, and `tests/test_real_home_runs.py::test_native_reader_never_overwrites_owner_mcp_entry` (enabled and disabled collisions). |
| DeepSeek, Local and cloud-scoped retirement remain separate | `tests/test_real_home_runs.py::test_deepseek_personal_instructions_remain_separate_from_native_homes`, `tests/test_deepseek_isolation.py`, `tests/test_local_sandbox.py`, `tests/test_cloud_scoped_retirement.py`. |

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

The complete UI campaign and final integration evidence are recorded after the
implementation commit; they are not claimed by the focused results above.
