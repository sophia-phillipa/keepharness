# Chat polish (0.15.0, WP-16)

What the conversation view does since the gauntlet fixes; each line names the test that pins it.

## Answers and code

- Every answer has **Copy** (the Markdown source) and **Ask again** (newest answer only; re-sends the question it replied to as a new turn, never over a draft). Every code block has a header with its language and **Copy**. Copy uses the clipboard API and falls back to `execCommand("copy")` on an insecure origin. `tests/response-format.spec.cjs`, `tests/ask-again.spec.cjs`.
- A wide table scrolls inside its own region (`.table-scroll`, focusable) and keeps header words whole; code scrolls sideways with a visible scrollbar in light and dark themes. `tests/response-format.spec.cjs`.
- The desktop app allows `clipboard-sanitized-write` and `notifications` for its own origins only (`desktop/policy.cjs`, `permissionAllowed`). `desktop/policy.test.cjs`.

## Notifications (D35)

While the window is not focused, a run that needs the user, fails or finishes raises an OS notification (once per event); the number of "needs you" requests leads the window title. The permission is requested with the first message. `tests/window-alerts.spec.cjs`.

## Search and titles (D38)

- `GET /v1/conversations?q=<text>` returns only the caller's conversations whose prompts or answers contain the text (accent and case insensitive, at least 2 characters), each with a `snippet`. Without `q` nothing changes. The search dialog merges those hits with its local title matches, shows provider, date and snippet, never an internal id, and skips the project-files request for a project without a folder. `tests/test_conversations.py`, `tests/conversation-search.spec.cjs`.
- A conversation with no explicit title is named by the first sentence of its first prompt, `@@` markers removed (`resources.conversation_title`).

## Names (D42) and the model picker

Provider names: Codex, Claude Code, DeepSeek, Local models, Maestro. Model names are derived from the identifier when the server has no name (`claude-opus-4-7` is "Claude Opus 4.7", `gpt-6-astra` is "GPT-6 Astra"). In the Claude Code group only the newest model of each family is on top; older ones sit under "More models". Before a message goes to another provider than the last turn's, a one-line note says the conversation goes along. A fresh model choice starts on the provider's default effort, or Medium. `tests/claude-model-picker.spec.cjs`, `tests/model-provider-groups.spec.cjs`, `tests/conversation-route-divider.spec.cjs`.

## Plain words

Header access chip and composer menu share one label (no `read_only`); approval cards show the request's own message; publication reads "Sent through KeepHarness" or "Not controlled by KeepHarness"; the Agents chip opens the agent list without typing `@`; the status strip shows counts and the work item, never the prompt; "Worked for" leaves out queue time, which is shown as "waited". `tests/session-start-notice.spec.cjs`, `tests/approval-card-copy.spec.cjs`, `tests/composer-files-agents.spec.cjs`, `tests/harness-mock4-layout.spec.cjs`, `tests/harness-side-details.spec.cjs`.

## Limits and cost

- The draft limit is the server's 150,000 UTF-8 bytes: from 80 % a counter shows "N / 150,000 bytes", over it Send is blocked with the number of bytes to cut. Attachment chips show their size, the count has the per-file limit as its tooltip, and "+" stops at 20 files with the reason. A long draft is not re-split per key and the spoken character count waits for a pause. `tests/composer-limits.spec.cjs`.
- An idle tab makes at most about 30 requests a minute (activity polling every 20 s when nothing runs and the console is closed, the build checked every 30 s) and the "/" list is fetched once per project and engine, then filtered locally. `tests/harness-reconnect.spec.cjs`, `tests/harness-slash-palette.spec.cjs`.

## Not in this change

Move to project and Edit and resend (D36, D37, need a conversation PATCH), the "What this conversation can do" panel (L37), command input and output in the activity view (L45), the server-side request budget per person and the `/v1/version` hash cost (L32), and Stop with a queued follow-up (L47, WP-10).
