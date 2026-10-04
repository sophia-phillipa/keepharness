# Archive, Delete permanently and storage caps

Decisions D31 and D33 (PRD-R4-6; SEC-R3-3, HAR-R1-1, HAR-R3-3, OPS-R3-4). What used to be
"Delete conversation" only hid a conversation: every turn stayed readable by job id, its
files stayed on disk and its runs kept counting toward the project caps, so a project could
lock itself out for good. Now a conversation can be archived (reversible) or deleted
permanently (erased), and the caps count only what is kept.

## Who can do it

Every client, on its own conversations only. Another client's conversation answers
`job_owner_denied` (403) to archive and delete, exactly like reading it. A conversation with
a queued or running turn answers `conversation_busy` (409).

## Archive

`PATCH /v1/conversations/{id}` with `{"archived": true}` hides the conversation from
`GET /v1/conversations`; `{"archived": false}` brings it back with its full history. Any
other value is `invalid_archived` (422). `GET /v1/conversations?archived=true` lists the
archived ones. An archived conversation cannot be continued or opened
(`conversation_not_found`) until it is unarchived; its turns keep answering on the job routes
because nothing was erased. Archived rows live in the `deleted_conversations` table, which
keeps its original name so older releases read it the same way (as hidden).

UI: the row menu offers **Rename conversation**, **Archive conversation** (one click) and
**Delete permanently**. Settings › **Archived chats** lists archived chats with
**Unarchive** and **Delete permanently**.

## Delete permanently

`DELETE /v1/conversations/{id}` (live or archived) answers
`{"id", "deleted": true, "turns": <n>}`. In the UI it takes two steps: the menu item opens a
dialog that says what is erased, and only its **Delete permanently** button sends the
request. The purge removes:

- the conversation's turns (`jobs`), their `events`, `gates` and `effects`, its title and
  remembered approvals;
- uploads that only this conversation attaches (row and `files/<project>/<id>/`); an upload
  that another conversation attaches survives, and so does another upload with the same
  sha256, which owns its own hard link to the bytes;
- the session folders (`sessions/<conversation>` and `sessions/<turn>` for Maestro stages),
  Maestro plans (`maestro/<turn>`) and workspace answer copies
  (`workspaces/<id>/work/_harness_results/<turn>`);
- provider sessions in the harness-owned provider homes (`provider_homes`, the state's
  `providers/` folder): every file or folder named after a session id found in the
  conversation's session markers or turn results. A provider home that holds or sits in the
  person's own `~/.codex` or `~/.claude` is never walked.

Afterwards every turn's `/v1/jobs/{id}`, `/events`, `/spans` and `/artifacts/result.json`
answers 404. The audit keeps one line per turn in `purged-turns.jsonl` (state folder,
mode 0600): job and conversation ids, project, owner, created and purged times, provider,
model and input/output tokens, never content.

Order: the conversation is archived first (no turn can join it), then the disk is cleared,
then the rows go in one transaction. A purge cut short leaves an archived conversation whose
Delete permanently can run again. Deployment backups (`backups/<turn>`) are copies of the
project's own files and are kept.

## Storage caps

A project keeps at most 1000 runs (`job_storage_limit`, 429) and 2 GiB of uploads
(`upload_limit`, 413), as before, but:

- only kept content counts: a permanently deleted conversation frees its runs and uploads at
  once; archived conversations still count, since they are kept;
- identical uploads are stored once: an upload whose sha256 matches a kept upload of the
  project becomes a hard link to it and adds nothing to the count (the cap is checked after
  hashing, so a repeat upload succeeds at the cap);
- `GET /v1/storage?project_id=<id>` returns
  `{"project_id", "runs": {"used", "limit"}, "bytes": {"used", "limit"}, "warning"}`;
  `warning` is true from 80% of either cap. Settings › Archived chats shows it as the
  Storage line, highlighted when `warning` is true.

## Migration of existing data

Existing duplicates were stored as separate copies. `python -m agent_service.storage_migration
--state-dir <state>` prints, per project, the kept runs, the counted upload bytes and the
archived conversations, plus the duplicates and the bytes linking would free; it changes
nothing. `--apply` links each duplicate to the first upload with the same content after
checking both against the recorded sha256, logging each file in `storage-migration.jsonl`.
Reruns skip what is already linked. Rollback: content never changes; give a file its own
copy again with `cp -p <source> <source>.copy && mv <source>.copy <source>`.

Conversations deleted before this release are archived ones now: they appear under Archived
chats and can be restored or deleted permanently.

Tests: `tests/test_conversations.py` (purge 404s, rows and folders gone, shared file
survives, archive/unarchive, guests, busy, caps, Storage, capability card),
`tests/test_gauntlet_round10_uploads.py` (dedupe, cap after hashing),
`tests/test_storage_migration.py`, `tests/conversation-delete.spec.cjs`.
