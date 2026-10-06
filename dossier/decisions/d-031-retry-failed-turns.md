# D-031 — Retry failed or interrupted turns, and image-capability guidance

Status: accepted. Date: 2026-10-06. Decided by: Sophia (no Retry on cancelled turns; Retry uses the failed turn's model; image failures must be clear and guide to another model). Spec: [Codex-app parity design](../codex-parity-design.md). Release notes: [v0.16.0](../releases/v0.16.0.md).

## Context

Codex shows no observable failed-turn Retry ([inventory](../research/codex-app-inventory-2026-10.md), lines 250-253). KeepHarness only refills the composer, so the user resends by hand and a double click or a second tab can run the turn twice. Separately, DeepSeek is not multimodal for now: sending it an image fails, and the UI cannot tell before sending which models read images.

## Decision

- `POST /v1/jobs/{job}/retry` with body `{}`, next to `/resume`. It accepts only a source whose state is `failed` or `interrupted` and that is still the latest turn of its conversation, with the same owner and access checks as resume.
- The retry is a new child turn built from the source's prompt, file ids, backend, model, effort, access mode, resource selections, invocations and task label, with `parent_job_id` and `retry_of` set to the source. It goes through `submit_async` with the idempotency key `retry:<source>`, so admission (access mode, assessment, attachments) runs again and the existing UNIQUE(owner, project, idem) allows one retry per source across tabs. A replay returns the same job with `reused: true`.
- Refusals: 409 `retry_source_not_failed` (completed, running, queued, cancelled), 409 `retry_source_superseded`, 409 `retry_not_supported` (workflow, schedule and `maestro_*` sources). Errors from re-validation (model gone, invalid file id, full access disabled, image refusals) pass through unchanged.
- No Retry on cancelled turns (the user chose to stop), lost-connection turns (Resume tracking covers them) or workflow turns (Resume workflow covers them).
- The history shows a "Retried" marker on the source instead of a repeated prompt.
- Images: `capabilities.images` in `/v1/models` follows the rules of `validate_images`. The composer prevents sending an image to a model with `images: false` (a warning naming the model, with "Choose another model" and "Remove image"), and a turn that still fails for image capability shows "Choose another model" instead of Retry. The misleading `select_model_for_image` copy is fixed.

## Rationale

`submit_async` already owns admission and idempotency, so Retry adds no new state or storage. A failure caused by a model that cannot read images is deterministic: retrying would fail again, so the user is guided to another model rather than offered a button that cannot work.

## Alternatives

- Client-only resend: no idempotency across tabs; duplicate runs.
- Automatic backoff retry: sends a turn without a click.
- In-place replacement of the failed turn: rewrites history.
- Prevent-only or fail-only for images: prevent-only leaves a stale composer able to send; fail-only tells the user after the fact.

## Impact

New endpoint and error codes; additive `retry_of` on `job.request`; additive `capabilities.images` on every model of `/v1/models`. `validate_images` keeps its behavior and shares one rule with the list. The UI follows in the 0.16.0 UI package.
