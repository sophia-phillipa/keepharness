# Concurrency validation plan

Date: 2026-09-18
Status: Proposed protocol. No live batch was run for this research.

## What a greeting can prove

Prompt: `Reply only with: oi. Do not use tools, files, network access, or subagents.`

A successful reply tests authentication, routing, response parsing, and basic streaming. Prompt instructions alone are not a security boundary: disable tools, connectors, hooks, and child agents through supported runtime controls, using an empty isolated workspace. System context still consumes tokens. A greeting does not reproduce realistic long-context tasks, fan-out, sustained token rates, or delayed account enforcement. Passing it cannot certify policy compliance or freedom from future suspension.

## Stage A — local simulation

Use a fake provider with ten synthetic authenticated users and no upstream credentials. Exercise 100 outstanding tasks, 10 per user, plus rejection of each eleventh task. Simulate variable duration, child fan-out, disconnects, timeouts,429 with Retry-After, unavailable quota,401/403, and provider capacity failures. Verify fairness, reservations, bounded memory, cancellation, recovery, conversation isolation, and account-wide cooldown. Use both ASGI checks and socket-level tests; existing ASGI burst tests are not network capacity certification.

Record requested/admitted/running/finished tasks, per-user wait percentiles, account slot occupancy, duplicate execution count, observed request rates, event connections, and server CPU/RAM. The current32-job queue and single worker do not satisfy the100-task target; document that gap rather than relabel rejected tasks as supported concurrency.

## Stage B — minimal authorized live smoke test

First identify account type, owning person or organization, intended users, supported CLI authentication, and documented budget. Use a permitted account arrangement. Run one greeting per selected provider with a60-second timeout, one attempt, and no automatic resubmission. Record CLI version, model, effort, timestamps, latency, token usage where available, sanitized errors, and account usage before/after when exposed.

This is a connectivity check, not a load test. Do not run100 greetings through a personal subscription to discover a hidden enforcement threshold.

## Stage C — bounded capacity experiment

Only after Stage A passes and the account arrangement is established, set an explicit experiment request/cost budget and a documented target capacity. Start sequentially; a small two-request parallel sample may test session independence if permitted by the provider's limits. Increase only toward a justified operational target within known limits, not until an account blocks. Stop on quota exhaustion, authentication/enforcement signals, repeated transient failures, or unexpected spend. Respect cooldown; do not mask traffic or switch identities to continue.

Acceptance means the measured configuration handled the measured workload within limits. It does not mean the provider has approved all future workloads. Sustained100-task capability remains unverified until both scheduler/resource tests and the relevant account capacity checks pass.
