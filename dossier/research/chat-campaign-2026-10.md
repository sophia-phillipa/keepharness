# Chat campaign, October 2026

Status: pilot done (2026-10-05); slices 1 to 4 pending. Diataxis kind: explanation (campaign log and findings). The scenario catalog lives in `tests/operator/chat-campaign/plan.md`; fixes are listed in `CHANGELOG.md` under Unreleased.

## Purpose

Chat is the core of 0.16.0. This campaign exercises it with real providers, in visible runs on the owner's screen, and records timings, regressions and bugs in one place so each fix can be traced to the run that found it. Fixtures never stand in for the provider here; quota or credit errors stop the campaign.

## Setup

- **Instance**: an isolated admin and harness with the harness on port 18640 and the admin on port 18641, with state under `~/.cache/kho/chat`. It never touches the owner's everyday state.
- **App**: desktop package 0.16.0 built from commit `f384035`; the runs use a copy with only the Node inspect fuse turned on so Playwright can attach (see plan.md, How to run).
- **Providers**: Codex with `gpt-5.6-sol` at medium effort and `gpt-5.6-luna`; DeepSeek, whose key the owner types in Settings > Providers of the test instance (nobody else types keys, and keys are never written to this file).
- **Attach-mode limitation**: the desktop app attaches to the admin that is already running, so closing the app does not stop the backend. A "close and reopen" scenario tests the window and client state, not a cold backend start; stopping the backend needs its own command.

## Metrics definitions

| Metric | Definition | How measured |
| --- | --- | --- |
| TTFT | Time to first token: send action to the first streamed text painted | Operator timing or event timestamps |
| Total reply time | Send action to the end of the stream (status back to idle) | Same |
| Input-to-paint latency | Keystroke in the composer to the character painted | Operator timing or `performance` marks |
| RSS and CPU | Resident memory and CPU share of the Electron process tree and of the harness process, sampled at idle and during a stream | `ps` samples |
| App open time | Launch command to a usable window (composer focusable) | Operator timing |
| App reopen time | Close to a usable window again, with the backend still running | Operator timing |
| Window bounds | Size and position before closing and after reopening | Window geometry readout |

## Round template

Copy this block for each round.

### Round N, YYYY-MM-DD

- **Slice**: 1 to 4 (see [plan.md](../../tests/operator/chat-campaign/plan.md))
- **Provider, model, effort**: 
- **Scenarios**: ids run, in order
- **Results**: passed / total (prompts spent / budget)

| Metric | Value | Note |
| --- | --- | --- |
| Median TTFT (s) | | |
| Median total reply time (s) | | |
| Input-to-paint latency (ms) | | |
| Electron RSS / CPU | | |
| Harness RSS / CPU | | |
| App open / reopen (s) | | |
| Window bounds before / after | | |

**Bugs**

- **C-NN, short title**
  - Steps:
  - Expected:
  - Actual:
  - Screenshot: `~/.cache/kho/chat/...`

**Notes**: 

## Pilot

### Round 0 (pilot), 2026-10-05

- **Slice**: pilot
- **Provider, model, effort**: Codex, `gpt-5.6-sol`, Medium
- **Scenarios**: new-chat-short, follow-up, project-facts (project `chat-facts`, Read only), reopen
- **Results**: 5 / 5 checks passed (3 prompts spent / 3). Replies were right: Everest; 8,848.86 m in the follow-up; the three seeded facts from `FACTS.md` in the project folder.

| Turn | TTFT (ms) | Total (ms) | Input-to-paint median / p95 (ms) | Electron RSS (MB) | Harness RSS (MB) | CPU % Electron / harness |
| --- | --- | --- | --- | --- | --- | --- |
| 1 new-chat-short | 44* | 5625 | 15.1 / 31.6 | 735 | 240 | 21.5 / 14.3 |
| 2 follow-up | 34* | 6575 | 20.8 / 31.2 | 756 | 234 | 23.4 / 4.9 |
| 3 project-facts | 630 | 11483 | 17.3 / 32.6 | 732 | 326 | 19.0 / 7.3 |

\* The TTFT probe fired on the first text of the assistant article (the run card), not on the first model token; slice 1 measures the reply body instead. Total times are valid.

- App open 872 ms; reopen 877 ms; window bounds before and after reopen identical (x 240, y 68, 1440 x 900); the last conversation was restored.
- `userData` checked inside the campaign HOME before any action.

**Bugs**: none confirmed.

**Notes**: possible visual nit to confirm in slice 1: the active project conversation row in the sidebar shows a grey block on its right edge that cuts the row (shot `~/.cache/kho/chat/runs/pilot/shots/turn-3.png`). Attach-mode limitation applies (closing the app leaves the backend running).

## Bugs index

| Id | Severity | Title | Status | Fix commit |
| --- | --- | --- | --- | --- |
| | | | | |

## Cross-round comparison

| Round | Provider | Median TTFT (s) | Median total (s) | RSS (MB) |
| --- | --- | --- | --- | --- |
| 0 pilot | Codex Sol Medium | n/a (probe fixed in slice 1) | 6.6 | Electron ~740, harness ~240-326 |
