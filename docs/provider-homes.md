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
- `/v1/resources` lists user-scope agents from the owner's folders; without the opt-in it
  lists those of the harness home (none by default). `/v1/integrations` lists no Codex,
  DeepSeek or Claude connector without the opt-in.
- The "/" palette offers only what the CLI will load. User-scope Claude skills and commands
  are listed but unavailable (Claude runs read skills from the project only), the owner's
  Codex skills are unavailable too, and DeepSeek lists its own home (`providers/deepseek` and
  `providers/home/.agents/skills`), never the Codex one.

Not carried by the opt-in (follow-ups): Codex hook definitions and user skills, which the
CLI reads only from its own home; Claude user skills (WP-09 materializes or marks them).

## Language rule (D30, rule A)

Every provider's instructions carry one line: "Answer in the language of the user's latest
message unless they ask otherwise." (Codex, DeepSeek and local developer instructions;
Claude's appended system prompt; both isolated adapters.)
