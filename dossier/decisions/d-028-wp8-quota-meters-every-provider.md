# D-028 — WP8 quota meters for every connected provider

Status: accepted (requested; not started). Date: 2026-10-05 (window 7). Decided by: Sophia. Spec: [WP8](../codex-parity-design.md#wp8-quota-meters-for-every-connected-provider-frontend--small-python).

## Context

The rail showed a quota meter only for providers whose quota has windows (for example "Codex 22%"). Sophia: "seria legal aparecer do Claude ali também, ou seja de todo provedor que estiver conectado" ("it would be nice to show Claude there too, that is, every connected provider"). <!-- conventions: allow-pt -->

## Decision

One meter per connected or enabled provider. A provider that reports no quota shows a neutral "n/a" meter with the reason in its tooltip instead of disappearing. First check that Claude's quota reaches the rail when Claude is connected. Only `--th-*` tokens ([D-020](d-020-theme-tokens-for-copied-screens.md)); a visible pass is required ([D-029](d-029-visible-pass-before-merge.md)).

## Rationale

A meter that disappears reads as "no limit" or "broken"; a neutral meter says the provider is connected and reports nothing. Codex shows only its own quota, so this is a KeepHarness-only feature ([D-021](d-021-keep-keepharness-only-features.md)).

## Alternatives

Show meters only when quota data exists (the current behavior).

## Impact

`agent_service/ui.js` (`updateProviderQuotas`), `agent_service/services/conversation_service.py` (`claude_quota`), `adapters/claude/account.py`.
