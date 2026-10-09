# DeepSeek adapter specification

Responsible agent: `integrate-deepseek_keepharness_engineer`. Adapter spec revision: **1**.
Harness baseline: **0.16.0 working-tree**. Reviewed: **2026-10-08**.

## Provider state facade

`DeepSeekStateAdapter` reuses the Codex state reader and CLI writer with provider
`deepseek`, engine `codex`, and `CODEX_HOME` and `HOME` both set to
`<state>/providers/deepseek`, the same private home as execution. State RPCs pin file credential
storage and reject symlinked home paths, nonregular or multiply linked private
config files (including symlinks and hardlinks), and foreign authentication entries
before starting the CLI. Listing state
does not require or read the API key. `set_api_key` delegates to the existing
atomic private key writer.

The Plugins page shows DeepSeek's own state and sends its own fingerprint when
switching a row. Profile layers retain the Codex writer's read-only rules. Hook
and instructions rows are consumed when reported by an adapter; the Codex reader
does not invent unsupported rows. DeepSeek has no directory or uninstall command
in this facade. Shared `~/.agents/skills` roots are watched and named using each
adapter's actual HOME: content changes affect other providers using that root,
while switches change only the selected provider's config. A private DeepSeek
root is never described as the owner's root or assigned invented `affects` IDs.
Executor tested: **codex-cli 0.157.1**. Remote API: rolling, unversioned `/responses`.
Machine-readable correlation: [compatibility.json](compatibility.json).

## Integration decisions

`backend.py` owns BYOK configuration and reasoning policy; `account.py` checks the model catalog and balance. The Codex transport owns tool execution and local session history. This remains an API-key integration: the CLI is the tool agent, not the inference provider. Secrets stay in the existing private key file and process environment, never command arguments or specs. Missing endpoint/key configuration fails explicitly; there is no fallback to an OpenAI account.

Use HTTP Responses with WebSocket support explicitly disabled. Reuse the existing `native-thread.json` marker and Codex rollout through `thread/resume`. Keep the original marker name so existing conversations remain resumable. Markers record `provider=deepseek`, `engine=codex`, `adapter=deepseek` and `adapter_spec_revision=1`, preserving the thread ID and cumulative usage. Legacy markers with `adapter=deepseek` establish provenance and gain the identity fields only on their next normal write. A missing marker starts a new session. An existing marker without a valid thread ID or known provenance refuses with `deepseek_session_identity_ambiguous`; explicit foreign provider, engine or adapter refuses with `deepseek_session_identity_mismatch`, before any process, resume or turn. Refusal preserves the marker and history; explicitly start a new native session to continue. Other Codex-backed providers cannot resume a DeepSeek-tagged thread. No engine fallback or bulk migration occurs. `context_strategy=client_history_replay` describes this correctly: the thread ID is local, not a DeepSeek conversation ID.

The service validates the same marker identity before context recovery or transfer may archive it, and the adapter repeats the check before execution. A missing/stale harness cursor or a context-overflow recovery cannot bypass refusal. Compatible DeepSeek/Codex markers retain the existing portable-history recovery behavior. `tests/test_deepseek_service_identity.py` exercises these paths through `Service.infer` with completed prior turns and fake app-server RPC.

The API requires client-supplied history and does not implement server conversation IDs or stored response chaining. Plain reasoning items, messages and paired tool calls/results must survive replay; reasoning summaries/encrypted content cannot substitute for plain reasoning. Source: [Responses guide](https://api-docs.deepseek.com/guides/responses_api/).

For a new implementation using Chat Completions, retain complete assistant messages and tool results. When tools are sent in thinking mode, preserve `reasoning_content` across requests; the documented rule also covers earlier turns without tool calls. Source: [Thinking mode](https://api-docs.deepseek.com/guides/thinking_mode/). This adapter uses Responses instead, and does not maintain a second, divergent chat transcript.

`configured` maps to this integration's documented default `high`, rather than an unrelated global CLI profile. Explicit `none`, `low`, `high`, `max` remain available; invalid values fail before execution. Summaries are disabled while raw reasoning remains in the transport history. This changes the previous meaning of `configured` for DeepSeek; choose an explicit effort to preserve a previous preference.

## Models

- [deepseek-flash](models/deepseek-flash.md)
- [deepseek-v4-pro](models/deepseek-v4-pro.md)

`GET /models` still controls account availability. A static spec records a known contract, not entitlement or a frozen remote model build. See the [API reference](https://api-docs.deepseek.com/api/create-response/) and [official Codex integration](https://api-docs.deepseek.com/quick_start/agent_integrations/codex/). We use per-process options; we do not run the documentation's setup script or replace the user's global profile.

## Validation and limitations

`tests/test_deepseek_continuity.py` starts the installed CLI against a stateless localhost SSE fixture with a dummy key. It exercises a tool call, its paired output, a final answer, process termination, session resume, and a second user turn. The captured requests must retain prior user/assistant text and raw reasoning. It also checks effort transmission and absence of server-side conversation chaining. This proves the observed CLI/wire combination, not authenticated DeepSeek inference or account access.

Other tests cover dispatch, missing configuration, key privacy and model catalog/account errors. Process cancellation and API errors use the Codex transport; no claim is made about remote billing cancellation. No live DeepSeek request or paid inference was performed. History can still be compacted by the CLI when context grows; the fixture does not certify arbitrary-length compaction. Do not silently retry a tool-executing turn after a resume error.

## Change policy

Read this local spec first. Review after changes to the CLI version, remote API contract, model alias, effort mapping, tool items, persistence or compaction. Increment the spec revision with corresponding code/tests, retain the old revision record in Git, and update `compatibility.json` and model notes. A matching version is necessary evidence, never proof by itself; rerun the local wire test for each new CLI baseline. Recheck official sources when this boundary changes, not on every prompt.

## Transport errors and dropped setting (2026-10-03, codex-cli 0.157.1)

- `model_supports_reasoning_summaries=true` is no longer passed: 0.157.1 ignores the key and logs an ERROR on every run (HAR-R2-9). `model_reasoning_summary="none"` stays; `tests/test_deepseek_continuity.py` still sees raw reasoning replayed.
- Failures carry the provider name and its words: `deepseek_execution_failed: <message>`, with `additionalDetails` and the HTTP status. 401 maps to `provider_authentication_required` (the UI asks to replace the API key) and 402 "Insufficient Balance" to `provider_quota_exhausted` (the UI asks to top up the balance). `error` notifications with `willRetry: true` no longer end the run (HAR-R3-2).
- A lost Codex thread falls back to the harness history, as described in the Codex spec.

## Conversation display title (2026-09-20)

The service passes `_conversation_title` separately from the prompt: the conversation root prompt truncated to 100 characters, overridden by an explicit Harness rename. Provider/model handoffs retain that title; workspace metadata and history wrappers are never used as its source.

The shared Codex executor calls `thread/name/set` on session creation/resume before starting the model turn. This also covers local and DeepSeek inference through Codex; Codex scoped execution uses the same helper. Errors/timeouts emit `session_title_sync_failed` and do not claim successful synchronization. Renames made while no turn is starting are propagated at the next execution, not in real time. Calls without a nonblank title leave existing native titles unchanged.

Contract checked against installed Codex CLI 0.155.0-alpha.9.2 generated `v2/ThreadSetNameParams.json` (`threadId`, `name`) and https://learn.chatgpt.com/docs/app-server . Offline tests validate transport and handoff; no live model inference. This display metadata does not change session identity, isolation or inference model.

## Credential and shell isolation (2026-10-08, issues #48/#49)

The absolute configured key file anchors the state directory. Every attended or scheduled run
uses `<state>/providers/deepseek` as both `CODEX_HOME` and `HOME`, regardless
of inherited homes or vault injections. `set_api_key` provisions this
owner-only directory. A missing, linked, foreign-owned or nonprivate home refuses with
`deepseek_credential_isolation`; execution never repairs it or falls back to the owner environment.
The provider remains `deepseek`, engine `codex`, transport `tail_api`, with
`requires_openai_auth=false` and `cli_auth_credentials_store="file"` pinned on the command line.

Before launching the engine, `auth.json`, `secrets/codex_auth.age`, matching keyring metadata or
any failed/inconclusive probe refuses with that same stable error. No credentials are read,
deleted, migrated or copied by the probe. The mocked identifier contract follows the pinned
[Codex auth storage](https://github.com/openai/codex/blob/rust-v0.157.1/codex-rs/login/src/auth/storage.rs)
and [secrets namespace](https://github.com/openai/codex/blob/rust-v0.157.1/codex-rs/secrets/src/lib.rs):
service `Codex Auth`, account `cli|<digest>`, and service `codex`, account `secrets|<digest>`;
`digest` is the first 16 lowercase hexadecimal characters of SHA-256 of the canonical home path.
The latter shared passphrase is conservatively refused even when only MCP secrets use it.
Linux uses Secret Service `SearchItems` through `gdbus`, matching `service` and `username`, without
retrieving secret values. macOS uses `security find-generic-password` without `-w`; status 44 alone
means absent. Missing tools, inaccessible stores, unexpected replies and unsupported platforms
refuse the run. Tests replace every OS query with a fake store or process response; actual OS
keyring interoperability has **not** been certified by these tests.

The version-tested policy is `shell_environment_policy` with `inherit="all"`,
`ignore_default_excludes=false`, `filters={KEEPHARNESS_API_KEY="exclude"}`, `set={}` and
`experimental_use_profile=false`, with `allow_login_shell=false` also pinned. `filters` is the supported keyed syntax; do not mix it with
legacy `exclude`/`include_only`. The pinned
[configuration parser](https://github.com/openai/codex/blob/rust-v0.157.1/codex-rs/config/src/shell_environment_policy.rs)
normalizes filter names case-insensitively. CLI tables merge with home/project configuration:
`set={}` does not erase inherited assignments, and `set` can reinsert an excluded variable.
Consequently, the adapter checks the app-server's effective `config/read` at the execution cwd
before any thread start/resume. Missing policy evidence, an ineffective exclusion/store setting,
profile environment activation, or `set` containing the key variable or a known secret refuses.
No configuration files are rewritten to bypass this refusal.

`test_pinned_codex_shell_excludes_deepseek_key_with_hostile_config` asserts exactly 0.157.1 and
uses a local SSE endpoint to request a real `exec_command` running `env`. The fixture uses full
shell access to isolate environment-policy behavior from host sandbox availability. It observes
the fake key in HTTP Authorization and its absence, including the variable name, from the shell
result, with a hostile inherited key and exact/wildcard include filters. The fixture uses the default non-login shell. A subsequent keyring probe
failure prevents engine spawn; a hostile `set` reinsertion refuses before another API request,
leaving the continuation marker untouched. This is local transport evidence, not paid inference.

API-key updates retain `deepseek.key`, use a unique mode-0600 sibling and atomic replacement in
a mode-0700 directory, refuse symlink/hardlink targets and directory links, and serialize writers
with a nonblocking directory lock. An observed concurrent change refuses; failed writes preserve
the previous bytes and unrelated settings. This lock coordinates KeepHarness writers, not
arbitrary same-user processes. Exact key values register with the existing stream/log redactor
for the execution lifetime; split answer/reasoning deltas, provider errors and stderr diagnostics
are sanitized before leaving the adapter. Existing account, 401/402 and balance behavior stays.
