# Chat campaign, October 2026

Status: pilot and slice 1a done (2026-10-05); slices 1b to 4 pending. Diataxis kind: explanation (campaign log and findings). The scenario catalog lives in `tests/operator/chat-campaign/plan.md`; fixes are listed in `CHANGELOG.md` under Unreleased.

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

### Round 1 (slice 1a), 2026-10-05

- **Slice**: 1, part a (S1-01 to S1-05)
- **Provider, model, effort**: Codex, `gpt-5.6-sol`, Medium
- **Scenarios**: S1-01, S1-02, S1-03, S1-04, S1-05 (run visibly, once)
- **Results**: 3 / 6 checks passed as recorded by the run (17 / 17 prompts spent). One failure is a bug in the run's own table check (selector counted header and first body row, 6 columns instead of 3), fixed in the script afterwards; the rendered table was correct, so S1-02 counts as passed on the data. Real failures: S1-03 (C-01, C-02) and S1-04 (C-03). All 6 fact answers in S1-04 and both in-folder answers in S1-05 were right; the outside file was refused without an approval card.

| Turn | TTFT (ms) | Total (ms) | Input-to-paint median (ms) | Electron RSS (MB) | Harness RSS (MB) |
| --- | --- | --- | --- | --- | --- |
| 1 S1-01 question | 3849 | 4889 | 17.3 | 739 | 210 |
| 2 S1-01 follow-up | 4845 | 6367 | 18.5 | 762 | 227 |
| 3 S1-02 Markdown | 5498 | 7541 | 17.3 | 763 | 223 |
| 4 S1-02 two languages | 3840 | 6646 | 14.7 | 757 | 231 |
| 5 S1-02 120-line listing | 6846 | 20835 | 14.4 | 770 | 279 |
| 6 S1-02 wide table | 4597 | 9946 | 15.9 | 776 | 266 |
| 7 S1-03 No project, by path | 7595 | 13638 | 11.6 | 779 | 300 |
| 8 S1-03 second ask | 4593 | 6121 | 15.0 | 783 | 252 |
| 9 S1-04 code word | 14608 | 15891 | 13.6 | 790 | 363 |
| 10 S1-04 CSV total | 7610 | 8642 | 18.8 | 755 | 331 |
| 11 S1-04 function name | 12101 | 15648 | 18.3 | 758 | 363 |
| 12 S1-04 line count | 6098 | 7028 | 17.5 | 754 | 326 |
| 13 S1-04 top item | 5866 | 7151 | 15.8 | 758 | 331 |
| 14 S1-04 keeper | 10866 | 12146 | 15.9 | 761 | 374 |
| 15 S1-05 main folder | 17412 | 17459 | 13.9 | 772 | 325 |
| 16 S1-05 second folder | 9096 | 9134 | 19.3 | 773 | 349 |
| 17 S1-05 outside file | 8361 | 9660 | 17.9 | 778 | 347 |

TTFT is now the first non-empty text of the reply body (`article.assistant > .text`), not the run card. Short replies appear in one piece about 1 s before the run completes; the 120-line listing streamed for 14 s between first text and completion.

| Metric | Value | Note |
| --- | --- | --- |
| Median TTFT (s) | 6.8 | range 3.8 to 17.4; grows when the run reads files |
| Median total reply time (s) | 9.1 | range 4.9 to 20.8 |
| Input-to-paint latency (ms) | 15.9 | median of per-turn medians; p95 stays near 31 to 33 |
| Electron RSS / CPU | 763 MB / 21 % | range 739 to 790 MB |
| Harness RSS / CPU | 325 MB / 5 % | range 210 to 374 MB, rises with project turns |
| App open / reopen (s) | 0.9 / not run | reopen is S1-10 |
| Window bounds before / after | not run | S1-10 |

Checks that passed: reply and follow-up in the same history (2 user and 2 assistant messages); Markdown headings, lists and table render and fit; code blocks keep indentation; the 120-line listing scrolls inside its block and is not clipped; the Copy buttons put exactly the block text on the clipboard (3 of 3, read through Electron); the wide table scrolls inside its own region; project creation and adding a second folder worked through the UI.

**Bugs**

- **C-01, the new-chat project picker says "Choose project" instead of "No project"**
  - Steps: New Conversation; read the project picker (`#project-button-label`) before choosing anything.
  - Expected: the scope label shows "No project" (plan S1-03); the conversation rows already use that wording.
  - Actual: the label reads "Choose project" while the value is `sem-projeto`.
  - Screenshot: `~/.cache/kho/chat/runs/s1a/shots/turn-7.png`
- **C-02, an Ask-mode chat reads any file the OS user can read, with no approval card (needs a decision)**
  - Steps: New Conversation (No project), access left at Ask for approval (the run meant to pick Read only but the selection did not apply; the screenshot shows "Ask for approval"), ask to read `<HOME>/campaign-main/facts/alpha.txt` by absolute path.
  - Expected (plan S1-03): the chat cannot read it and says so.
  - Actual: the model ran `sed -n` on the path through Codex's native shell and quoted the file (`QUILLFEATHER-3917`, `Ondra Welk`); no approval card.
  - Root cause (Codex rollout: cli 0.157.1, approval_policy on-request, sandbox read-only): Ask grants `shell`, and Codex's read-only sandbox lets in-sandbox commands read anywhere; on-request only asks for commands that need to leave the sandbox. This is the Codex CLI default. Read only is not affected: with `shell` off the harness passes `-c features.shell_tool=false -c features.unified_exec=false` (adapters/codex/native.py `build_command`), and a fake-server probe on 0.157.1 confirmed the shell tool is gone (`unsupported call: exec_command`).
  - Options for the owner: (a) Ask/Auto also drop the native shell and read only through the harness reader; (b) Ask requires approval for every shell command (unverified on 0.157.1); (c) keep the Codex default and document that a granted shell can read the whole disk.
  - Campaign fix: the area must assert the access label after choosing Read only; S1-03 reruns with Read only.
  - Screenshot: `~/.cache/kho/chat/runs/s1a/shots/turn-7.png`
- **C-03, run steps show "Ran tool" without which file was read**
  - Steps: S1-04, any fact question; read the run steps of the reply.
  - Expected: file reads are shown (plan S1-04).
  - Actual: milestones read "Queued, Running, Preparing the run, Thinking, Ran tool, Completed"; no file name or command in the inline steps. The full run view (View run) was not opened.
  - Screenshot: `~/.cache/kho/chat/runs/s1a/shots/turn-9.png`
- **C-04, grey block cuts the right edge of the active conversation row**
  - Steps: open any conversation so its sidebar row is active (project or Chats list).
  - Expected: the row ends cleanly.
  - Actual: reproduces, in the project list and in the Chats list. A grey rounded block fills the right end of the active row (about 40 px); the row's `span.project-actions` / trailing button sits there, so the block is most likely that button's area painted without hover.
  - Screenshot: `~/.cache/kho/chat/runs/s1a/shots/sidebar-project-active.png`

**Notes**: UI project creation works, but projects created in the UI do not appear in the admin settings (`projects`, Codex `projects` stay `chat-facts` only) and chats in them still work. A throwaway dry-run project was created and removed from the list before the run, so the sidebar shows "Removed projects (1)". S1-05 used a new conversation after adding the folder. The send button turns disabled while running and has no Stop state. The TTFT probe and seeds live in the area script and `instance.cjs` (folders `campaign-main`, `campaign-annex` under the harness HOME, outside file under `projects/outside/`).

### Round 2 (slice 1b), 2026-10-05

- **Slice**: 1, part b (S1-06 to S1-10)
- **Provider, model, effort**: Codex, `gpt-5.6-sol`, Medium
- **Scenarios**: S1-06, S1-07, S1-08, S1-09, S1-10 (run visibly, once)
- **Results**: 5 / 6 checks passed as recorded by the run (13 / 13 prompts spent). The one failure is a bug in the run's own S1-06 check: it counted `#files-tree` items, but that tree is the server-wide "Browse authorized server folders" browser (Local Folders: `campaign-annex`, `campaign-main`), not the project's tree. The Files panel of the folder-less project correctly reads "No project root is authorized." The check was corrected afterwards to look for that notice; no prompt was resent. S1-06 counts as passed on the evidence in `shots/s1-06-code-view.png`.
- **Environment**: desktop 0.16.0 (`f384035`), instance on 18640/18641, no quota or rate-limit text seen.

| Turn | TTFT (ms) | Total (ms) | Input-to-paint median (ms) | Electron RSS (MB) | Harness RSS (MB) |
| --- | --- | --- | --- | --- | --- |
| 1 S1-06 general question | 4482 | 5782 | 13.3 | 764 | 250 |
| 2 S1-06 file request | 7595 | 12390 | 19.5 | 760 | 276 |
| 3 S1-07 summary | 3861 | 5654 | 11.4 | 766 | 301 |
| 4 S1-07 bug review | 4598 | 6145 | 15.2 | 769 | 311 |
| 5 S1-07 departure time | 5599 | 5720 | 18.9 | 772 | 313 |
| 6 S1-08 before edit | 7078 | 12883 | 15.2 | 781 | 312 |
| 7 S1-08 after edit | 10096 | 10224 | 13.8 | 782 | 352 |
| 8 S1-08 quote | 10118 | 11402 | 23.2 | 764 | 351 |
| 9 S1-09 Chat | 4845 | 4887 | 12.0 | 769 | 248 |
| 10 S1-09 Code view | 5595 | 5877 | 20.2 | 774 | 262 |
| 11 S1-09 back in Chat | 3596 | 5135 | 23.6 | 774 | 252 |
| 12 S1-10 baseline a | 4080 | 5641 | 15.4 | 759 | 250 |
| 13 S1-10 baseline b | 5596 | 5714 | 17.6 | 762 | 257 |

| Metric | Value | Note |
| --- | --- | --- |
| Median TTFT (s) | 5.6 | range 3.6 to 10.1; the two file-reading turns after the edit (S1-08) were the slowest |
| Median total reply time (s) | 5.8 | range 4.9 to 12.9 |
| Input-to-paint latency (ms) | 15.4 | median of per-turn medians; p95 stays near 31 to 32 |
| Electron RSS / CPU | 769 MB / 23 % | range 759 to 782 MB |
| Harness RSS / CPU | 276 MB / 4 % | range 248 to 352 MB |
| App open (s) | 1.07 | first open |
| Reopen (s) | 1.14, 1.01, 1.07 | three close and open cycles |
| Window bounds before / after | identical, 3 of 3 | `x 240, y 68, 1440 x 900` each time |
| Baseline TTFT / total, two short prompts | 4.8 s / 5.7 s | S1-10 fresh conversation (medians of 2) |

Checks that passed: a project created in the UI without a folder works and answers a general question; asked to read `facts/alpha.txt` it said it cannot (no filesystem tool in that session) without a crash, and its Files panel states that no project root is authorized; both attachment chips (`harbor-memo.txt`, `average.py`) appeared in the composer and in the sent message, the summary cited the boat name `Marlin Dusk`, the review named `len(values) + 1`, and a later turn still answered `06:40` from the attachment; after editing `facts/gamma.txt` on disk the next ask returned `BEACON-8843` and the exact quote contained no trace of `BEACON-2210`; Chat, Code and Chat again kept one conversation (2 user and 2 assistant messages in Code and after returning, 3 and 3 after the third turn, no duplicates, same title); the conversation was restored on all three reopens; the window kept its bounds.

**Bugs**

- No new bug. C-04 (grey block on the active row) reproduces: the active row of the `Campaign edits` project conversation shows the grey rounded block on its right end. Screenshot: `~/.cache/kho/chat/runs/s1b/shots/sidebar-project-active.png`.

**Notes**: the S1-06 Code view lists the server-wide folders (`campaign-main`, `campaign-annex`) under "Browse authorized server folders" even for a project without a folder; the label says authorized server folders, so it is treated as designed and not filed. The prompts of S1-07 attach files from `~/.cache/kho/chat/projects/attach/`, outside every project folder, so the chat could only know them through the attachment. S1-09 used the same composer in Code view. C-02 was not exercised on purpose.

### Round 3 (slice 2a), 2026-10-05

- **Slice**: 2, part a (S2-01 to S2-05)
- **Provider, model, effort**: Codex, `gpt-5.6-sol` Medium and `gpt-5.6-luna` (Medium, Low for one send in S2-02)
- **Scenarios**: S2-01, S2-02, S2-03, S2-04, S2-05 (run visibly, once)
- **Results**: 5 / 5 scenarios passed (6 / 6 checks with the open step); 16 of 18 prompts spent (S2-01 4, S2-02 3, S2-03 2, S2-04 3, S2-05 4). The plan lists 3 and 4 prompts for S2-03 and S2-04; two were enough.
- **Environment**: desktop 0.16.0 (`f384035`), instance on 18640/18641, no quota or rate-limit text seen.

| Turn | Model | TTFT (ms) | Total (ms) | Input-to-paint median (ms) | Electron RSS (MB) | Harness RSS (MB) |
| --- | --- | --- | --- | --- | --- | --- |
| 1 S2-01 Sol, name itself | Sol Medium | 5599 | 19975 | 13.0 | 761 | 305 |
| 2 S2-01 Luna | Luna Medium | 9845 | 11400 | 15.5 | 738 | 248 |
| 3 S2-01 Sol again | Sol Medium | 3597 | 4642 | 18.2 | 740 | 253 |
| 4 S2-01 backwards word | Sol Medium | 7853 | 9402 | 17.7 | 744 | 253 |
| 5 S2-02 Luna Low | Luna Low | 5600 | 5647 | 14.1 | 746 | 256 |
| 6 S2-02 Luna restored | Luna Medium | 4596 | 4900 | 16.9 | 750 | 259 |
| 7 S2-02 Sol | Sol Medium | 4600 | 6152 | 17.9 | 756 | 256 |
| S2-03 long answer, Stop | Sol Medium | not measured | stopped | n/a | 761 | 269 |
| 8 S2-03 next send | Sol Medium | 3592 | 3890 | 17.8 | 761 | 252 |
| S2-04 long answer + 2 follow-ups | Sol Medium | not measured | stopped / discarded / completed | n/a | n/a | n/a |
| 9 S2-05 round 1, two chats streaming | Sol Medium | n/a | 31239 (both) | 24.5 (p95 31.1, 71 keys) | 785 | 480 |
| 10 S2-05 round 2, two chats streaming | Sol Medium | n/a | 23402 (both) | 22.1 (p95 31.4, 146 keys) | 797 | 448 |

| Metric | Value | Note |
| --- | --- | --- |
| Median TTFT (s) | 5.1 | single streams, 8 turns; range 3.6 to 9.8 (Luna after a model switch was the slowest) |
| Median total reply time (s) | 5.9 | range 3.9 to 20.0 (the first turn also ran a docs lookup) |
| Input-to-paint latency, single stream (ms) | 17.3 | median of per-turn medians; p95 stays near 31 to 33 |
| Input-to-paint latency, two streams (ms) | 24.5 and 22.1 | typing while another conversation streams adds about 5 to 7 ms; p95 unchanged |
| Electron RSS single / two streams | 738 to 761 / 785 to 797 MB | +30 to +50 MB with two live streams |
| Harness RSS single / two streams | 248 to 305 / 448 to 480 MB | +190 to +220 MB with two live runs |
| Stop to stream halt | 141 ms | polled every 40 ms; header pill read Cancelled after 230 ms |

Checks that passed:

- **S2-01**: the reply footer showed GPT-5.6 Sol, Luna, Sol, Sol; the picker and the server's run records matched; every run was Medium; the code word survived both switches (history carried over). Self-reports were not model names ("Codex, based on GPT-5" in all three), so the footer is the authoritative check, as planned.
- **S2-02**: Luna's effort before the change was Medium; the Low send was recorded as `low`, the restored send as `medium`, and a following Sol send as `medium`: no stale effort.
- **S2-03**: Stop at 430 characters; text stopped growing within 141 ms and stayed at 457 characters over 2.5 s (the late characters were already in flight); the partial text is kept; header pill and sidebar read Cancelled, the composer note reads "Run cancelled"; no ghost stream (strip `0 running`); the next send answered.
- **S2-04**: both follow-ups showed as `1 running · 2 queued` in the status strip while the first answer streamed. The UI has no per-follow-up cancel while the first run is live; Stop on the live run holds the next follow-up with "Run queued message" and "Discard" buttons. Discarding the first follow-up ended it as cancelled with no reply; the second then ran and answered `CHARLIE-QUEUE`; final strip `0 running · 0 queued`.
- **S2-05**: both conversations streamed at once (`2 running`), four tab switches kept each stream live and none showed text of the other; both completed in round 1 and round 2 with 2 user and 2 assistant messages each and the complete number ranges.

**Bugs**

- C-05 (nit): while the first answer streams and follow-ups are queued, the conversation header pill reads "Queued" and the status line reads "Receiving response…" for the live run; only the bottom strip counts `1 running · 2 queued`. Steps: S2-04, start a long answer, send two follow-ups. Expected: the header shows Running (and the queued follow-ups are named as queued); actual: "Queued". Screenshot: `~/.cache/kho/chat/runs/s2a/shots/s2-04-queued.png`.

**Notes**: the plan's "cancel one queued follow-up" can only be done through Stop then Discard, since the UI offers no cancel for a queued follow-up while the earlier run is live; this is recorded as design, not as a bug. The Stop prompt of S2-03 and the long prompts of S2-04 and S2-05 have no TTFT in the table (Stop and queue turns do not go through the single-turn probe). Metrics: `~/.cache/kho/chat/runs/s2a/metrics.jsonl`. Screenshots: `~/.cache/kho/chat/runs/s2a/shots/`.

## Bugs index

| Id | Severity | Title | Status | Fix commit |
| --- | --- | --- | --- | --- |
| C-01 | nit | New-chat project picker reads "Choose project", not "No project" | open | |
| C-02 | major (needs decision) | Ask mode with shell reads any file without an approval card (Codex default) | open, owner decision | |
| C-03 | minor | Run steps show "Ran tool" without the file read | open | |
| C-04 | nit | Grey block cuts the right edge of the active sidebar row | open (reproduced in round 2) | |
| C-05 | nit | Header pill reads "Queued" for the live run while follow-ups are queued | open | |

## Cross-round comparison

| Round | Provider | Median TTFT (s) | Median total (s) | RSS (MB) |
| --- | --- | --- | --- | --- |
| 0 pilot | Codex Sol Medium | n/a (probe fixed in slice 1) | 6.6 | Electron ~740, harness ~240-326 |
| 1a | Codex Sol Medium | 6.8 | 9.1 | Electron ~763, harness ~325 |
| 1b | Codex Sol Medium | 5.6 | 5.8 | Electron ~769, harness ~276 |
| 2a | Codex Sol Medium + Luna | 5.1 | 5.9 | Electron ~740-760 (~790 with two streams), harness ~250-305 (~480 with two streams) |
