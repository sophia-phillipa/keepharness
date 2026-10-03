# Space pages

A page is a Markdown note that lives next to a project's conversations: a place to keep a
plan, a checklist or the notes of a long task, and to come back to it later. This is the
backend of the Space > Pages area; the UI is separate.

Pages are private. Each one belongs to the client that created it (the same identity that
owns jobs and conversations) and to the project it was created in. Another client never
sees it, not even in a project they share. A page is not part of any conversation and
is never added to a prompt by the harness.

## Who can use pages

Every authenticated client, for the projects it may use. The check is the one the other
project routes make: a project the client cannot use is `project_denied` (403) and
nothing is read or written. The "No project" scope (`sem-projeto`) works like any other
project.

## Data

One JSON file per page, in a folder per owner and project:

```
<state_dir>/pages/<sha256(owner)[:16]>/<project_id>/<id>.json
```

The owner folder is a hash, so the folder name does not reveal the client name. A project
id must be a single plain path component (`^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$`); any other
value is `page_invalid` with `field: "project_id"`, so a project id can never reach outside
its folder.

```json
{
  "id": "5d1c0b0a6e0d4f43a53e0f2b1f6a7c11",
  "title": "Release plan",
  "body": "# Release plan\n\n- [ ] Cut the branch\n",
  "created_at": "2026-10-03T12:00:00.000Z",
  "updated_at": "2026-10-03T12:00:00.000Z"
}
```

`id` is a random UUID4 in hex (32 lowercase characters) and never changes. Times are UTC,
ISO 8601, with milliseconds. The `revision` returned by the API is the SHA-256 of the exact
file text, so any change to the file (by the API or by hand) changes it.

| Field | Rule |
| --- | --- |
| `title` | 1-120 characters after trimming; no control characters (a line break is one) |
| `body` | 0-204800 bytes of UTF-8 (200 KiB), kept exactly as written; tabs and line breaks are allowed, other control characters are not |
| per owner and project | at most 500 pages |

A file is at most 413696 bytes (twice the largest body plus room for the other fields), which
covers the worst case of JSON escaping. Requests to the page routes may be that large; every
other route keeps the 200000-byte request limit.

## API

All routes need the same authentication as the other `/v1` routes and answer with
`Cache-Control: no-store`. Errors use the standard body `{code, message, retryable,
request_id}`; `page_invalid` adds `field`. `project_id` is a query parameter on the two
`GET` routes and a JSON body field on the others.

| Route | Success | Errors |
| --- | --- | --- |
| `GET /v1/pages?project_id=` | 200 `{"pages": [...]}`, newest first | `page_invalid` (422), `project_denied` (403), `page_storage_unsafe` (500) |
| `GET /v1/pages/{id}?project_id=` | 200 the page | `page_invalid` (422), `project_denied` (403), `page_not_found` (404), `page_storage_unsafe` (500) |
| `POST /v1/pages` | 201 the page | `page_invalid` (422), `project_denied` (403), `page_limit` (409), `page_storage_unsafe` (500) |
| `PUT /v1/pages/{id}` | 200 the page | `page_invalid` (422), `project_denied` (403), `page_not_found` (404), `page_changed` (409), `page_storage_unsafe` (500) |
| `DELETE /v1/pages/{id}` | 200 `{"deleted": true}` | `page_invalid` (422, `revision`), `project_denied` (403), `page_not_found` (404), `page_changed` (409), `page_storage_unsafe` (500) |

A listing item is a summary; `size` is the byte length of the body:

```json
{
  "pages": [
    {
      "id": "5d1c0b0a6e0d4f43a53e0f2b1f6a7c11",
      "title": "Release plan",
      "updated_at": "2026-10-03T12:05:41.318Z",
      "revision": "9f2c…64 hex characters",
      "size": 38
    }
  ]
}
```

A page is the stored record plus `revision`:

```json
{
  "id": "5d1c0b0a6e0d4f43a53e0f2b1f6a7c11",
  "title": "Release plan",
  "body": "# Release plan\n\n- [ ] Cut the branch\n",
  "created_at": "2026-10-03T12:00:00.000Z",
  "updated_at": "2026-10-03T12:05:41.318Z",
  "revision": "9f2c…64 hex characters"
}
```

Requests:

```json
POST /v1/pages   {"project_id": "p", "title": "Release plan", "body": "# Release plan\n"}
PUT /v1/pages/5d1c…   {"project_id": "p", "title": "Release plan", "body": "# Done\n", "revision": "9f2c…"}
DELETE /v1/pages/5d1c…   {"project_id": "p", "revision": "9f2c…"}
```

- `title` and `body` are both required on `POST` and `PUT`; `PUT` replaces the whole page
  (send `"body": ""` to empty it). `created_at` is kept and `updated_at` is set.
- `id`, `created_at`, `updated_at` and `revision` are accepted and ignored on `POST`, and
  on `PUT` apart from `revision`, so a page that was read can be sent back with its edit
  (`id` must match the path). Any other unknown key is `page_invalid` naming it.
- `PUT` and `DELETE` take the `revision` the client last saw. A different revision is
  `page_changed` (409) and nothing changes; read the page again and retry.
- A page that does not exist in that owner's project, one that belongs to another owner or
  project, and a path id that is not 32 lowercase hex characters are all `page_not_found`.
  The harness never says whether another client has a page with that id.
- A deleted or edited page is reflected by the next call; there is no cache.

## Security

- Folders are created `0700` (every folder this feature creates, parents included), an
  existing page folder is tightened to `0700` on every write, and files are `0600`. A
  symlinked project folder is refused (`O_NOFOLLOW` on the directory descriptor); a symlinked
  or non-regular page file is never read or written through. The API reports
  `page_storage_unsafe`; a listing skips the file with a warning.
- Writes go to a temporary file in the same folder (`O_EXCL|O_NOFOLLOW`), are fsynced, then
  published atomically (`link` for a new page, `replace` for an edit) and the directory is
  fsynced. The temporary file is removed on failure.
- A read-compare-write sequence holds one lock, so a stale `revision` never overwrites a newer
  edit and the 500-page limit holds under concurrent creates. The lock is per process; the
  service runs a single worker.
- Stored files are checked against the same limits as requests. A hand-edited file that breaks
  them (or is not JSON) is skipped in the listing with a warning and is `page_not_found` when
  read directly. It still counts toward the 500-page limit until removed by hand.
- The body is Markdown text. The harness stores and returns it as written; whoever renders it
  must treat it as untrusted input.

## Notes for the UI

- Listing reads every page file of the project to report `revision`, `updated_at` and `size`.
  That is bounded by the 500-page limit; a project with hundreds of large pages is slower to
  list.
- The summary has no body. Open a page with `GET /v1/pages/{id}` and keep its `revision` for
  the next save.
- Error copy for every code above lives in `userErrors` in `agent_service/ui.js`.
