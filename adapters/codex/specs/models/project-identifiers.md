# Codex identifiers declared by the project

**Responsible agent:** `integrate-codex_tail-harness_engineer`.

`gpt-6-astra`, `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna`, and `gpt-5.5` occur in the harness defaults, UI labels, agent settings, or tests. They are **not** a static Codex adapter catalog and this repository has no public-model compatibility evidence for them.

`gpt-6-astra` is the code default for some Codex paths. That default is accepted only when the configured, authenticated `model/list` result contains it with the requested effort. The other identifiers are presentation/configuration references until the same condition is met.

No public OpenAI documentation was found in this review that verifies these exact identifiers for `codex-cli 0.155.0-alpha.9.2`; do not infer availability, aliases, reasoning levels, context window, or account access from their names. Reclassify any identifier only after a fresh CLI catalog observation and a directly affected contract test.

Protocol reference consulted 2026-09-19: <https://developers.openai.com/pt-BR/docs/app-server>.
