# Changelog

All notable changes to KeepHarness are recorded here. The format follows [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The per-release notes in [`dossier/releases/`](dossier/releases/) hold the full behavior, acceptance criteria, migration notes and actual validation; this file is the short index.

## [Unreleased]

### Added

- Retry on the latest failed or interrupted turn (`POST /v1/jobs/{job}/retry`), and a composer warning that names a model which cannot read images and offers another model (`capabilities.images` in `/v1/models`).
- The Settings button opens a submenu of the Settings sections (Personal: Appearance, Models; Integrations: Customize; Archived: Archived chats; System, local host only: Providers, Operations, Run history, Catalogs and vault, Connection / MCP), each jumping straight to its page; Ctrl/Cmd+, opens Settings at the last section. The desktop app handles an admin-origin open by focusing the main window and opening Settings there (`#open=settings/<section>`) (WP2).
- Back and Forward at the top left, like the Codex app: an in-memory view history (conversations, Settings pages, Space, Scheduled, Customize, Home), Ctrl/Cmd+[ and Ctrl/Cmd+], mouse buttons 3 and 4, with each conversation's scroll position kept (WP1).
- `GET /v1/conversations/{id}/continuation?target=claude|chatgpt` returns a redacted, size-capped, paste-ready prompt to continue a conversation in the Claude or ChatGPT desktop app; tool evidence is names and outcomes only (WP5 backend).
- The conversation row menu has "Continue in another app…": a dialog to pick ChatGPT or Claude, review the redacted handoff, copy it or save it as `.md`; in the desktop app, after a Copy or Save and only for installed apps, a click on "Open ChatGPT/Claude" opens the app with the handoff (nothing is sent automatically) (WP5 UI).
- `/v1/integrations` gains an additive `elsewhere` list of tools connected on another provider the owner may use for the project, saying whether the current provider only needs to enable it (WP4 backend).
- Durable UI preferences in a backend store: `GET`/`PATCH /v1/ui-state` keep the owner's theme, panel, sidebar, reading size, model choice, project list, scroll and tour state in an allow-listed, size-capped file in the harness state folder instead of browser `localStorage`, so a port change or profile reset no longer wipes them (WP6 backend). The UI now reads and writes them through `window.HarnessPrefs`: old `localStorage` keys migrate into the store and are erased only once accepted, a read-only store shows a notice and stops writing, and a 413 is split per key (WP6 frontend).
- Cross-provider tool warnings: the Plugins menu lists tools connected on another provider ("On other providers", with Enable / Open Plugins), the Plugins chip shows a dot, and the provider-switch note names tools that do not follow the conversation (WP4 UI).

### Changed

- Continue in another app: a segmented ChatGPT/Claude control, cleaner spacing, and inside a distrobox container the apps are detected and opened on the host with constant arguments; the prompt goes through the clipboard there.
- The "Admin panel" link in Settings is gone and the admin no longer opens in a second window: "Open admin panel" and the desktop app open Settings > Providers (or the matching section) in the main window; the Settings nav is grouped like the Codex Settings navigation (WP2).

### Security

- Log and handoff redaction now masks GitHub tokens and credentials in URL userinfo; the continuation prompt also masks generic secret assignments, AWS keys, Slack tokens, PEM private keys and JWTs.
- `elsewhere` is empty for non-owner and remote callers, counts only providers allowed for the project, and never lists the `local` backend.

### Fixed

- Menus are opaque on every palette; conversation rows no longer show through the Settings menu on Paper.
Chat campaign fixes (see [the campaign log](dossier/research/chat-campaign-2026-10.md) and the "Chat campaign fixes" section of [`dossier/releases/v0.16.0.md`](dossier/releases/v0.16.0.md)):

- New Conversation's project button reads "No project" instead of "Choose project" (C-01).
- A reply's inline tool steps show what each tool acted on, like the Codex and Claude CLIs ("Ran sed -n 1,5p notes.txt", "Read facts/alpha.txt"); the target is redacted, at most 160 characters, and is not forwarded to another provider (C-03).
- The active or hovered conversation row in the sidebar no longer shows a grey block on its right edge (C-04).
- The header pill shows "Running" while a reply streams behind queued follow-ups (C-05).
- The view stays pinned to the bottom of a fast stream until the user scrolls up (C-06).
- Reopening the app or a conversation restores its scroll position; one left at the bottom reopens at its latest message (C-07).
- A chat that finishes in the background while another is open shows the unread dot (C-08).

## [0.16.0] - unreleased

Detail: [`dossier/releases/v0.16.0.md`](dossier/releases/v0.16.0.md).

### Added

- Whole attachments: an "excerpt sent" chip (`excerpt: true`) when only part of a long file is sent, DOCX text from headers, footers, footnotes and endnotes, and decoding of UTF-16 or UTF-8 BOM and Windows-1252 text files.
- Archive and Unarchive for conversations (`PATCH /v1/conversations/{id}` with `{"archived": bool}`), a Settings Archived chats screen and `GET /v1/storage` with run and byte caps counted on live content only.
- `python -m agent_service.storage_migration` to link identical old uploads (dry run by default, `--apply` to change).
- Projects without a folder (`"paths": []`) and a Files chip popover (recent uploads, Space pages, Upload, Browse project files).
- Schedules can pick an agent and up to 5 pages, and carry an `allow_internet` flag.
- Visual markers: icons on run steps, skill and agent chips that link to the catalog, chips for catalog names and known file paths in answers, and a Settings Appearance toggle.
- `keepharness backup` and `keepharness restore` for the state folder, with owner-only archives and secrets left out by default.
- Supervised harness restarts with backoff, a drain on shutdown, and a close confirmation in the desktop app when work is running.
- Hashed dependency locks (`--require-hashes`) and supply-chain scans in CI.

### Changed

- Automatic mode is bounded to the project on Codex, DeepSeek and Claude Code; anything beyond it raises an approval card, and Gemini asks before shell or connector requests.
- Full access is owner-only and off by default (admin setting `full_access`).
- Scheduled runs are offline unless the task sets `allow_internet`.
- `DELETE /v1/conversations/{id}` now purges the conversation for good; use the archive `PATCH` to hide one.
- The "/" palette lists what each provider CLI really reads, and work-item pattern matching runs off the event loop with a startup guard.
- Settings regrouped (Admin, Appearance, About), Chat and Code are views of one conversation, and the tour has five steps.

### Removed

- The Maestro planner: no multi-step plan mode, no "Maestro (auto plan)" model entry and no `maestro` backend (refused with 422 `backend_unavailable`). Old conversations reopen on the available provider; Workflows and declared `/` chains still run on the step engine.

### Fixed

- Document-heavy conversations of about 16 turns no longer fail with `source_context_limit`.
- Valid work-item patterns are no longer rejected as `invalid_work_item_pattern` on a busy host.
- Offline Gemini schedules with connectors configured run instead of failing.

### Security

- `POST /v1/login` checks `Origin` before counting the attempt, so a hostile page cannot lock real devices out.
- `/v1` JSON answers carry `Cache-Control: no-store`, another owner's job answers 404 like a missing one, and the admin API no longer returns raw exception text.
- Personal resources of the owner are hidden from guests in the palette, the catalog and run resolution.

## Earlier versions

Releases 0.15.x and before are described in the notes under [`dossier/releases/`](dossier/releases/).
