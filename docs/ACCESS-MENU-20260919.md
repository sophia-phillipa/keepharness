# Access menu and full-access mode

**Superseded in part by fix round 01 (F-58, F-110):** the default access mode, whether it carries over between conversations, and the approval-card guarantee described here no longer apply — see [conversation-execution-mode.md](../dossier/conversation-execution-mode.md) for the current behavior. The menu layout, options and validation notes below (as of 2026-09-19) are otherwise still accurate.

Wider composer with a more visible border. The access selector now opens a box up to 620px wide, with a border, descriptions, a selection indicator and four options: Ask for approval, Automatic, Full access and Read-only. On narrow screens, the menu respects the window bounds. Keyboard: arrows/Home/End, Enter, Escape and Tab. A connection block closes the menu while preserving the selected mode.

`access_mode=full` is accepted by the API. It does not widen administrative permissions, roots or network access. Codex/Claude use never/dontAsk, as already happens in auto mode; unexpected new requests from those executors do not receive additional automatic authorization. In the isolated local executor, recognized actions are approved according to effective permissions. Explicit requests to widen permissions are denied in full-access mode; requests for information from the user remain interactive.

Validation: 21 backend tests passed in `test_approval_policy.py`, `test_model_permissions.py` and `test_native.py`; Playwright layout passed across six themes, desktop/mobile, keyboard, Full access selection and payload; 12 UX scenarios, per-model permissions and block/reconnect passed. No inference run or full suite. `git diff --check` and JavaScript syntax verified.

Local activation: the service had no queued/running jobs. Restarted from the admin panel after an idleness check; stop/start HTTP 200. `/v1/version` responded HTTP 200, version 0.4.4, build bb0a3bc26aa7. No new version, commit or change to global grants/defaults.
