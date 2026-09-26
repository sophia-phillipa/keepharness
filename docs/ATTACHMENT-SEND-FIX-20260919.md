# Attachment send, selection and preview

The send button stopped working because the `send()` declaration was removed while inserting the file tree. The global identifier of the `#send` element masked the absence of a function. The function was restored with the access mode and initial title, without the task field removed earlier.

Remove-attachment buttons stayed disabled because the chips were created while `uploads` was still positive and were not rebuilt when the tree attachment finished. The final block now refreshes attachments after releasing that state.

Requested review: Shift must span visible items across different tree levels, including files and folders; dragging must not replace its own source element. The existing ten-file limit must be shown and enforced both for upload and for the file browser.

Attached PNG/JPEG/WebP images receive a `preview_url` authenticated by owner and project. The preview serves only the validated image file, with no-store and nosniff; it does not allow browsing an arbitrary path.

Backend validation: `tests/test_file_previews.py tests/test_project_browser.py` — 9 passed. Service restarted after confirming idleness, stop/start HTTP 200. `tests/harness-files-panel.spec.cjs` passed: click/Enter with prompt and file_ids, mixed Shift/Ctrl/Space selection, drag, preview, removal, the 10-item limit and hover feedback. Execution requests were mocked; no model was run during this validation. Syntax and `git diff --check` passed.

Click interception by notifications over the composer was also found. Notifications now sit below the top bar and their text no longer captures mouse events. Buttons use the selectors' neutral appearance and common hover/focus feedback. 128x96px thumbnails use an authenticated preview, and the composer shows the attachment count.
