# Local owner access

Decisions D09, D13 (desktop part) and the D05/D06 groundwork; gauntlet ledger WP-06 (SEC-R5 RC-01, RC-02, RC-04, RC-05).

## Why

Every account on a computer can reach 127.0.0.1. Before this change, the harness gave the owner identity `local` to any loopback request whose Host was `localhost` or `127.0.0.1`, and the admin handed its session cookie to any loopback browser. The harness also honoured the `Tailscale-User-Login` header for any Host, so a page whose name re-resolved to 127.0.0.1 could act under a mapped tailnet login.

## Contracts

| Area | Contract | Code |
| --- | --- | --- |
| Install secret | `<state>/local.key` (default `~/.local/share/keepharness/local.key`), mode 0600, created once and never rewritten. Only its SHA-256 (`local_secret_sha256`) goes into `runtime.json`. | `control/local_access.py`, `control/runtime_config.py` |
| Owner cookie | `keepharness-local` (product slug + `-local`) holds the secret: HttpOnly, SameSite=Strict, Path `/`. Cookies ignore the port, so one cookie reaches the admin and the harness on 127.0.0.1. | `control/local_access.py` |
| Admin | `GET /` serves the panel to every loopback request, but sets the `admin` cookie only when the owner cookie is valid. Without it, `/api/*` answers 401 and names `keepharness open`. | `control/routes.py` (`admin_guard`) |
| One-time link | `keepharness open` prints and opens `http://127.0.0.1:<admin port>/open?ticket=<expiry>.<nonce>.<HMAC>`; the admin accepts each ticket once within 5 minutes, sets the owner cookie (30 days) and the admin cookie, and redirects to `/`. `keepharness` (serve) prints a link only on a terminal, never in a service journal. | `control/cli.py`, `control/routes.py` (`open_link`) |
| Harness `local` | Loopback client, loopback Host, no proxy or Tailscale header, `local_access` on, and the owner cookie when the config carries `local_secret_sha256`. The secret is never a bearer token. A hand-written config without the digest keeps loopback trust and logs a warning at startup (tests and fixtures only; the admin always writes it). | `ConversationService.identity`, `holds_local_secret` |
| Host allow-list | One function, `host_allowed`, for both apps: the admin accepts `127.0.0.1`/`localhost`; the harness's loopback identity accepts the same names; a request with `Tailscale-User-Login` and a Host outside the configured origins gets 403 `host_denied`. Cookies need no Host check, because a browser never sends 127.0.0.1's cookies to another name, and a rebinding page cannot know a bearer token. | `control/local_access.py`, `ConversationService.identity` |
| Tailscale login | Mapped only when Host equals the netloc of `browser_url`, the remote origin that Tailscale Serve forwards. Without `browser_url`, the header is never honoured. | `ConversationService.remote_host` |
| Project folders | `POST`/`PATCH /v1/projects`, `DELETE /v1/project-folder` and `GET /v1/project-directories` answer 403 `project_management_local_only` to anyone but `local`, before any other check. | `agent_service/routes/projects.py` |
| Sharing | `project_registration` (admin default true) enables registration. `shared_projects` (admin default false) decides whether clients other than `local` receive registered projects; providers and `local` always do. | `ProjectService.share_projects` |
| Guest views | For clients other than `local`: `GET /v1/harness-agents` returns only name, backend, model, effort and availability; `/v1/resources` and `/v1/catalog` replace every absolute `source` path with the item's logical `resource_id`. | `harness_agents.public_view`, `resources.without_host_paths` |
| Enrollment refusal | The 403 `approval_session_required` body adds `login` for a tailnet identity. The UI tells `local` to run `keepharness approve-device --owner local`, and tells anyone else to ask the owner of this KeepHarness to run it with their login. | `approval_sessions.require_approval_session`, `ui.js` (`enrollmentMessage`) |
| Desktop | The main process reads `local.key` and sets the owner cookie before loading. When the harness window has no `harness_session` cookie, it runs `python -m control approve-device --owner local --yes`, loads the printed link only if it is an `/approve-device` link on the window's harness origin, and submits the confirmation form. | `desktop/main.cjs`, `desktop/policy.cjs` |

## Failure modes

- A missing or unreadable key in the desktop: no cookie is set, so the window gets 401 from both apps; `keepharness open` still works.
- A failed or slow enrollment command (20 s limit) or a link on the tailnet origin: the window opens normally, and approvals show the enrollment message.
- An admin restart within a ticket's 5 minutes forgets the used tickets, so such a ticket could be replayed once until it expires.
- The owner cookie, like any cookie on 127.0.0.1, also reaches other local ports from same-site pages. SameSite=Strict keeps it out of cross-site requests.

## Not in this change

The owner "Enroll a device" button and the list of enrolled devices with Revoke (D13 B/C), Owner/Guest roles (D05), a settings switch for `shared_projects`, and redaction of project roots in `GET /v1/projects` and `GET /v1/project-folder`.

## Tests

`tests/test_api_security.py` (`test_local_identity_needs_install_secret`, `test_runtime_config_carries_only_the_install_secret_digest`, `test_tailscale_login_requires_remote_host`, `test_project_management_local_only`, `test_registered_projects_stay_with_the_owner_unless_shared`, `test_non_local_views_redacted`), `tests/test_admin_security.py` (`test_admin_cookie_needs_install_secret`, `test_open_link_admits_one_browser_once`, `test_open_command_prints_a_one_time_link`), `tests/test_approval_authority.py` (`login` in the refusal) and `desktop/policy.test.cjs`.
