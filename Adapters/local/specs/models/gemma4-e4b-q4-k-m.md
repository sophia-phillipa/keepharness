# Gemma 4 E4B Q4_K_M

**Responsible agent:** `provedor_local`.

The project catalog pins `gemma-4-E4B-it-Q4_K_M.gguf` from `unsloth/gemma-4-E4B-it-GGUF` at revision `bfc15c382204943c3a8fff0c750b94ae2364d7a3`, SHA-256 `85a896a047553e842f25297ee5b031d64ff30147d9c4af17b1e4b394cd1fab87`.

Gemma is also represented in local-model permission contracts, but no Gemma suggested profile is versioned under `profiles/`. Test fixtures are not a hardware profile or evidence that a real weight is installed, loaded, supports tools, or works with `/v1/responses`.

The local adapter's required protocol is documented against llama.cpp's Responses endpoint: <https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md>. The current runtime is not discoverable from `PATH`, so this integration has no runtime validation. Review after adding a versioned Gemma profile, changing the catalog pin, or verifying endpoint behavior against an already-running runtime.
