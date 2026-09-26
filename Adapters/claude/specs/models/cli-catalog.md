# Claude CLI model catalog

The `initialize` control response supplies `value`, `resolvedModel`, `disabled`, `supportsEffort`, and `supportedEffortLevels`. The harness keeps selectable aliases and version IDs, including `[1m]` variants and their ordinary IDs. It excludes disabled rows and offers only the reported effort levels plus `configured`. Active legacy versions omitted by the picker are included from the official lifecycle and CLI effort tables: Opus 4.8, 4.7, 4.6, 4.5; Sonnet 4.6, 4.5. Retired models are excluded. Legacy registration is not an account-entitlement guarantee; explicit disabled CLI entries take precedence. Opus 4.5 has API effort support but no CLI effort support in the documented CLI table, so it retains configured effort.

Observed on 2026-09-21 with Claude Code 2.1.236: Sonnet 5, Opus 5, Fable 5.1 and Haiku 4.5; the first three report low/medium/high/xhigh/max and Haiku reports no effort capability. Account policy and future CLI releases can change this list. Discovery does not run inference.

Source: https://code.claude.com/docs/en/model-config

Lifecycle source, reviewed 2026-09-21: https://platform.claude.com/docs/en/about-claude/model-deprecations
