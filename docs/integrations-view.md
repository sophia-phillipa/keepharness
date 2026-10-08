# Integrations view

`GET /v1/integrations` tells the user, for one project and one route, which connectors
(MCP servers) and plugins are installed, allowed, effective for the run and used
recently. It is read-only: it changes no setting, starts no provider CLI and never
returns a command, argument, URL, header or environment value from a provider profile.
It backs a Plugins chip in the composer (the chip itself is not part of this change).

## Request

`GET /v1/integrations?project_id=&backend=&model=&execution_mode=&access_mode=`

Access and validation match `GET /v1/resources`: the caller needs the project, the
provider service must be enabled for it, and the model must be allowed. The removed
`maestro` backend is refused with `backend_unavailable`. `execution_mode` defaults to the provider's default and is
validated the same way; `access_mode` is one of `ask` (default), `auto`, `full`,
`read_only`, otherwise `invalid_access_mode`. The response is sent with
`Cache-Control: no-store`.

## Response

```json
{
  "backend": "claude",
  "execution_mode": "native",
  "access_mode": "ask",
  "effective_note": "Availability and approvals follow the native CLI configuration and selected access mode.",
  "items": [
    {
      "id": "mcp:github",
      "kind": "mcp",
      "name": "github",
      "transport": "http",
      "status": "configured",
      "allowed": null,
      "effective": null,
      "reason": "Availability and approvals follow the native CLI configuration and selected access mode.",
      "used": {"count": 3, "last_used": 1791000000.0, "tools": ["create_issue"]}
    }
  ],
  "other_tools": [{"name": "exec_command", "count": 12, "last_used": 1791000000.0}],
  "elsewhere": [],
  "window_days": 30,
  "warnings": []
}
```

- `items` is the provider's inventory, connectors first, then plugins, each sorted by
  name. `transport` is `http` or `stdio` for connectors and `null` for plugins. Servers
  named `harness_effects*` are the harness's own and are never listed. Native Codex
  and Claude list their configured CLI inventory without a personal-setup opt-in.
  DeepSeek retains its personal-setup boundary; see [provider-homes.md](provider-homes.md).
- For native Codex and Claude, `allowed` and `effective` are `null`: the inventory
  establishes configuration, not whether the CLI will enable or approve an item.
  `reason` and `effective_note` explain that the native CLI and selected access mode
  govern availability and approvals. The UI labels these items "Configured in the
  native CLI" rather than claiming they are available or disabled. Read only does
  not apply a KeepHarness integrations filter, and remote Codex apps are not hidden.
- For other routes, `allowed` is the provider-level list in Settings
  (`services.<provider>.integrations`); `effective` is `allowed` and usable on the
  route. When false, `reason` says why. These routes retain their existing rules:

  | Route | Result |
  | --- | --- |
  | Isolated (`scoped`) conversation or the local provider | Nothing is effective: no host connectors or plugins. |
  | A caller other than the owner on this computer (a tailnet login) | Nothing is effective: host connectors run only for the owner. |
  | Gemini with `read_only` | Nothing is effective: connectors are turned off. |
  | DeepSeek with `read_only` | Nothing is effective: host connectors and plugins are turned off. |
  | Gemini without the `internet` permission (provider or project grant) | Allowed connectors are not effective. |
  | Other-provider item not in the allowed list | Not effective: change it in Settings. |

- Outside native Codex and Claude, `effective_note` retains the adapter-specific
  approval explanation: DeepSeek in `ask` and `auto` asks for every connector call;
  it runs them without asking
  when the adapter is unrestricted, the access mode is `full` and the shell is granted; the
  isolated sentence for isolated routes; otherwise empty.
- Native Codex and Claude return no speculative `elsewhere` enable suggestions;
  their unknown availability is not advertised as effective for another route.
- For other providers, `elsewhere` lists tools (matched by `key`: name without `mcp:`/`plugin:` prefix and
  `@marketplace`, lowercase, `_`/space as `-`) that another provider the project may use (enabled, project and models granted) has installed and
  allowed while this provider has them installed but not allowed (`here: "enable"`) or not
  installed (`here: "absent"`). Providers sharing one inventory (Codex, DeepSeek) are not
  "elsewhere" for each other, and the scoped-only `local` provider is never a source. Only the
  owner gets entries (guests get `[]`). Up to 50 entries; computed even when the route is blocked.
- `used` and `other_tools` count `tool_start` events of this provider's runs by the
  caller in this project over the last `window_days`. Claude names tools
  `mcp__<server>__<tool>`, so those are attributed to `mcp:<server>` (count, latest
  time, up to five tool names). Codex and Gemini tool names carry no server, so their
  usage appears only under `other_tools` (top 20 by count).
- `warnings` lists problems reading the inventory. An unreadable provider profile gives
  `items: []` and a warning, never an error.

For DeepSeek the plugin list comes from the runtime's `plugin_inventory` when
present (the installed plugin list the adapter toggles), otherwise from the profile.
Native Codex uses its CLI inventory without that legacy override.

## Where it lives

- `agent_service/integrations_view.py`: the pure view (`route_limits`, `build`).
- `ConversationService.integration_view`: access checks, shared with `resource_catalog`
  through `_resolve_route`, and the usage read.
- `MessageRepository.tool_usage`: one grouped query that walks the caller's own jobs
  (only those created since one day before the window starts, because a run takes hours
  at most) and probes events through the `(job, type, time)` index.
- `agent_service/routes/projects.py`: the `/v1/integrations` route.
- `tests/test_integrations_view.py`: contract, secrets, route matrix, usage and failures.
