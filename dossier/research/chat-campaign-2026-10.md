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
  - Decision (owner, 2026-10-05): keep the Codex default; it is already documented as an accepted limitation in dossier/conversation-execution-mode.md. The Read only rerun (round 4, S1-03r) refused the read, approval_policy never, no tool call.
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

### Round 4 (slice 2b), 2026-10-05

- **Slice**: 2, part b (S2-06 to S2-10) plus the S1-03 rerun on Read only (`s1-03r`)
- **Provider, model, effort**: Codex, `gpt-5.6-sol` Medium for every send, `gpt-5.6-luna` Medium for one send (S2-07)
- **Scenarios**: S1-03r, S2-06, S2-07, S2-08, S2-09, S2-10 (run visibly, once)
- **Results**: 3 / 6 scenarios passed as recorded (4 / 7 checks with the open step); 14 of 14 prompts spent (S1-03r 2, S2-06 2, S2-07 3, S2-08 2, S2-09 3, S2-10 2). No quota or rate-limit text seen. Two of the three failures come from the app (C-01 reproduced, new C-06), one from the model reading the image (see S2-06); in S2-09 two further checks were wrong in the run's own code (see Notes).
- **Environment**: desktop 0.16.0 (`f384035`), instance on 18640/18641.

| Turn | Model | TTFT (ms) | Total (ms) | Input-to-paint median (ms) | Electron RSS (MB) | Harness RSS (MB) |
| --- | --- | --- | --- | --- | --- | --- |
| 1 S1-03r Read only, by path | Sol Medium | 11628 | 13254 | 15.8 | 765 | 253 |
| 2 S1-03r keeper name | Sol Medium | 5595 | 5930 | 14.6 | 759 | 258 |
| 3 S2-06 PNG, what it shows | Sol Medium | 4628 | 6684 | 14.1 | 776 | 247 |
| 4 S2-06 background colour | Sol Medium | 3596 | 4968 | 17.0 | 772 | 256 |
| 5 S2-07 attach tally.py, HARBOR_FEE | Sol Medium | 8846 | 9613 | 17.4 | 777 | 252 |
| 6 S2-07 after switch to Luna | Luna Medium | 4610 | 5903 | 14.7 | 779 | 255 |
| 7 S2-07 back on Sol | Sol Medium | 3590 | 4385 | 16.0 | 782 | 254 |
| 8 S2-08 start and end markers | Sol Medium | 4511 | 18675 | 14.7 | 795 | 261 |
| 9 S2-08 was the end included | Sol Medium | 4861 | 6158 | 14.4 | 797 | 253 |
| S2-09 long answer + 2 follow-ups | Sol Medium | not measured | completed (3 runs) | n/a | n/a | n/a |
| 10 S2-10 three files, one line each | Sol Medium | 5460 | 10157 | 12.3 | 795 | 242 |
| 11 S2-10 which file has a bug | Sol Medium | 3602 | 4894 | 14.7 | 799 | 256 |

| Metric | Value | Note |
| --- | --- | --- |
| Median TTFT (s) | 4.6 | 11 single turns; range 3.6 to 11.6 (the first turn of the round was the slowest) |
| Median total reply time (s) | 6.2 | range 4.4 to 18.7 (S2-08 read the full file with a tool before answering) |
| Input-to-paint latency (ms) | 14.7 | median of per-turn medians; p95 stays 30 to 34 |
| Electron RSS | 759 to 799 MB | grows about 40 MB across the round |
| Harness RSS | 242 to 261 MB | flat |

**S1-03r (Read only, No project)**: the access label read "Read only" (asserted by the run; the helper now fails loudly if it does not). Both replies said "I cannot read that file with the available tools."; the code word and keeper name were not revealed. Codex rollout of the run (one file, only `turn_context` and tool-call names read): `approval_policy` `never`, sandbox `read-only`, tool calls none, so no `exec_command` ran. This confirms the C-02 note: Read only removes the shell. The scope-label check failed again on C-01 ("Choose project" instead of "No project"), the only failed check in this scenario.

Checks that passed:

- **S2-07**: the code file attached in turn 1 was used in turn 2 (after the switch to Luna, answer `settle_dock_fee`) and turn 3 (back on Sol, `420`); the footers and the server's run records read Sol, Luna, Sol; the composer chip was cleared after the send; no notices.
- **S2-08**: the "excerpt sent" chip appeared once on the 7213-character file (limit 6000, `EXCERPT_CHARS`) and stayed on the sent message. Sol quoted both markers: it said the displayed source was only an excerpt and read the full file with its shell, so the end was reachable (the same Ask-mode behaviour as C-02); the follow-up said the excerpt ended partway through ledger entry 105. The run passes because the answer admits the excerpt and quotes the end only after reading the file.
- **S2-10**: three chips (text, code, CSV); the "Remove attachment average.py" button removed one, a second pick re-added it (3 chips again); the reply cited the boat name, the mean function and the CSV; the sent message shows 3 attachments; the follow-up named `average.py` as the file with the bug.
- **S2-09 (parts)**: scrolled up during the stream, the view held its position (scrollTop unchanged within 3 px while the list grew from 4841 to 13055 px), the jump-to-latest button showed, and the status strip read `1 running · 2 queued`; the two follow-ups ran in order (`ECHO-ONE`, `ECHO-TWO`) and all three runs completed.

**Failures**

- **S2-06, not filed as a bug**: the chip showed an image preview and the answer was "red background with white, pixel-style text reading HOLM 23" (the image reads HOLM 73); the follow-up answered "Red". The check is strict (colour and the exact text), so it fails. The wrong digit looks like a model reading error on a pixel font; the run cannot tell whether the app scaled the image down. Screenshot: `~/.cache/kho/chat/runs/s2b/shots/s2-06-chip.png`.
- **S1-03r**: C-01 reproduced (see above).

**Bugs**

- C-06 (minor): auto-scroll detaches while a long answer of short lines streams and does not come back when the user scrolls to the bottom. Steps: S2-09, New Conversation, ask for the numbers 1 to 900 with a colour word each, watch the list without touching it; later scroll to the bottom with the wheel. Expected: the list follows the stream while it is at the bottom (plan S2-09: "auto-scroll resumes at the bottom"). Actual: after the first chunks the list stayed at `scrollTop` 131 while `scrollHeight` grew from 3248 to 4477 px (gap to the bottom 2411 to 3640 px, jump-to-latest button shown); after scrolling back to the bottom (gap 9 px) the list moved 282 px and then fell 1319 px behind within 1.8 s. Cause (read from `agent_service/ui.js` `scroll()`, not changed here): it only follows when the gap is under 250 px, and one render of a fast list adds more than that, so the stream escapes the threshold. Screenshot: `~/.cache/kho/chat/runs/s2b/shots/s2-09-scrolled-up.png`. Metrics: `~/.cache/kho/chat/runs/s2b/metrics.jsonl` (`s2-09` line).
- C-05 reproduced in S2-09 (header pill "Queued" while the first answer streams), see the same screenshot.

**Notes**: in S2-09 the run's own checks wrongly expected the run state `done` (the server says `completed`) and compared user messages cut at 30 characters (the word ONE/TWO fell outside); both are fixed in the script after the run and are not app failures. The area was not rerun (prompt budget), so the S2-09 line in this round stays failed on C-06. The picture seed is a generated 1000 x 300 PNG (red, white 5x7-pixel "HOLM 73"). Metrics: `~/.cache/kho/chat/runs/s2b/metrics.jsonl`. Screenshots: `~/.cache/kho/chat/runs/s2b/shots/`.

### Round 5 (slice 3a), 2026-10-05

- **Slice**: 3, part a, the DeepSeek block (S3-00, S3-01, S3-02, S3-03, S3-08, S3-09, S3-10)
- **Provider, model, effort**: DeepSeek `deepseek-flash` (the DeepSeek V4.1 Flash chat model, effort `configured`) and Codex `gpt-5.6-sol` Medium
- **Scenarios**: S3-00, S3-01, S3-02, S3-03, S3-08, S3-09, S3-10 (run visibly, once)
- **Results**: 8 / 8 checks passed (open step plus 7 scenarios); 18 of 18 prompts spent: DeepSeek 12 (S3-01 2, S3-02 2, S3-03 1, S3-08 2, S3-09 2, S3-10 3), Codex 6 (S3-02 4, S3-03 2). No quota, credit, balance or rate-limit text seen. No new bug.
- **Environment**: desktop 0.16.0, instance on 18640/18641. S3-00 changed: the key was already in the instance state (placed by the owner), so the instance provisioning enables DeepSeek (`deepseek-flash`, projects `sem-projeto` and `chat-facts`) after the admin's own provider check succeeds; the run asserted the admin reports DeepSeek authenticated with credentials, and the model picker offers it. The key was never read or shown.

| Turn | Provider / model | TTFT (ms) | Total (ms) | Input-to-paint median (ms) | Electron RSS (MB) | Harness RSS (MB) |
| --- | --- | --- | --- | --- | --- | --- |
| 1 S3-01 capital | DeepSeek Flash | 2578 | 2618 | 13.9 | 765 | 264 |
| 2 S3-01 follow-up | DeepSeek Flash | 1884 | 1935 | 15.2 | 768 | 271 |
| 3 S3-02 plant HERON | Sol Medium | 3597 | 5225 | 17.2 | 761 | 243 |
| 4 S3-02 second word | Sol Medium | 4607 | 5819 | 18.2 | 763 | 261 |
| 5 S3-02 recall on DeepSeek | DeepSeek Flash | 2617 | 2676 | 18.4 | 759 | 269 |
| 6 S3-02 third word on DeepSeek | DeepSeek Flash | 2041 | 2214 | 19.5 | 760 | 273 |
| 7 S3-02 list on Codex again | Sol Medium | 6636 | 8582 | 15.6 | 761 | 258 |
| 8 S3-02 first question | Sol Medium | 7878 | 9452 | 16.4 | 762 | 258 |
| 9 S3-03 plant OTTER | DeepSeek Flash | 2596 | 2637 | 18.8 | 771 | 268 |
| 10 S3-03 recall on Codex | Sol Medium | 4866 | 6437 | 16.8 | 771 | 251 |
| 11 S3-03 list both | Sol Medium | 4617 | 7014 | 15.1 | 775 | 246 |
| S3-08 numbers 1 to 600, Stop | DeepSeek Flash | not measured | Stop halted the text in 193 ms | n/a | n/a | n/a |
| 12 S3-08 NEXT-OK | DeepSeek Flash | 2792 | 2851 | 14.8 | 800 | 240 |
| 13 S3-09 Markdown + table | DeepSeek Flash | 3166 | 3967 | 11.4 | 800 | 241 |
| 14 S3-09 two code blocks | DeepSeek Flash | 2405 | 2493 | 14.5 | 803 | 243 |
| 15 S3-10 harbor | DeepSeek Flash | 2604 | 6331 | 13.9 | 803 | 301 |
| 16 S3-10 lanterns | DeepSeek Flash | 2666 | 2712 | 12.8 | 802 | 303 |
| 17 S3-10 door code | DeepSeek Flash | 2880 | 2918 | 15.8 | 804 | 302 |

- **DeepSeek vs Codex**: median TTFT 2.6 s (11 turns) against 4.7 s (6 turns); median total 2.7 s against 6.7 s. DeepSeek answers in about 2 to 3 s whatever the follow-up; Codex Sol Medium grew to 8 to 9 s on the recall turns. Resources are the same on both (Electron 759-804 MB, harness 240-303 MB).
- **S3-01**: both replies streamed and finished without error; the reply footer shows the model and the time ("DeepSeek V4.1 Flash · 2.0 s") but no usage or balance figure anywhere in the chat; there is nothing to check beyond "no error". The history shows 2 user and 2 assistant messages.
- **S3-02 / S3-03 provider switch**: the picker allows switching provider inside an open conversation, in both directions, with no lock message and no 409 (`conversation_execution_mode_locked` only rejects an explicit `execution_mode` on a continuation; the model is free to change). History is carried: DeepSeek named both Codex-planted words (HERON-5521, LARK-8830) and added PLOVER-1204; Codex, two switches later, listed HERON-5521, LARK-8830, PLOVER-1204 in order and recalled the very first request; in S3-03 Codex named OTTER-3302 planted on DeepSeek and then both words. The server holds the six runs of S3-02 as `gpt-5.6-sol`, `gpt-5.6-sol`, `deepseek-flash`, `deepseek-flash`, `gpt-5.6-sol`, `gpt-5.6-sol`, all `completed`, and each footer names the model that answered. The first reply after a switch shows the milestone "Preparing the run" instead of "Conversation context resumed", so the new provider gets a fresh session with the history passed in. The mode stayed "Native conversation" throughout (no silent mode change); the isolated mode was not tried. `dossier/conversation-execution-mode.md` only forbids changing the execution mode of an existing conversation, which agrees; it does not say anything about switching the model or provider, so this behavior is undocumented there.
- **S3-08 Stop on DeepSeek**: the text stopped changing 193 ms after Stop (pill "Cancelled" after 269 ms); 450 characters at Stop, 679 kept (the last chunk arrived after the click), no ghost stream, the Stop button hidden, and the next send answered NEXT-OK in 2.9 s. Screenshot `~/.cache/kho/chat/runs/s3a/shots/s3-08-stopped.png`.
- **S3-09**: headings, lists, the 3 by 3 table and its fit, two code blocks in two languages with 4-space indentation, and both copy buttons (clipboard equal to the block) pass on DeepSeek, same as on Codex.
- **S3-10**: DeepSeek in the project read FACTS.md with tool steps (first answer "I'll read the file now", the three facts quoted exactly: Port Quillon, 7 brass lanterns, ZEBRA-4471). The access picker took "Read only" without error.

**Notes**: no new defect was found, so the Bugs index is unchanged (C-01, C-03 to C-06 are known and were not re-filed). Metrics: `~/.cache/kho/chat/runs/s3a/metrics.jsonl`. Screenshots: `~/.cache/kho/chat/runs/s3a/shots/` (`s3-00-models.png` shows the model picker with the DeepSeek group and no key; the Settings > Providers screen was not captured).

### Round 6 (slice 3b), 2026-10-05

- **Slice**: 3, part b, reload, close and reopen, harness restart (S3-04 to S3-07)
- **Provider, model, effort**: Codex `gpt-5.6-sol`, Medium
- **Scenarios**: S3-04, S3-05, S3-06, S3-07 (run visibly)
- **Results**: S3-04 PASS, S3-05 PASS, S3-06 FAIL (one check: scroll position, see C-07), S3-07 PASS; 12 of 12 prompts spent (S3-04 3, S3-05 4, S3-06 2, S3-07 3). No quota, credit or rate-limit text from the provider. One new bug: C-07.
- **Environment**: desktop 0.16.0, instance on 18640/18641. The slice ran in two passes because the first one polled `/v1/conversations/:id` in a tight loop and the harness answered HTTP 429 to the operator (not a provider limit): S3-04 crashed on that answer after its first prompt (that prompt's result was lost, so S3-04 was rerun with 2 prompts and its "short follow-up after reload" prompt dropped; the early-reload prompt covers "send works after a reload"), S3-05 ran round a (2 prompts) and aborted on a check race, S3-07 passed in full. The second pass ran S3-04 (2), S3-05 round b (2, `CHAT_S305_ROUNDS=b`) and S3-06 (2) with a 2 s poll. Evidence of both passes: `~/.cache/kho/chat/runs/s3b/` (first pass copied to `run1/`).

| Turn | Scenario | TTFT (ms) | Total (ms) | Input-to-paint median (ms) | Electron RSS (MB) | Harness RSS (MB) |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | S3-05 round a, send after reopen | 10595 | 11902 | 13.2 | 832 | 274 |
| 2 | S3-07 plant KESTREL | 5442 | 7271 | 12.6 | 821 | 250 |
| 3 | S3-07 recall after restart | 8619 | 9930 | 19.0 | 813 | 259 |
| 4 | S3-07 second word | 3605 | 5421 | 14.6 | 815 | 253 |
| 5 | S3-05 round b, send after reopen | 5615 | 6429 | 14.8 | 828 | 251 |
| 6 | S3-06 prompt, conversation A | 7617 | 8937 | 19.2 | 829 | 274 |
| 7 | S3-06 prompt, conversation B | 6612 | 7936 | 18.2 | 832 | 264 |

The three long numbered replies and the early-reload reply are not in the table (they were not timed as turns).

| Metric | Value | Note |
| --- | --- | --- |
| Median TTFT (s) | 6.6 | 7 short turns; range 3.6 to 10.6 |
| Median total reply time (s) | 7.9 | same 7 turns |
| Input-to-paint latency (ms) | 14.8 median | 12.6 to 19.2 |
| Electron RSS / CPU | 813-832 MB, 23-46% | CPU is the average over the turn |
| Harness RSS / CPU | 250-274 MB, 3-5% | |
| App open / reopen (s) | open 1.2; reopen 1.0 (S3-05 a, b), 1.3, 1.1, 1.1 (S3-06) | reopen = relaunch to a usable window, backend already running |
| Window bounds before / after | x 240, y 68, 1440x900 both before and after, on all 5 reopens | restored exactly |

- **S3-04 reload mid-reply**: PASS. The page reloaded in 521 ms with the stream live (135 characters at the reload, pill "running"); the same conversation came back with its 2 messages, the stream continued to the end (3371 characters in the page and on the server, last line `300 nutshell`, run `completed`), and the server still held exactly 1 turn (no duplicate). Early reload (right after pressing send, before any text): exactly one more turn, run `completed`, answer `EARLY-OK`. Screenshot `~/.cache/kho/chat/runs/s3b/shots/s3-04-reload-a.png`.
- **S3-05 close mid-reply**: PASS in both rounds. The app was closed with 129 and 139 characters streamed; while it was closed, the admin and the harness ports stayed open (attach mode); after the reopen (about 1.0 s) the same conversation was selected with its messages, the pill read "running" and the stream finished: 2186 characters (round a, last line `200 snowflake`) and 2863 characters (round b, last line `250 ...`), page equal to server, 1 turn each, `completed`. Window bounds identical before and after. A short prompt after each reopen answered (REOPEN-A, REOPEN-B). Note: in round a the stock wait helper saw the header pill "Completed" while the new reply still showed "Working..." and the partial text `REOPEN-` (the check then failed on a race); the same turn ended `Completed` with `REOPEN-A` a moment later and round b did not repeat it. Treated as a helper race, not filed.
- **S3-06 idle close and reopen x3**: the three reopens took 1.34, 1.07 and 1.07 s; window bounds identical; the selected conversation (the 300-line one) was restored each time. FAIL on one check: the scroll position was not restored. The conversation was scrolled to the middle (3323 of 6547 px) before closing; after every reopen it sat at the top (0 of 6547), see C-07. Send worked in both conversations after the third reopen (ALPHA-OK in the first, BETA-OK in a second one picked from the sidebar). Screenshot `~/.cache/kho/chat/runs/s3b/shots/s3-06-last-open.png`.
- **S3-07 harness restart from the admin**: PASS. `POST /api/stop` then `/api/start` on the admin: the harness came back with a new pid (1769112 to 1776872) in 1.9 s; the open app reconnected on its own (no reload needed), with the same conversation and the same 1 turn. The follow-up recalled `KESTREL-6613` and the next turn listed both words (`KESTREL-6613`, `SWIFT-2047`); the second run shows the milestones "Conversation context resumed | Preparing the run", so the Codex session resumed; the server held 3 completed turns. No error message appeared. Screenshot `~/.cache/kho/chat/runs/s3b/shots/s3-07-restarted.png`.

**Bugs**

- **C-07, scroll position is not restored on reopen (opens at the top)**
  - Steps: open a long conversation (300 numbered lines, 6547 px of content), scroll to the middle (3323 px), close the app, open it again. Repeat three times.
  - Expected: the same conversation is selected and its scroll position is kept (or it opens at the newest message).
  - Actual: the conversation is selected but the view sits at the very top (scrollTop 0) every time, far from the newest message.
  - Screenshot: `~/.cache/kho/chat/runs/s3b/shots/s3-06-last-open.png`; numbers in `~/.cache/kho/chat/runs/s3b/metrics.jsonl` (`s3-06-start`, `s3-06-open`).

**Notes**: C-01, C-03 to C-06 were not re-filed. Operator lesson: poll `/v1/conversations/:id` no faster than every 2 s; the harness rate-limits tight polling with 429.

## Bugs index

| Id | Severity | Title | Status | Fix commit |
| --- | --- | --- | --- | --- |
| C-01 | nit | New-chat project picker reads "Choose project", not "No project" | open | |
| C-02 | major (needs decision) | Ask mode with shell reads any file without an approval card (Codex default) | closed: owner kept the Codex default (2026-10-05); already documented in dossier/conversation-execution-mode.md (Ask row, accepted limitation); S1-03 rerun in Read only refused the read with no shell call | |
| C-03 | minor | Run steps show "Ran tool" without the file read | open | |
| C-04 | nit | Grey block cuts the right edge of the active sidebar row | open (reproduced in round 2) | |
| C-05 | nit | Header pill reads "Queued" for the live run while follow-ups are queued | open (reproduced in round 4) | |
| C-06 | minor | Auto-scroll detaches during a fast long stream and does not resume at the bottom | open | |
| C-07 | minor | Scroll position is not restored on reopen: the selected conversation opens at the top | open | |

## Cross-round comparison

| Round | Provider | Median TTFT (s) | Median total (s) | RSS (MB) |
| --- | --- | --- | --- | --- |
| 0 pilot | Codex Sol Medium | n/a (probe fixed in slice 1) | 6.6 | Electron ~740, harness ~240-326 |
| 1a | Codex Sol Medium | 6.8 | 9.1 | Electron ~763, harness ~325 |
| 1b | Codex Sol Medium | 5.6 | 5.8 | Electron ~769, harness ~276 |
| 2a | Codex Sol Medium + Luna | 5.1 | 5.9 | Electron ~740-760 (~790 with two streams), harness ~250-305 (~480 with two streams) |
| 2b | Codex Sol Medium + Luna | 4.6 | 6.2 | Electron ~760-800, harness ~242-261 |
| 3a | DeepSeek Flash (12 turns) vs Codex Sol Medium (6 turns) | 2.6 vs 4.7 | 2.7 vs 6.7 | Electron ~759-804, harness ~240-303 |
| 3b | Codex Sol Medium (7 short turns) | 6.6 | 7.9 | Electron ~813-832, harness ~250-274 |
