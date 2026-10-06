# D-021 — "Copy Codex" never removes a KeepHarness-only feature

Status: accepted. Date: 2026-10-05. Decided by: Sophia. Spec: [Codex-app parity design](../codex-parity-design.md#keepharness-only-features-protected-in-every-review).

## Context

Copying Codex screens risks dropping features Codex lacks.

## Decision

Each KeepHarness-only feature keeps working and is placed where Codex would put a similar thing (same structure, KeepHarness tokens). The protected list is in the spec. Reviews flag any diff that drops or buries one of them.

## Rationale

Parity is a means to daily use; the multi-provider and harness features are why KeepHarness exists.

## Alternatives

Strict Codex parity, hiding what Codex lacks: rejected by Sophia.

## Impact

Every parity WP's review.
