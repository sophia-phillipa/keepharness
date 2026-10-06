# D-027 — WP7 visual markers are deterministic and display-only

Status: accepted. Date: 2026-10-05 (window 7). Decided by: Sophia (feature and "no AI"), a JEV choice (no prompt insert, option B, confidence 1.00) and the coordinating session in window 7 (extraction rules and the web-search title). Spec: [WP7](../codex-parity-design.md#wp7-visual-markers-frontend--small-python). Implementation: branch `feat/wp7-markers`, not merged at the time of writing.

## Context

Sophia asked, like ChatGPT, for icons showing what the model is doing (file opened, skill used, agent used), always highlighted, computed deterministically, with a prompt insert only if unavoidable.

## Decision

1. No prompt insert: markers come from structured tool events, plus prose chips only for exact catalog names or known project paths (never inside code blocks).
2. Skill and agent names are extracted only from already-redacted text, from reader commands only (`cat`, `sed`, `head`, `tail`, `less`, `bat`, `nl`) for `<name>/SKILL.md`; they are display-only and never forwarded to another provider (`portable_history` drops `skill` and `agent` with `target`).
3. The Claude `WebSearch` step title becomes "Searching the web" / "Searched the web" (Codex wording).
4. Icons from the Tabler sprite and `--th-*` tokens; an Appearance toggle stored as the WP6 key `visual_markers`, on by default.

## Rationale

Structured events are stable across providers and need no model cooperation; redacting before extraction keeps a secret in a command line from becoming a chip; limiting to reader commands avoids naming a skill the model only listed or wrote.

## Alternatives

A prompt insert asking models to announce tools (provider-dependent, costs tokens); free-phrase matching in prose (false positives).

## Impact

`agent_service/tool_metadata.py`, `agent_service/conversation_context.py`, `agent_service/ui.js`, `agent_service/ui_state.py`, `tests/visual-markers.spec.cjs`.
