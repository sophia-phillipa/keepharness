# Conversation title synchronization

The Harness conversation title is the source of display metadata: an explicit rename, or the first user prompt limited to 100 characters. Every provider receives the same `_conversation_title`, independently of the current prompt, resource expansion, workspace prefix, history replay and model handoff. Internal Maestro stages do not reuse the user conversation title.

| Executor / provider | Behavior |
|---|---|
| Codex native and scoped | `thread/name/set` after start/resume, before model turn |
| Local and DeepSeek via Codex | Same shared Codex implementation, including local isolated runtime |
| Claude native | CLI `--name`, including `--resume` on continuation |
| Claude scoped | No persistent provider session (`--no-session-persistence`); external rename is not applicable |
| Gemini CLI 0.60.0 ACP | No implemented title setter; emits `session_title_sync_unsupported` instead of claiming external synchronization |

Names propagate when a session is created or resumed for another execution. A rename in the Harness does not immediately rename idle external sessions. This is one-way Harness-to-executor synchronization, not a bidirectional title merge. Codex title rejection/timeout emits `session_title_sync_failed`; the title operation never starts a model turn itself. Empty titles do not trigger native metadata changes.

## Evidence and validation

- Codex CLI 0.155.0-alpha.9.2 generated stable schema: `ThreadSetNameParams` requires `threadId` and `name`. [Official App Server reference](https://learn.chatgpt.com/docs/app-server) describes `thread/name/set` for loaded and persisted threads.
- Claude CLI 2.1.258 help exposes `--name`; adapter contract tests check the argument without inference.
- Gemini CLI 0.60.0 bundled `docs/cli/acp-mode.md` and ACP agent implementation expose creation/loading/prompt/mode/model, but no session-title setter. Gemini remains hidden/limited as described in its adapter specs; no Antigravity compatibility is inferred.
- Targeted tests cover root and renamed titles, all five providers at the service boundary, handoff, Codex creation/resume in native/scoped modes, errors/timeouts and explicit Gemini limitation. No live model inference, benchmark, full regression suite or Git milestone was performed.
- The reported existing Codex conversation was renamed to its actual Harness title using the app integration. This repairs that conversation only, not unrelated historical sessions.

Changes must be loaded by the running Harness service before new executions use them. An active execution was not interrupted for deployment.

Final targeted validation: `.venv/bin/python -m pytest tests/test_session_titles.py tests/test_native.py tests/test_model_handoff.py tests/test_conversations.py tests/test_gemini_titles.py tests/test_gemini_integration.py tests/test_claude_title_sync.py tests/test_adapter_specs.py -q` — **66 passed**, three preexisting framework deprecation warnings. `git diff --check` passed.
