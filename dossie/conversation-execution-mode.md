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

Final backend validation: `.venv/bin/python -m pytest -q tests/test_execution_modes.py tests/test_conversations.py tests/test_model_handoff.py tests/test_model_permissions.py tests/test_native.py tests/test_attachment_formats.py tests/test_context_recovery.py` — **86 passed**, one external Starlette deprecation warning. Both browser scripts named above passed using the bundled Playwright runtime. JavaScript syntax and `git diff --check` passed.

Deployment: an initial guard deferred restart while one job was active. Once it completed, the guard confirmed zero active/queued jobs and `tail-harness.service` was restarted. Live HTTP checks on port 8095 confirmed the simplified copy and model `execution_modes` fields. No active job was interrupted. The UI hides the new mode controls until the server advertises this contract, preserving compatibility during rollout.
