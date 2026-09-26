# UC-003 — Additive project and model access

Date: 2026-09-18

## Required rule

A conversation without a project uses only its model's configured folders and permissions. Within a project, the project's folders and explicit grants are added to the model's folders and grants. A project grant can enable an operation disabled by the model's default. An absent or false project grant does not revoke a model grant. Service/project membership and authenticated identity checks still apply.

## Configuration

Each exact local weight file stores `permissions`, `capabilities.tools`, and `allowed_roots`. Runtime model identifiers are bound to discovered canonical weight paths. Project settings store a root and optional `permissions` grants. The UI offers a server-side directory picker, directory creation, per-model folders and additive project grants. Browsing does not grant access; saving does.

The harness fetches effective permissions when the selected project changes. Attachments are allowed only for an eligible model in that project. Switching to a model without attachment permission preserves the draft and attachment while blocking submission with an explanation.

## Enforcement and limits

Local agents run inside a Linux bubblewrap filesystem boundary. Only the private session, selected authorized folders, runtime binary and necessary system files are mounted. Roots outside that boundary, including symlink destinations, remain inaccessible. Writes follow the effective write permission. Network access is controlled by the CLI tool sandbox; disabling internet also denies approval requests that would escape that sandbox. The model endpoint itself requires local network access. This is not an independent network firewall.

The local tool list omits cloud web search and auxiliary multi-agent/goal/image tools. Web retrieval uses a permitted local terminal tool. A model's ability to call a tool must be tested separately from permission to use it.

## Validation

- Automated tests cover additive grants, per-model defaults, upload denial/acceptance, project-sensitive catalog queries, folder union, no-project scope and actual bubblewrap filesystem restrictions.
- Browser tests cover folder navigation, creating a project directory, permission-error recovery, model folders, mobile layout and changing permissions when switching project/model.
- Real Qwen run `e8ada87b73c7416cb324a140333aa506` consumed an uploaded marker and retrieved HTTPS example.com, returning HTTP 200 and its title.
- Real isolated Qwen run `762bb4699cf44aa788e1024f075e3c9e` read an authorized project marker, failed to read a real external fixture file, and retrieved HTTPS example.com.
- Real no-project Qwen run `48cfe2d2f4324000914eab43935b13bb` read its model folder and could not read the project-only fixture folder.
- The official Gemma E4B QAT Q4_0 passed a minimal direct function-call probe. Its first full CLI task did not execute tools and was cancelled; this is not a successful agent validation. Follow-up results must be recorded separately.

No cloud inference was performed. Test content is synthetic. Private paths, profiles, weights and credentials are not committed.

### Completed Gemma validation

Real Gemma CPU run `8180bdae19ed4b49b823ca49c13ee325` called `exec_command` to run a prewritten synthetic verification script. Tool output confirmed both the project marker and Gemma model-folder marker, `FileNotFoundError` for the real external fixture, and HTTPS HTTP 200 / Example Domain. This validates access enforcement and tool execution, not general coding quality. Earlier attempts with the full default CLI prompt and self-authored scripts failed to use valid arguments; the local adapter now supplies a concise base instruction and omits unused auxiliary tools. A scripted task is easier than open-ended programming; do not equate this result with all tasks being reliable.

Final automated suite: 102 tests and 2 subtests passed. Full Chromium UI runner passed, including directory creation, additive grants, model-scoped roots, project-sensitive attachment controls and right-panel execution details. No cloud inference was used.

## Oversized attachment recovery

A provider context-overflow error now produces a readable message. On continuation, all attachments participating in the rejected context are excluded from automatic history replay, and the rejected prompt is omitted. Stored files, audit payloads and visible history remain intact. The poisoned native session marker is archived once; a fresh session receives only valid conversation history. Historical raw provider errors are recognized too. A newly uploaded smaller file can be used normally. Regression tests cover inherited attachments, preserved audit records and one-time session reset.
