# Chat campaign plan: real providers, visible runs

Scenario catalog for the October 2026 chat campaign, about 120 real provider turns in four slices of 30. Results, timings and bugs go to `dossier/research/chat-campaign-2026-10.md`. Diataxis kind: how-to plus reference.

## Ground rules

- Real providers only; fixtures never stand in. Instance: ports 18640 (admin) and 18641 (harness), state under `~/.cache/kho/chat`, desktop package 0.16.0 from `f384035`.
- Providers: Codex `gpt-5.6-sol` at medium effort (default), `gpt-5.6-luna` where a scenario says so, DeepSeek in slice 3 and where noted.
- Keys: the owner types the DeepSeek key in Settings > Providers of the test instance. Nobody else types keys; no key is written to any file, log or screenshot.
- Runs are visible: the operator drives the app on the owner's screen and saves screenshots under `~/.cache/kho/chat/`.
- Seeded content: before slice 1, create a scratch folder with small files of known content (for example `facts/alpha.txt` with a made-up code word, `facts/budget.csv`, `src/sample.py`, a PNG with a known color and text, and a long text file over the excerpt limit). Facts are invented, so a correct answer proves the file was read.
- Stop rule: any quota, credit or rate-limit error from any provider stops the campaign at once. Record it, notify the owner and do not continue another way.
- Prompt budget: at most 30 prompts per slice; a prompt is one real turn sent to a provider. Re-sends after a failure count.
- Bugs are recorded as `C-NN` in the research log, with steps, expected, actual and a screenshot path.

## How to run

0. Package the desktop from a clean `main` (`scripts/package-desktop-linux.sh`), copy the output to `~/.cache/kho/chat/app` and turn on only the `EnableNodeCliInspectArguments` fuse in the copy (`flipFuses` from `desktop/node_modules/@electron/fuses`), so Playwright can attach. The production package keeps that fuse off. Override the path with `CHAT_APP`.
1. Start the isolated instance (admin 18641, harness 18640, state under `~/.cache/kho/chat`, override with `CHAT_ROOT`): `NODE_PATH=<playwright node_modules> node tests/operator/chat-campaign/instance.cjs start` (`status` and `stop` work the same way).
2. Run a slice visibly: `env -u WAYLAND_DISPLAY DISPLAY=:0 CHAT_SLICE=<pilot|s1|s2|s3|s4> CHAT_BUDGET=<prompts> NODE_PATH=<playwright node_modules> PLAYWRIGHT_MODULE=<playwright node_modules>/playwright node tests/operator/areas/20-chat-real-providers.cjs`. Outputs go to `~/.cache/kho/chat/runs/<slice>/` (`metrics.jsonl`, `summary.md`, `shots/`).
3. After each slice, fill a round block in `dossier/research/chat-campaign-2026-10.md` and update its Bugs index.

## Totals

| Slice | Focus | Scenarios | Prompts |
| --- | --- | --- | --- |
| 1 | Codex basics, projects and folders | 10 | 30 |
| 2 | Model and effort switches, queue, Stop, parallel chats, attachments | 10 | 30 |
| 3 | DeepSeek round-trip, reload, close and reopen, window behavior | 10 (plus 1 pause step) | 30 |
| 4 | Long conversation, errors, keyboard, sizes, themes, rename, archive, delete | 8 | 30 |
| Total | | 38 | 120 |

## Slice 1: Codex basics, projects and folders (Sol medium)

| Id | Steps | Checks | Prompts |
| --- | --- | --- | --- |
| S1-01 | New conversation, send a short question, then a follow-up | Reply streams; send button state changes; history shows both turns; record TTFT and total time | 2 |
| S1-02 | Ask for Markdown with headings, lists, a table, a code block in two languages and a 120-line code listing | Rendering is correct; table fits; code scrolls and keeps indentation; copy buttons copy exact text | 4 |
| S1-03 | In a No project (`sem-projeto`) conversation, ask about the seeded `facts/alpha.txt` by path | The chat cannot read it and says so, with no invented content; the scope label shows No project | 2 |
| S1-04 | Create a project with the seeded folder; ask 6 fact questions about the files (code word, a CSV total, a function name, a line count) | Every answer matches the seeded facts; file reads are shown; no invented facts | 6 |
| S1-05 | Add a second folder to the project; ask about a file in each, then about a file outside both | Files in either folder are read; the outside file is refused or escalated to an approval card, never read silently | 3 |
| S1-06 | Create a folder-less project; ask a general question and ask it to read a file | Chat works; the project has no folder tree; file requests are answered without a crash | 2 |
| S1-07 | Attach a text file and a code file to the project chat; ask for a summary and a bug review | Both attachments are listed as chips and used; answers cite their content | 3 |
| S1-08 | Edit a seeded file on disk, then ask the same fact again | The new value is returned, not the stale one | 3 |
| S1-09 | Same conversation, open the Code view and ask one question there, then return to Chat | Chat and Code show one conversation with one history; no duplicate turns | 3 |
| S1-10 | Baseline timing: close and open the app 3 times; send 2 short prompts in a fresh conversation | Record app open time and TTFT medians as the baseline for later rounds | 2 |

Slice 1 total: 2 + 4 + 2 + 6 + 3 + 2 + 3 + 3 + 3 + 2 = 30.

## Slice 2: switches, queue, Stop, parallel chats and attachments (Sol and Luna)

| Id | Steps | Checks | Prompts |
| --- | --- | --- | --- |
| S2-01 | In one conversation: Sol medium, switch to Luna, send, switch back to Sol, send; each time ask the model to name itself | Each reply comes from the selected model; the picker keeps the choice; history is carried over | 4 |
| S2-02 | Same conversation: change the effort level to another one the picker offers, send, then restore medium | The effort shown matches the run; no stale effort on the next send | 3 |
| S2-03 | Ask for a very long answer, press Stop mid-reply, then send a new question | Stream halts quickly; partial text is kept and marked stopped; the next send works; no ghost stream | 3 |
| S2-04 | Send a long prompt and, while it streams, send 2 follow-ups; then cancel one queued follow-up | Follow-ups show as queued, run in order, the cancelled one never runs; the status line matches | 4 |
| S2-05 | Two conversations streaming at once (2 prompts each, started within a few seconds) | Both complete with their own text; no cross-talk; switching tabs keeps each stream live | 4 |
| S2-06 | Attach a PNG with known color and text; ask what it shows | Answer matches the image, or the image notice appears once; chip is shown | 2 |
| S2-07 | Attach a code file in this round and refer to it in a later turn, after a model switch | The later turn still sees the file; no repeated notices | 3 |
| S2-08 | Attach the over-limit text file; ask about its start and its end | The "excerpt sent" chip is shown; the answer admits what was not sent | 2 |
| S2-09 | While a long answer streams, scroll up, then back down; send a follow-up while the queue drains | Auto-scroll stops when scrolled up and resumes at the bottom; queue drains in order | 3 |
| S2-10 | Attach three files of different kinds in one turn and ask for a one-line summary of each | All three are used; chips are removable before send | 2 |

Slice 2 total: 4 + 3 + 3 + 4 + 4 + 2 + 3 + 2 + 3 + 2 = 30.

## Slice 3: DeepSeek round-trip, reload, close and reopen, window behavior

| Id | Steps | Checks | Prompts |
| --- | --- | --- | --- |
| S3-00 | **Pause: await DeepSeek key.** The owner types the DeepSeek key in Settings > Providers of the test instance (127.0.0.1:18640). The operator waits and does not touch the field | Provider shows as ready; the campaign resumes only after the owner confirms | 0 |
| S3-01 | New DeepSeek conversation; send a question and a follow-up | Reply streams; usage or balance shows without errors; record TTFT | 2 |
| S3-02 | One conversation: Codex Sol turn, DeepSeek turn, Codex turn (2 prompts each); in each ask what was said earlier | History carries over in both directions; check whether the switch is allowed or the conversation's execution mode locks (`conversation_execution_mode_locked`, 409) and that the message shown is clear; note which result occurs and whether the doc `dossier/conversation-execution-mode.md` agrees | 6 |
| S3-03 | New conversation that starts on DeepSeek, then switch to Codex | Same checks as S3-02 in the other direction; no silent mode change | 3 |
| S3-04 | Reload the page mid-reply (Sol) | The conversation and the running stream are restored; no duplicate turn; the final text is complete | 3 |
| S3-05 | Close the app window mid-reply and reopen it. Record window size and position before and after | The harness keeps working (attach mode); the restored conversation shows the stream or its final text; window bounds are restored | 4 |
| S3-06 | Idle close and reopen 3 times; after the last, send one prompt per conversation | Open and reopen times recorded; selected conversation and scroll position restored; send works | 2 |
| S3-07 | Restart the harness from the admin of the test instance, then continue an earlier conversation | Session resumes with history; if the provider session cannot resume, the message is clear and the history is intact | 3 |
| S3-08 | DeepSeek: long answer, Stop mid-reply, then a new question | Same Stop checks as S2-03 on DeepSeek | 2 |
| S3-09 | DeepSeek: Markdown, table, code block and copy buttons | Same rendering checks as S1-02 on DeepSeek | 2 |
| S3-10 | DeepSeek in the seeded project: 3 fact questions | Answers match the seeded facts, or the limits of the DeepSeek access are explained clearly | 3 |

Slice 3 total: 0 + 2 + 6 + 3 + 3 + 4 + 2 + 3 + 2 + 2 + 3 = 30.

## Slice 4: long conversation, errors, keyboard, sizes, themes, housekeeping

| Id | Steps | Checks | Prompts |
| --- | --- | --- | --- |
| S4-01 | One conversation of 18 turns on Luna: plant 4 facts in the first turns, add filler turns, ask for recall at turns 10, 15 and 18 | Recall is correct or the loss is explained; no `source_context_limit`; UI stays responsive; record RSS at turns 1, 9 and 18 | 18 |
| S4-02 | Rename a conversation, archive it, find it in Settings > Archived chats, unarchive it, then delete permanently another one (2 short prompts to create content) | Title updates everywhere; archive hides, unarchive restores history; permanent delete removes it and its history, with the explicit second step | 2 |
| S4-03 | Offline provider: stop the provider (or select a model name that does not exist), send, then restore it and resend | A readable error shows with no raw stack text; the conversation is not corrupted; the resend works | 2 |
| S4-04 | Network-style failure: cut the provider network access or interrupt the harness during a stream, then retry | Failure is shown; the retry works and does not double the turn | 2 |
| S4-05 | Keyboard-only flow: focus composer, send, open the model picker, switch conversation, open the "/" palette, Stop, all without a mouse | Every step is reachable with visible focus; Escape closes popovers; Enter and Shift+Enter behave | 2 |
| S4-06 | Resize to a narrow window and a wide one; send one prompt at each size | No horizontal overflow; composer and side panel stay usable; code blocks scroll | 1 |
| S4-07 | Light and dark themes with a Markdown and code reply visible | Contrast is readable; code and tables are themed; the choice survives reload | 1 |
| S4-08 | Two conversations, one streaming in the background; switch between them | Streaming indicators are right; the unread or finished state is clear; no text lands in the wrong chat | 2 |

Slice 4 total: 18 + 2 + 2 + 2 + 2 + 1 + 1 + 2 = 30.

## Coverage map

- Send, streaming, Stop, queue: S1-01, S2-03, S2-04, S2-09, S3-08.
- Long conversation and context recall: S4-01 (plus S1-08 for stale reads).
- Model and effort switches: S2-01, S2-02, S2-07.
- Provider switch in one conversation and execution mode lock: S3-02, S3-03.
- Reload, close and reopen, window bounds, session resume: S3-04 to S3-07, S1-10.
- Parallel conversations: S2-05, S4-08.
- Rendering and copy buttons: S1-02, S3-09, S4-07.
- Rename, archive, delete: S4-02.
- Errors and recovery: S4-03, S4-04.
- Keyboard, sizes, themes: S4-05, S4-06, S4-07.
- Projects, folders, No project (`sem-projeto`) and attachments: S1-03 to S1-09, S2-06 to S2-08, S2-10, S3-10.
