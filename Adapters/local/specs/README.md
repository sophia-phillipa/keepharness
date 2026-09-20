# Local adapter specification

**Responsible agent:** `provedor_local` (`.codex/agents/provedor_local.toml`).

`adapter_spec_revision: 1`
`harness_baseline: 0.4.4 working-tree`

## Observed baseline

- Consulted: 2026-09-19.
- Build recipe pinned by the project: llama.cpp `b11003`, commit `7d6f5d02bb40fca0ab29e65fe4eb86eab6886f19`.
- `llama-server` and `ollama` were not found in `PATH`; no server was started and no `local-ai/` runtime, key, or weights were read.
- Upstream llama.cpp server reference: <https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md>.
- Upstream Ollama OpenAI-compatibility reference: <https://registry.ollama.com/blog/openai-compatibility>.

The build recipe is versioned project configuration, not proof of an installed runtime. Runtime capabilities, loaded weight, tool calling, multimodality, context capacity, streaming and throughput remain unverified until an already-running endpoint is inspected through the harness contract.

## Contract implemented by the harness

| Concern | Current behavior | Validation status |
| --- | --- | --- |
| Input and output | The adapter configures Codex app-server with a local `tail_local` provider, base URL `<endpoint>/v1` and `wire_api="responses"`. | Source and adapter tests; no live local endpoint call. |
| Session | Local turns use the Codex app-server thread contract and retain only compatible isolated session metadata. | Contract inherited from Codex adapter tests; no local runtime session validated. |
| Effort | The local service exposes configured effort values only; profile `reasoning` fields are runtime launch settings, not a verified provider effort API. | Profile schema tests only. |
| Streaming | The adapter relies on app-server events after it calls the local Responses endpoint. | Not validated against llama.cpp or Ollama in this working tree. |
| Cancel | Job cancellation follows the app-server/job lifecycle. No local endpoint cancellation acknowledgement is verified. | Documental limitation. |
| Errors | Missing local CLI, filesystem isolation and invalid project scope become explicit harness errors. Endpoint/protocol errors are not mapped from a live runtime here. | Targeted isolation tests; no endpoint compatibility claim. |
| Authentication | A configured local key may be injected only into the isolated environment as `TAIL_HARNESS_LOCAL_KEY`. | Source review; secret value not read or documented. |

llama.cpp documents `GET /v1/models` and `POST /v1/responses`; its server reference says Responses requests are converted to Chat Completions requests. This is a compatibility surface, not proof that every Responses feature or tool pattern required by Codex works with the pinned runtime or a selected GGUF. Ollama's cited official compatibility page documents `/v1/chat/completions`; it does not validate this adapter's `/v1/responses` requirement. Treat Ollama as discovered inventory until a compatible endpoint is directly verified.

## Suggested weights and profiles

- [Qwen3.6 35B A3B UD-Q3_K_M](models/qwen36-35b-a3b-ud-q3-k-m.md) has the two versioned profiles.
- [Gemma 4 E4B Q4_K_M](models/gemma4-e4b-q4-k-m.md) is a pinned download catalog entry with no suggested profile versioned in `profiles/`.

## Review triggers

Review after changing the pinned llama.cpp revision, the endpoint `wire_api`, a profile schema or a selected weight; after a runtime becomes discoverable; or after validating a Responses/tool/stream/cancel feature against an already-running local endpoint. Do not infer capability merely from GGUF presence or an Ollama model listing.
