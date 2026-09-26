# UC-002 — Model-specific local execution profiles

Date: 2026-09-18
Status: Implemented in development preview.

Each canonical GGUF path owns a private execution profile. Different quantizations are independent. The legacy single profile is read compatibly and retained; adding another profile does not overwrite entries belonging to other model files.

The administrator selects the target model by full filename before opening Advanced execution settings. CPU affinity, GPU layer allocation, threads, context, cache, reasoning, and supported sampling settings belong to that file. Unknown fields cannot become arbitrary CLI arguments. Operation permissions and declared tool compatibility now belong to each exact model file too. API aliases are bound to discovered canonical weight paths; unmatched or ambiguous mappings get no permissions. Project membership remains a service-level list and model read permission further restricts access. Local native execution now uses a bubblewrap filesystem boundary; see UC-003 for additive project grants and validation evidence.

Saving a profile persists it immediately and does not restart a running model. Copying detected runtime settings only targets the selected file. Starting with a saved profile requires an exact path match. Export/import carries the profile collection while accepting older single-profile bundles. Unsaved profile edits require a discard decision when navigating away; general provider Save must not silently drop or apply advanced edits.

## Local installation

Gemma4E4B Q4_K_M GGUF was downloaded using the existing pinned installer and verified against its SHA-256:85a896a047553e842f25297ee5b031d64ff30147d9c4af17b1e4b394cd1fab87. File size:4,977,164,544bytes. Source: https://huggingface.co/unsloth/gemma-4-E4B-it-GGUF/tree/bfc15c382204943c3a8fff0c750b94ae2364d7a3

Its independent initial profile uses CPU inference,4threads,8192context and1slot. No CPU affinity from Qwen was copied. This is a conservative unbenchmarked starting profile, not a performance optimum. Gemma was installed but not started; Qwen remained running. Weights and private profiles are outside the repository.

## Acceptance checks

Automated backend checks cover legacy migration, profile isolation, input validation, exact-match launch, and export/import. Browser validation covers two quantizations, model selection without parameter inheritance, saving only the selected profile, dirty-edit navigation, and mobile layout. See the UI validation test for reproducible fixtures; all test inference is mocked.

## 2026-09-18 follow-up: model acquisition and authorization

The editor separates downloading weights from selecting an existing GGUF file. A single selected model owns hardware and operation permissions. GPU device discovery invokes informational runtime flags only. Multi-GPU options are validated against supported runtime flags on startup. A CPU-only second runtime may run alongside an existing GPU runtime; a second GPU launch remains blocked.

The historically benchmarked official Google Gemma E4B QAT Q4_0 was installed with SHA-256 `676c35070db6dbe52f93e9c864ee0fba4eddea94b9c875d9cb10daff453fbaee`. Its preview uses an independent CPU profile to preserve the running Qwen GPU model. Historical GPU settings were retained privately.

An administrative preview/runtime mismatch was found: admin settings allowed uploads and internet while the separate harness preview disabled them. The preview is now managed through the same admin state and keeps the existing conversation database. The harness displays effective permissions for the selected model and blocks attachments when switching to a model that disallows them.
