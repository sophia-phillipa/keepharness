# Local adapter specification

**Responsible agent:** `integrate-local_tail-harness_engineer` (`.codex/agents/integrate-local_tail-harness_engineer.toml`).

`adapter_spec_revision: 2`
`harness_baseline: 0.4.4 working-tree`

## Observed baseline

- Consulted: 2026-09-19.
- Build recipe pinned by the project: llama.cpp `b11003`, commit `7d6f5d02bb40fca0ab29e65fe4eb86eab6886f19`.
- `llama-server` and `ollama` were not found in `PATH`; no server was started and no `local_ai/` runtime, key, or weights were read.
- Upstream llama.cpp server reference: <https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md>.
- Upstream Ollama OpenAI-compatibility reference: <https://registry.ollama.com/blog/openai-compatibility>.

The build recipe is versioned project configuration, not proof of an installed runtime. Runtime capabilities, loaded weight, tool calling, multimodality, context capacity, streaming and throughput remain unverified until an already-running endpoint is inspected through the harness contract.

## Contract implemented by the harness

| Concern | Current behavior | Validation status |
| --- | --- | --- |
| Input and output | The adapter configures Codex app-server with a local `tail_local` provider, base URL `<endpoint>/v1` and `wire_api="responses"`. | Source/adapter tests and one live Qwen web research turn; see revision 2 below. |
| Session | Local turns use the Codex app-server thread contract and retain only compatible isolated session metadata. | Initial live Qwen turn completed; session resumption remains covered by offline contract tests only. |
| Effort | The local service exposes configured effort values only; profile `reasoning` fields are runtime launch settings, not a verified provider effort API. | Profile schema tests only. |
| Streaming | The adapter relies on app-server events after it calls the local Responses endpoint. | Tool events and answer received in the live Qwen smoke; no Ollama validation. |
| Cancel | Job cancellation follows the app-server/job lifecycle. No local endpoint cancellation acknowledgement is verified. | Documental limitation. |
| Errors | Missing local CLI, filesystem isolation and invalid project scope become explicit harness errors. Endpoint/protocol errors are not mapped from a live runtime here. | Targeted isolation tests; no endpoint compatibility claim. |
| Authentication | A configured local key may be injected only into the isolated environment as `TAIL_HARNESS_LOCAL_KEY`. | Source review; secret value not read or documented. |

llama.cpp documents `GET /v1/models` and `POST /v1/responses`; its server reference says Responses requests are converted to Chat Completions requests. This is a compatibility surface, not proof that every Responses feature or tool pattern required by Codex works with the pinned runtime or a selected GGUF. Ollama's cited official compatibility page documents `/v1/chat/completions`; it does not validate this adapter's `/v1/responses` requirement. Treat Ollama as discovered inventory until a compatible endpoint is directly verified.

## Suggested weights and profiles

- [Qwen3.6 35B A3B UD-Q3_K_M](models/qwen36-35b-a3b-ud-q3-k-m.md) has the two versioned profiles.
- [Gemma 4 E4B Q4_K_M](models/gemma4-e4b-q4-k-m.md) is a pinned download catalog entry with no suggested profile versioned in `profiles/`.

## Review triggers

Review after changing the pinned llama.cpp revision, the endpoint `wire_api`, a profile schema or a selected weight; after a runtime becomes discoverable; or after validating a Responses/tool/stream/cancel feature against an already-running local endpoint. Do not infer capability merely from GGUF presence or an Ollama model listing.

## Web research (revision 2, 2026-09-20)

The local Responses endpoint does not implement Codex hosted web search. Keep
`web_search="disabled"`; instead, when both effective `internet` and `shell`
permissions are enabled, mount the standard-library helper read-only at
`/tail-web-search.py`. The agent invokes `python3 /tail-web-search.py 'query'`
through its existing terminal tool. No model or OpenAI inference intermediary
is involved in retrieval. Query terms are sent to the public Bing RSS endpoint.
The JSON response contains titles, URLs, snippets, retrieval time and an
external-data trust label; snippets are not full-page evidence. The agent must
check relevance, refine queries, deduplicate and fetch sources before citing
claims. Public search can return irrelevant results, throttling or challenges;
errors and insufficient results must be reported honestly.

The helper is unavailable when either permission is disabled. It has a 12-second
network timeout and a 1 MiB response limit, returning a nonzero exit code on
provider/format errors. No credentials, new runtime dependencies or host MCP
configuration are exposed. Existing isolated sessions receive the new developer
instructions on resume.

Validation: targeted search parsing/permission and local adapter policy tests;
real public search helper call, including inside the bubblewrap boundary.
A real `qwen-local` job (`b78aa3a41796468bae90f0764f04f890`) also completed
with nonempty output, `finish_reason=completed`, `incomplete=false`, and recorded
terminal tool calls in 50.97 s (queue 0.007 s; 65,244 input / 1,364 output
tokens reported by the local adapter). It returned three links and reported
limited relevance to the full query. This validates the tool path, not general
research quality or a guaranteed number of relevant sources. DuckDuckGo HTML/Lite
returned challenges in this check. Bing returned results but relevance required query refinement.

## Conversation display title (2026-09-20)

The service passes `_conversation_title` separately from the prompt: the conversation root prompt truncated to 100 characters, overridden by an explicit Harness rename. Provider/model handoffs retain that title; workspace metadata and history wrappers are never used as its source.

The shared Codex executor calls `thread/name/set` on session creation/resume before starting the model turn. This also covers local and DeepSeek inference through Codex; Codex scoped execution uses the same helper. Errors/timeouts emit `session_title_sync_failed` and do not claim successful synchronization. Renames made while no turn is starting are propagated at the next execution, not in real time. Calls without a nonblank title leave existing native titles unchanged.

Contract checked against installed Codex CLI 0.155.0-alpha.9.2 generated `v2/ThreadSetNameParams.json` (`threadId`, `name`) and https://learn.chatgpt.com/docs/app-server . Offline tests validate transport and handoff; no live model inference. This display metadata does not change session identity, isolation or inference model.
