# File navigation in the right panel

Request: share the right panel between Files (initial view) and Activity, with controls in the top bar. The user explicitly expanded the scope to allow system-wide navigation, independent of the project. The conversation's project remains the attachment destination; execution permissions are not widened.

The tree loads children on expansion, shows visible files and folders, allows multiple selection and drag to the prompt. The browser starts at Local Folders and keeps External Folders, highlighting the selected option; the System and external media shortcuts were removed. System folders and hidden items are filtered according to the operating system; this was locally confirmed on a Linux host during this review. The final revision removes checkboxes and the Attach selection button: attachment happens by drag, with visual selection by click and modifiers. Navigation is independent of the project. The selection must be bound to its destination at the moment of attachment and protected against context switches during async requests. Directories are expanded into supported attachments, preserving originals and flagging skipped items/limits. The existing limit is ten files per request.

The Activity view preserves existing events and controls. Files is the initial view; on mobile, the panel opens on demand. Both buttons open or toggle the view, and collapse the panel when clicking the already-open view again.

## Contract

- GET `/v1/project-files?view=tree&project_id=...&root_id=...&path=...&start=1&limit=100`: immediate directory listing, browser roots and pagination indicator.
- POST `/v1/project-files/attach?project_id=...&backend=...&model=...&max_files=...`: selection relative to the browser root; returns attachments with `file_id` and skipped items with a reason. Reuses the question submission via `file_ids`.

## Validation

- Backend: `tests/test_project_browser.py` — 7 passed, including authentication, out-of-project attachment, canonical paths, permissions, limits and model forwarding.
- UI: `tests/harness-files-panel.spec.cjs` passed with mocked APIs for multiple selection, expansion, toggling, drag and submission.
- Runtime: restart only after confirmed idleness, stop/start HTTP 200. Global tree HTTP 200 with System, home folder and media shortcuts present.
- Real attachment of a synthetic file in `/tmp`: HTTP 200, one attachment, no skipped items, original preserved. No model was executed.
- Mobile review completed: Files/Activity controls grouped on the right; the drawer starts right below the bar and ends at the right edge. The focused test passed after the adjustment. The real tree loaded without error.

No Git milestone.

UI review: checkboxes and the Attach selection button were removed. Visual selection uses click/Ctrl/Shift and drag to the prompt. Below Files: "drag files or folders into the chat to use them." The access selector synchronizes icon, name and tooltip with the active mode; smaller descriptions explain each mode.
