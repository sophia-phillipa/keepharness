# Codex CLI dynamic catalog

**Responsible agent:** `integrate-codex_tail-harness_engineer`.

The only effective Codex model catalog is the authenticated result of `model/list`, read by `control/server.py`. Each returned model ID is paired with `supportedReasoningEfforts`; when that list is empty, the harness currently exposes `low` as its fallback. The project must still explicitly enable the model and its efforts for each project.

This is a runtime/account catalog, not an alias promise. Do not replace it with a hard-coded list from UI labels, tests, or this document. Authentication, account entitlement, CLI version, and provider rollout can change the result.

**Observed CLI baseline:** `codex-cli 0.155.0-alpha.9.2` on 2026-09-19. No `model/list` request was made for this specification, so no concrete model or effort is validated for this account.

Official protocol: <https://developers.openai.com/pt-BR/docs/app-server>. It documents `thread/start` and `turn/start` model/effort fields, JSON-RPC transport, streamed notifications and errors; schema output is version-specific. Recheck after a CLI or protocol update.
