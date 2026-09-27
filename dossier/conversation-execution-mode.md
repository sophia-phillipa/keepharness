# Conversation execution mode

New conversations offer a one-time isolation switch above the conversation, with native execution selected by default where supported. The explanation uses plain language about working in the project folder versus a separate space with restricted file/tool access; it avoids CLI, engine and session terminology. The switch can be changed until the first job is accepted; a failed submission preserves both the choice and the draft.

After the first message, the switch and explanation disappear. The same shield icon remains at the upper-right corner inside the prompt box. The active switch and isolated-session icon use the theme accent; native state is neutral. The icon is an accessible read-only status, not an editable switch. Reloading or reopening a conversation restores its fixed mode. A new conversation resets to the supported default.

## Provider capabilities

| Provider | Available conversation modes |
|---|---|
| Codex | Native (default), isolated |
| Claude | Native (default), isolated |
| DeepSeek | Native |
| Gemini | Native; its existing provider limitations still apply |
| Local models | Isolated only: the existing mandatory filesystem boundary is preserved |

Single-mode providers disable the switch and explain why. Switching to an incompatible model after explicitly choosing a mode does not silently change that choice; sending is blocked until a compatible model/mode is chosen. Existing conversations cannot change mode via model handoff.

The API field is `execution_mode` (`native` or `scoped`). The first request establishes the conversation mode; follow-ups inherit it. The server enforces immutability and rejects unsupported combinations. Legacy conversations retain their effective mode rather than silently adopting the new default. Execution mode is separate from `access_mode`: approval choices never grant extra project permissions or redefine isolation.

## Validation

`tests/conversation-execution-mode.spec.cjs` covers native default, keyboard switch, draft reload, failed submission, fixed mode, same icon, theme accent, follow-up inheritance, reopening, new-conversation reset, mobile overflow and mandatory/unsupported provider modes. Desktop and mobile screenshots were inspected. `tests/harness-connection.spec.cjs` verifies the existing readiness gate still blocks interaction and preserves drafts during reconnects.

No new release, branch or commit is part of this change. No provider inference is needed for these checks.

## Fix round 01: isolation and access decisions (F-58, F-110)

Two owner decisions from fix round 01 (`~/.cache/th/design/fix-round-01.md`, `~/.cache/th/design/findings.md`) change how a conversation's isolation and access mode are chosen and shown, on top of the mechanism described above. Status: decided design, implemented by work package WP-A (backend in A1, UI in A2, first in the `agent_service/ui.js` lane); this document records the target behavior for that work, not a claim that every part is merged.

**F-58 — isolation and access choice move out of the in-chat toggles.**

- **Isolation** stays a choice made only once, when the conversation opens (as already described above: the one-time switch before the first message). Once the first message is sent, the conversation shows a fixed, read-only start-of-session notice at the top — "Native conversation" or "Isolated conversation" — instead of an editable control. There is no isolation toggle anywhere in an existing conversation.
- **Access** (the approval policy: Read only / Ask for approval / Automatic / Full access) no longer carries over from one conversation to the next. Every new conversation starts in **Ask for approval**, regardless of what a previous conversation used, and a start-of-session notice states the active access mode next to the isolation notice. The browser-level memory of the last access choice (`chat-selection` in `localStorage`) stops seeding a new conversation's access mode; it may still seed unrelated preferences (last project, last model) that are not the access mode.
- This closes finding F-58 (a browser-remembered "Full access" silently applying to a new conversation with no visible warning) and, as an accepted side effect, finding F-94 (a model/mode mismatch after switching isolation): since isolation is no longer an in-chat toggle after the first message, that mismatch can only occur when picking the model at conversation start, where a clear way out (pick a compatible model, or start a new conversation) is always available — Send is never left dead behind a disabled control.
- Access mode is still only an approval policy, per the existing rule above: it never redefines isolation, and isolation never grants extra project permissions.

**F-110 — "Ask for approval" guarantees a card before any change.**

Finding F-110 recorded a live Codex turn, in `ask`, writing a file with zero approval cards: the pre-round mapping (`workspace-write` sandbox + `on-request` policy) is exactly the documented Codex behavior for unattended edits, not a bug in the provider, so `ask` had to be remapped rather than merely fixed. The decided mapping (full rationale and options considered in `~/.cache/th/design/f110-approvals.md`) is:

| Mode | Codex (thread and turn) | Claude |
|---|---|---|
| **ask** | `sandbox:"read-only"`, `approvalPolicy:"on-request"`, `approvalsReviewer:"user"`; turn `sandboxPolicy:{type:"readOnly", networkAccess:true}`; never `dangerFullAccess`, even when the account is otherwise unrestricted | `--permission-mode default` (never `bypassPermissions`); `--settings` adds `permissions.ask:["Edit","Write","NotebookEdit","Bash","mcp__*"]` and `sandbox.autoAllowBashIfSandboxed:false` |
| auto, full, read_only | unchanged from before this round (`auto` additionally pins `approvalsReviewer:"user"`) | unchanged from before this round |

What this guarantees in `ask`, for both providers:
- No file is created, edited or deleted, and (for Claude) no `Bash`/`Edit`/`Write`/`NotebookEdit`/MCP tool runs, without an approval card appearing first. For Codex this guarantee comes from the OS-level read-only sandbox forcing every write to escalate through `item/fileChange/requestApproval` or `item/commandExecution/requestApproval` — not from model compliance.
- Network stays **on** in `ask` (`networkAccess:true` for Codex; nothing in the Claude settings disables it) — `ask` restricts changes, not connectivity.
- MCP connectors and plugins stay **enabled** in `ask` for both providers; `ask` does not implicitly disable integrations.
- A read-only Codex command (one the read-only sandbox does not need to escalate) may still run without a card. This is an accepted limitation, not a defect: there is no supported Codex policy today that prompts before every command regardless of effect, only before every modification.
- The approval card carries the pending change as a diff: the harness remembers each `item/started` fileChange's `changes`, keyed by `itemId`, and attaches them to the approval event.
- `item/fileChange/requestApproval` is a recognized item in `approval_policy.full_approval_allowed`, alongside the legacy `applyPatchApproval`/`execCommandApproval` names it already knew.

This closes finding F-110. Acceptance is a live check, at most two turns per provider, in a temporary project: create a file in `ask` and decline — the file must be absent; then run a command and create a file in `ask` and approve — both must exist, confirming Codex applies the approved patch under its read-only sandbox and Claude does not silently inherit `acceptEdits`.

**New error: `isolation_unavailable`.** Choosing (or defaulting to) an isolated conversation for a non-local backend on a server without Linux/`bubblewrap` is refused with `APIError("isolation_unavailable", 422)` (`agent_service/services/conversation_service.py:validate_execution_mode`) rather than failing mid-run; the composer surfaces it as "Isolated conversations need Linux with bubblewrap on the server. Turn isolation off or ask the administrator to install bubblewrap." (`agent_service/ui.js:userErrors`). Local execution keeps its own, separate sandbox check and error (`local_filesystem_isolation_unavailable`, `adapters/local/sandbox.py`), since local conversations are always sandboxed and never offer a native choice.

Final backend validation: `.venv/bin/python -m pytest -q tests/test_execution_modes.py tests/test_conversations.py tests/test_model_handoff.py tests/test_model_permissions.py tests/test_native.py tests/test_attachment_formats.py tests/test_context_recovery.py` — **86 passed**, one external Starlette deprecation warning. Both browser scripts named above passed using the bundled Playwright runtime. JavaScript syntax and `git diff --check` passed.

Deployment: an initial guard deferred restart while one job was active. Once it completed, the guard confirmed zero active/queued jobs and `tail-harness.service` was restarted. Live HTTP checks on port 8095 confirmed the simplified copy and model `execution_modes` fields. No active job was interrupted. The UI hides the new mode controls until the server advertises this contract, preserving compatibility during rollout.
