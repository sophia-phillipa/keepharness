# UC-001 — Ten-user concurrent execution

Date: 2026-09-18
Status: Requirements recorded; multi-provider parallel execution is not implemented.

## Scenario

Ten authenticated people use the same harness from different machines. Each identity may hold at most ten outstanding top-level tasks, across all browsers, tabs, and devices. Users continuously submit tasks while earlier tasks complete or are cancelled. Tasks may spawn child agents, model calls, and tools.

The requested target is up to 100 concurrent top-level tasks across the installation, potentially producing more child executions. This is a product capacity target, not an entitlement to run 100 simultaneous requests through one provider account. Admission, running tasks, child agents, outbound model requests, and open event streams are distinct quantities.

## Current implementation gap

Source currently allows ten queued/running jobs per identity, 32 globally, and one execution worker. Fair selection rotates between least recently served identities, FIFO within each identity. Therefore current code cannot admit all 100 outstanding tasks or run them in parallel. The process connection limit and per-identity SSE cap also require capacity validation. No runtime capacity increase is made by this specification.

The existing shared VPN key represents one identity. Current provider execution uses service-level credentials; it does not yet establish isolated per-person provider accounts. Tailscale identity alone does not solve provider credential isolation.

## Required identities and budgets

- `user_id`: authenticated harness person, stable across devices; never a caller-supplied browser identifier.
- `provider_account_id`: opaque identity of the authorized upstream account; all tasks and descendants using that account share its budget, regardless of model aliases or provider entries.
- `root_task_id`: top-level request; child work inherits ownership, account association, permissions, cancellation, and budgets.
- Distinct limits for user admission, account concurrency, provider/model token and request rates, global resource capacity, and local GPU execution.
- User quota remains ten outstanding tasks. Scheduling must additionally enforce a running share so one person cannot occupy all account slots.
- Completion, failure, cancellation, disconnect, and restart must release or reconcile reservations exactly once.
- Resource acquisition must avoid deadlock when parents await children: a waiting coordinator cannot retain the only execution slot its children need.

## Scheduling and protection requirements

A bounded durable queue must accommodate the agreed 100-task target before advertising it. Dispatch only when user, account, provider, and machine budgets permit. Rotate fairly among eligible users; one blocked account must not stall another account or the local provider. Serialize writes to a shared conversation; isolate concurrent project edits using separate workspaces or an explicit lock.

Apply bounded retries only to classified transient failures. Honor provider retry guidance, pause account dispatch on quota exhaustion, and require intervention for authentication/enforcement failures. Never rotate accounts, IPs, or credentials to evade limits. Do not replay uncertain non-idempotent tool actions automatically.

Count child model work against the same upstream budget. If a CLI does not expose or permit controlling internal request concurrency, report that limit explicitly and conservatively bound its whole process; do not claim request-level enforcement based only on a process count.

## Dashboard and acceptance criteria

Show queued/running top-level tasks separately from child agents, observed outbound requests, token rates, and local hardware usage. Mark unavailable telemetry as unknown. Explain whether waiting is due to the user share, account limit, provider cooldown, project lock, or hardware capacity. Never expose credentials or other users' content through ordinary user views.

1. Ten users can each admit ten tasks in the target configuration; the eleventh per user is refused without losing the draft.
2. Total runnable work never exceeds configured account and machine limits, even with child fan-out.
3. A flooding user cannot starve another eligible user, cancel another user's work, or read their files/history.
4. Provider cooldown affects the matching account across all entry points, while independent accounts continue.
5. Cancellation and viewing progress remain responsive under load.
6. Multi-user provider access is enabled only with an appropriate credential/licensing arrangement, independently of performance results.
