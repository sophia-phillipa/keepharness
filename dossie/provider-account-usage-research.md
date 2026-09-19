# Provider account usage research

Reviewed: 2026-09-18. Status: source review, not a provider approval or a ban-risk guarantee.

## Findings

OpenAI's individual terms prohibit making an account available to other people and bypassing service limits. Separately, official Codex documentation supports scripted execution with `codex exec`. Automation alone therefore must not be equated with prohibited use; sharing a personal account is a separate issue. [Individual terms](https://openai.com/policies/row-terms-of-use/) · [Non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode).

Anthropic's Claude Code documentation distinguishes consumer subscriptions from commercial access. For hosted Claude Code, it describes unmodified binaries and each end user's own authentication. It forbids routing third-party users through Free/Pro/Max credentials and restricts collecting or intermediating subscription tokens. It also describes authorized organizational API-key provisioning. These arrangements must not be conflated. [Claude Code legal documentation](https://code.claude.com/docs/en/legal-and-compliance).

Anthropic consumer terms prohibit account sharing, unauthorized automation, and disruptive use; permitted API or explicitly authorized automation has a separate basis. [Consumer terms](https://www.anthropic.com/legal/consumer-terms).

## Direct user report, not verified causation

A Reddit author reports a ban after changing from two or three workers to ten parallel Claude Code workers driven by a Python harness. The thread contains conflicting experiences and no verified enforcement explanation. It establishes that someone reported this sequence, not that ten workers is a ban threshold or that smaller batches are safe. [Original first-person report](https://www.reddit.com/r/ClaudeCode/comments/1s671dg/banned_wout_warning/).

The targeted Codex searches did not establish a comparable first-person case with a confirmed provider explanation that ordinary batch CLI concurrency alone caused suspension. This is a limited search result, not evidence that bans cannot occur.

## Consequence for this project

Ten harness identities must not silently share one person's consumer subscription. Use individually authorized provider access or an appropriate organizational API/commercial arrangement, checking hosted-product restrictions separately. A provider agreement may be necessary for arrangements not clearly covered by published documentation. No numeric concurrency setting guarantees compliance.

Rate limiting (429), capacity errors, exhausted usage allowance, authentication failures, and account enforcement are different outcomes. Classify them separately. Do not interpret every403 as a ban or every successful200 as approval of the business model.
