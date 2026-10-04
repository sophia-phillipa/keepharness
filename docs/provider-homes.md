# Provider homes and the personal setup

Since 0.15.0 (work package WP-08; decisions D01, D02, D30) Codex, DeepSeek and Claude Code
runs no longer use the owner's `~/.codex` and `~/.claude`. Each provider CLI gets a home
owned by KeepHarness under the control state folder, and only an allow-listed environment.

## Homes

| Provider | `HOME` | Config folder |
| --- | --- | --- |
| Codex | `<state>/providers/home` | `CODEX_HOME=<state>/providers/home/.codex` |
| Claude Code | `<state>/providers/home` | `CLAUDE_CONFIG_DIR=<state>/providers/home/.claude` |
| DeepSeek (Codex engine) | `<state>/providers/home` | `CODEX_HOME=<state>/providers/deepseek` |

- Folders are created with mode 0700. The runtime config carries their root as
  `<provider>.provider_homes` (`control/runtime_config.py`); `adapters/shared/provider_setup.py`
  turns it into the child's variables. A runtime config without it (written before 0.15.0)
  keeps the host home until the admin restarts the harness.
- Sign-in: Settings › Providers › Log in / Renew access runs `codex login --device-auth` and
  `claude auth login` in these homes, so the login lives there and is refreshed there (one
  sign-in per provider, D02). Status checks, model lists and quota reads use the same homes.
  Isolated (`scoped`) runs copy the credential file from these homes, not from the terminal's.
- A Codex or Gemini service that is enabled but not signed in (or whose CLI is
  missing) does not stop the harness: its models are taken offline with the reason "Sign in
  required" (`unavailable_models` in the runtime config and the admin status), the other
  providers keep working, and signing in then applying the settings brings it back.
- DeepSeek keeps its own Codex home: it never holds the ChatGPT login; its key file is passed
  as its only credential.
- Sessions (Codex rollouts, Claude transcripts) stay in these homes, inside the state folder.
  Nothing is written under the owner's `~/.codex/sessions` or `~/.claude/projects`.
- Conversations whose native session lives in the old host home fall back once to the
  harness history (`native_session_missing`; see `tests/test_native_session_missing.py`).

## Child environment

`adapters/shared/process.py::child_environment` keeps only `PATH`, `HOME`, `USER`, `LOGNAME`,
`SHELL`, `TMPDIR`, `TZ`, `LANG`, `LANGUAGE`, `LC_*`, `CODEX_HOME`, `CLAUDE_CONFIG_DIR`, the
proxy variables and `SSL_CERT_FILE`, `SSL_CERT_DIR`, `NODE_EXTRA_CA_CERTS`, then adds the
DeepSeek key and the secret-vault injections. Everything else (`CLAUDE_CODE_*`,
`ANTHROPIC_BASE_URL`, `OPENAI_API_KEY`, session buses, agent sockets, harness authority) is
dropped. Sign-in commands keep the host session (minus harness authority and the Claude
OAuth token) so a browser can open.

## Personal setup (owner opt-in, off by default)

Setting: top-level `personal_setup` (boolean, default `false`) in the admin settings; it
replaced `services.claude.global_hooks`. It applies only to the owner's own conversations:
guests (any caller other than the local owner) and scheduled runs never get it
(`provider_setup.run_settings`). For such a run, still inside the harness home:

- Codex and DeepSeek: `features.hooks` follows the hooks grant (otherwise always `false`);
  the owner's MCP servers and plugins from `~/.codex/config.toml` are offered; the owner's
  `~/.codex/AGENTS.md` is appended to the developer instructions.
- Claude Code: the owner's MCP servers (`~/.claude.json`) and plugins are offered;
  `--setting-sources user,project` plus the owner's `hooks` with the hooks grant; the owner's
  `~/.claude/CLAUDE.md` is appended to the system prompt.
- `/v1/resources` lists the user-scope agents, skills and commands of the home the CLI reads:
  the harness home for Codex and Claude, `providers/deepseek` plus
  `providers/home/.agents/skills` for DeepSeek, whatever the opt-in says. With the opt-in the
  owner's own `~/.codex` and `~/.claude` skills (and Claude commands) are listed as unavailable;
  the owner's agents and Codex prompts stay available because the harness inserts their text
  itself. Claude user skills and commands are unavailable unless the opt-in and the hooks grant
  are both on, the access mode is not Read only, and the project has no catalog
  (`approval_policy.hooks_allowed` and `effective_permissions`, shared with the run, which
  looks only at the catalogs of the selected resources: the palette cannot know the selection,
  so any catalog counts). The run then reads the harness home with
  `--setting-sources user,project`. A scheduled run drops the opt-in, which the palette cannot
  know. The owner's personal resources (`~/.gemini` commands, the owner's `~/.codex` and
  `~/.claude` folders) are listed only when the caller is positively the owner
  (`owner=True` in `resources.discover`, `resolve`, `catalog.catalog` and
  `ConversationService.selected_resources`; every default is "hidden"), in the palette, in
  `/v1/catalog` and when a run resolves its selections, so a guest cannot run an owner resource
  by id. Gemini commands are expanded by the harness, so the owner sees `~/.gemini` as before.
  `/v1/integrations` lists no Codex, DeepSeek or Claude connector without the opt-in.

Not carried by the opt-in (follow-ups): Codex hook definitions and user skills, which the
CLI reads only from its own home; the owner's own Claude and Codex skills (the palette lists them as unavailable).

## Language rule (D30, rule A)

Every provider's instructions carry one line: "Answer in the language of the user's latest
message unless they ask otherwise." (Codex, DeepSeek and local developer instructions;
Claude's appended system prompt; both isolated adapters.)
