# DeepSeek adapter specification

Responsible agent: `integrate-deepseek_keepharness_engineer`. Adapter spec revision: **1**.
Harness baseline: **0.4.4 working-tree**. Reviewed: **2026-09-19**.
Executor observed: **codex-cli 0.155.0-alpha.9.2**. Remote API: rolling, unversioned `/responses`.
Machine-readable correlation: [compatibility.json](compatibility.json).

## Integration decisions

`backend.py` owns BYOK configuration and reasoning policy; `account.py` checks the model catalog and balance. The Codex transport owns tool execution and local session history. This remains an API-key integration: the CLI is the tool agent, not the inference provider. Secrets stay in the existing private key file and process environment, never command arguments or specs. Missing endpoint/key configuration fails explicitly; there is no fallback to an OpenAI account.

Use HTTP Responses with WebSocket support explicitly disabled. Reuse the existing `native-thread.json` marker and Codex rollout through `thread/resume`. Keep the original marker name so existing conversations remain resumable. Markers now record `adapter=deepseek` and `adapter_spec_revision=1`; legacy markers are accepted and enriched on the next write. `context_strategy=client_history_replay` describes this correctly: the thread ID is local, not a DeepSeek conversation ID.

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

## Conversation display title (2026-09-20)

The service passes `_conversation_title` separately from the prompt: the conversation root prompt truncated to 100 characters, overridden by an explicit Harness rename. Provider/model handoffs retain that title; workspace metadata and history wrappers are never used as its source.

The shared Codex executor calls `thread/name/set` on session creation/resume before starting the model turn. This also covers local and DeepSeek inference through Codex; Codex scoped execution uses the same helper. Errors/timeouts emit `session_title_sync_failed` and do not claim successful synchronization. Renames made while no turn is starting are propagated at the next execution, not in real time. Calls without a nonblank title leave existing native titles unchanged.

Contract checked against installed Codex CLI 0.155.0-alpha.9.2 generated `v2/ThreadSetNameParams.json` (`threadId`, `name`) and https://learn.chatgpt.com/docs/app-server . Offline tests validate transport and handoff; no live model inference. This display metadata does not change session identity, isolation or inference model.
