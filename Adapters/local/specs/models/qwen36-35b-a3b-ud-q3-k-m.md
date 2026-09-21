# Qwen3.6 35B A3B UD-Q3_K_M

**Responsible agent:** `integrate-local_tail-harness_engineer`.

The project catalog pins `Qwen3.6-35B-A3B-UD-Q3_K_M.gguf` from `unsloth/Qwen3.6-35B-A3B-GGUF` at revision `a483e9e6cbd595906af30beda3187c2663a1118c`, SHA-256 `1b715841683f960bd9a49f008181bd910ee169b78d4cf465b6fde7f4d929ff99`. This documents the downloader input, not a locally inspected weight.

Two versioned suggestions point to that filename:

| Profile | Purpose | Not a runtime validation |
| --- | --- | --- |
| `profiles/qwen-author-profile.json` | Author-suggested Vulkan profile with multimodal projector and CPU affinity. | Explicitly authorized example; review hardware, memory and runtime compatibility before use. |
| `profiles/local-cpu-profile.json` | Conservative CPU starting point with no hardware affinity. | It is not a performance claim or universal recommendation. |

The selected runtime must expose a compatible `/v1/responses` endpoint and the selected model must work with the needed tool contract before execution is enabled. llama.cpp documents the endpoint, but no local runtime or weight was loaded for this specification: <https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md>.

Review when either profile, the catalog revision/SHA, the runtime revision, or a verified endpoint capability changes.
