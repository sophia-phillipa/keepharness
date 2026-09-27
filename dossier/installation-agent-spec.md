# AI-conducted Tail Harness installation

Status: operational specification. Audience: an AI agent with authorized server access and the person responsible for the installation. This is not a new release nor proof of an executed installation.

## Objective and contract

Install Tail Harness, reuse already-available CLIs, accounts, tokens and model servers, and deliver usable administration and conversation, with tests and evidence. **The installer is the AI agent:** it inspects, explains, runs individual commands, checks results and resolves decisions with the person, because the harness depends on the AI providers already installed on each server. Do not use `install.sh`, `setup.sh` or `tail-harness-install` as substitutes for this procedure (they are the manual path described in the README); run the equivalent commands individually so each checkpoint has its own evidence. Test scripts remain allowed.

Read the README (both language sections), `pyproject.toml` and the specs of the adapters used. Confirm the commands against the installed revision.

### Execution rules

- Before each phase, state its emoji, CP identifier, what you will do and why; when done, report the result, the evidence and the next step. Follow the progress format below. Never silence a blocker or announce success just because a process started.
- Ask questions only when a necessary decision is missing. Reuse previous answers and authorizations. Continue independent tasks while waiting; do not choose an alternative port or a widened access on the user's behalf.
- Do not read token values into the conversation, logs or the report. Reuse the provider's private mechanisms, under the correct user. The presence of a file does not prove authentication.
- Preserve the checkout, state, credentials, models, existing services and running jobs. Take a consistent private backup before changing existing state; for an active SQLite database, use a transactional backup, not an isolated file copy.
- Do not install CLIs, download weights, switch runtime, enable integrations, publish to the network, or widen permissions just because they were discovered. Execute the already-authorized scope; ask about additional choices only when needed.
- Each checkpoint receives `APPROVED`, `BLOCKED` or `NOT APPLICABLE`, with evidence, time and reason. A failure in one provider may allow continuing with the others, but prevents declaring that provider ready. Absence of evidence is not equivalent to approval.

## Mandatory visible progress

Use the same identifiers and emoji as the README: 🔎 CP-01 diagnosis, 🧭 CP-02 choices, 📦 CP-03 preparation, 🔌 CP-04 inventory, ⚙️ CP-05 configuration, 🧪 CP-06 validation, 🛠️ CP-07 recovery and 📋 CP-08 delivery. Present the roadmap at the start and update it as results are observed. The numbering identifies checkpoints, not a percentage or an estimated duration.

Before executing, post a short message in this format:

> 📦 CP-03 · Preparation — ⏳ IN PROGRESS
>
> I will install the checkout into the selected Python environment and check the dependencies to start administration. I will check the return of each command before proceeding.

When closing a phase, use `✅ APPROVED`, `⛔ BLOCKED` or `➖ NOT APPLICABLE`, along with the action executed, the observed result, sanitized evidence and the next step. Example format, to be filled with real results:

```text
📦 CP-03 · Preparation — <status>
Executed: <command or action with effective paths, no secrets>
Result/evidence: <observation, time and local reference>
Next step: <next checkpoint or action to resolve the blocker>
```

For long-running operations, explain which command is still running and what remains to be checked; do not invent progress or completion. If a failure occurs, announce 🛠️ CP-07, describe the diagnosis/repair within scope and resume the affected phase after retesting. When there is no failure to handle, record CP-07 as `➖ NOT APPLICABLE`, with that reason. Conditional phases and optional resources also need a justification; a requirement that failed stays blocked.

**Communication acceptance:** every phase has an emoji, an identifier and a text description; every closed checkpoint has a status, evidence and a next step. A partial installation ends with explicit blockers. Sample messages in this spec are not evidence of execution.

## Clean installation simulation

Before installing over an existing instance, prefer a private temporary directory, a fresh venv, empty state and free ports of its own. Test an out-of-checkout installed package first, then the editable flow. Do not register a service, shortcut, linger or VPN route during this simulation.

`--state` separates settings/history, but **it is not a server sandbox**: discovery still queries the user's profiles and already-active processes/models. Also set `TAIL_HARNESS_ROOT` to a dedicated temporary folder if there are operations with local resources. Only query metadata from existing profiles; do not perform login, plugin install/removal or changes to shared runtimes. If credential/process isolation is needed, use a dedicated user or container, recognizing that this changes what will be discovered.

In a real local test, enable only the chosen model on the temporary instance and restrict access with a synthetic project. **This simulation cannot restrict network, hooks or integrations for native Codex/Claude:** those providers are native-only, and `Manager.validate` forces every stored permission (read/write/upload/tests/internet/shell/hooks) to `true` for them regardless of the submitted payload (finding F-42) — there is no per-provider network or hooks toggle to withhold. What the simulation *can* restrict for native providers is per-conversation: the access mode (Read only / Ask for approval / Automatic / Full access, per [conversation-execution-mode.md](conversation-execution-mode.md) and the F-110 approval guarantees) and, where the provider supports it, an isolated conversation instead of a native one. Grant the synthetic project and pick the most restrictive access mode that still exercises the scenario; do not treat a permissions payload of all-`false` as achievable or as a test failure when the server persists `true`. Saving an enabled model can start the harness process immediately (the admin restarts it on save): check ports and reachability right after saving, not only before. Wait for runtime availability, do not compete with someone else's inference, impose a deadline and cancel only your own job when it is exceeded. When done, stop only the simulation's processes and check the production listeners. Preserve the report and evidence, noting that directories under `/tmp` are temporary.

## 🔎 CP-01 — Identify the server and preserve the installation

1. Confirm the effective user, system, architecture, checkout/revision and local changes. Record whether this is a first installation, an update or a resumption.
2. Check Python 3.11+, venv/pip, disk space, RAM, Node/Chromium when needed for tests, and access to dependencies. GPU is optional; do not load models just to measure availability.
3. Identify the service, process, Python environment, state directory and ports actually in use. Compare the terminal's environment to the service's, including PATH and access to the CLIs' profiles.
4. Default to the `~/.local/share/tail-harness` state and a permanent checkout. Inventory `settings.json`, `local-profiles.json`, the existing database and attachments, without exposing private content.
5. Locate code with Graphify/rg before broad reads. Implementation references are at the end of this spec.

**Acceptance:** unambiguous destination and user; prerequisites met or gaps identified; prior state preserved. Do not install a second instance by failing to recognize the first one.

## 🧭 CP-02 — Resolve ports and required choices

Check listeners and their processes before starting any service. Defaults: administration `8094`, conversation `8095`; `8096` is only relevant if a managed local runtime is requested. An existing Ollama typically uses `11434`; discover the real address. Administration stays on `127.0.0.1`.

If a port is busy, find out whether it belongs to this same installation. Reuse the correct instance when applicable. For a conflict with another service, present verified free ports and ask, for example: **"Port 8094 is in use by another service. Which port do you want to use for administration? Port 8097 is free right now."** Wait for the choice, check again before binding, and update the service, shortcuts, URLs and tests. Do not stop the other process.

Resolve only the remaining unknown choices: desired providers/models, authorized projects, permissions, manual/on-login/before-login startup and local/VPN access. If there is an external runtime, confirm reuse without migrating it into the internal directory on your own initiative.

**Acceptance:** valid distinct ports, between 1024 and 65535, for administration/conversation, and recorded decisions. Existing VPN routes must be handled before changing their ports, per the panel's validation.

## 📦 CP-03 — Install dependencies and start administration

The agent runs each command, inspects the return and only then proceeds. Example for a checkout already obtained from the authorized source, with adjusted variables and absolute paths:

```sh
TH_CHECKOUT=/absolute/path/tail-harness
TH_VENV="$HOME/.local/share/tail-harness-venv"
TH_STATE="$HOME/.local/share/tail-harness"
TH_ADMIN_PORT=8094
python3 -m venv "$TH_VENV"
"$TH_VENV/bin/python" -m pip install -e "$TH_CHECKOUT[test]"
"$TH_VENV/bin/python" -m pip check
"$TH_VENV/bin/python" -m control --port "$TH_ADMIN_PORT" --state "$TH_STATE"
```

The venv lives at `~/.local/share/tail-harness-venv`, a sibling of the state directory, never inside it: the admin only forces `0700` on the state directory when it creates it (`control/manager.py`), so a `python3 -m venv` run inside an existing `TH_STATE` leaves that directory at the umask's mode (typically `0755`) and world-listable. See [the naming model](naming-model.md) for the `TAIL_HARNESS_VENV`/legacy `TH_VENV` alias.

Do not blindly recreate an existing environment. The last command stays in the foreground: follow it in a managed session or use the approved unit below. An editable install depends on the checkout staying at that path. Do not routinely run a wheel install and an editable one at the same time.

For Linux/systemd, if automatic startup is part of the scope, the agent writes and reviews the user unit individually, using `control/install.py:files` as a reference, without running the installer. It must contain the absolute Python path, `-m control`, the chosen port and state, a PATH that finds the real CLIs, `UMask=0077` and a restart policy. Validate with `systemd-analyze --user verify UNIT_PATH`, reload the manager, and enable/start only the target unit. Do not start it over the manual process on the same port. A desktop shortcut is optional on a server with no graphical session; when applicable, it must open the effective URL and start the service, without reinstalling anything.

> ⚠️ **Run `systemd-analyze --user verify` on the HOST, never inside a distrobox/toolbox.** A container that shares the host's `/run/user/<uid>` (the default for distrobox/toolbox) replaces the host user-manager's private socket the moment `systemd-analyze --user verify` runs inside it, even though the command itself only renders and checks a unit. The host's own `systemd --user` instance keeps running, but every subsequent host `systemctl --user …` fails with "Failed to connect to user scope bus via local transport: Connection refused" until repaired. This has been reproduced (finding F-40): the container run's `exec_died` timestamp lines up exactly with the socket inode being replaced.
>
> - Run the verify step with `distrobox-host-exec systemd-analyze --user verify UNIT_PATH` (or directly on the host, never `container exec`) when the agent's own shell is inside a container.
> - If a host `systemctl --user` command already fails with "Connection refused" but `busctl --user status` still works and user services keep running, recover with `busctl --user call org.freedesktop.systemd1 /org/freedesktop/systemd1 org.freedesktop.systemd1.Manager Reexecute` (re-creates the socket without restarting units), or have the person log out and back in. Confirm the repair with `systemctl --user is-active` on an unrelated existing unit before resuming CP-03.

Linger only when startup before login was requested; a system authorization failure leaves this step blocked. Without systemd, record the manual mode or chosen supervisor and its limitations, without declaring autostart validated.

Open `/` and check assets and `/api/state` in the same HTTP session: the home page establishes the administrative cookie. Do not work around this protection when facing a 401.

**Acceptance:** the package is importable by the installed Python, dependencies are consistent, administration responds, and it runs the expected checkout/state. The service and shortcut, when requested, point to the correct installation.

## 🔌 CP-04 — Discover models and resources from every provider

State explicitly: **"I will read the models available on the servers and the resources already configured in the CLIs to fill in the provider panels. This reading does not enable new permissions."**

Do a complete scan of the categories below across every supported provider, under the service's effective user/profile. Complement automatic discovery with official, read-only queries available in the installed version. "Complete" means covering each category and recording limits, errors and pagination; it does not mean indiscriminately scanning personal files, extracting secrets, or promising to discover resources the CLI does not expose.

| Category | Gather and check |
| --- | --- |
| Provider and engine | Binary, version, profile, verifiable authentication, adapter and `adapter_spec_revision`; distinguish inference from tool/session execution. |
| Models | IDs, listing source, local endpoint when present, declared efforts/capabilities and observed availability. An alias or a weight file does not prove a usable model. |
| Features | Streaming, continuity, cancellation, tools, approvals, modalities/attachments, quotas and isolation; mark as unknown when not confirmed. |
| Connectors | MCP stdio/HTTP, account apps when exposed, IDs, global/project scope, configured/enabled, known authentication and permissions. |
| Plugins and extensions | Installed, enabled and merely available to install, listed separately; do not install catalog entries during the reading. |
| Skills, hooks and artifacts (via CLI only) | The admin panel does not represent skills or hooks; do not ask the person for a panel-visible matrix of them. Gather this category only through the provider CLI's own read-only listing when one exists (for example `claude` skill/plugin listings), record names/IDs and the ability to produce/fetch artifacts, and report it as CLI-sourced context outside the panel inventory. Do not import conversation content, files or credentials as inventory. |
| Projects and access | Already-authorized roots and permissions, uploads, network and per-model selection; a discovered project is not an authorized project. |

Current sources: `python -m control --scan`, the panel inventory (`GET /api/state`), account/model check (`POST /api/check`, with `provider`) and the catalog (`POST /api/integration-catalog`, with `provider`). Administrative write requests require the cookie obtained from `/` and `X-Harness-Admin: 1`, plus a valid origin. Codex uses the CLI's model listing; Claude presents aliases whose availability depends on the account. For Gemini and DeepSeek, follow their adapters. Discover llama.cpp processes/servers and query local models; Ollama cloud must not be labeled as local inference. An endpoint that lists models still needs to satisfy the adapter's execution contract.

`claude mcp list` (behind the Claude catalog call above) is not a passive read: it spawns each configured stdio MCP server and contacts remote endpoints to report their health, so this "read-only" discovery step has real side effects (network calls, short-lived child processes) worth naming to the person before running it, and its output also includes `claude.ai <Name>` account-app connector lines (Gmail, Google Drive, …) alongside the `mcp:*` entries; record their reported status (Connected / Needs authentication / Failed) as part of the connector inventory rather than discarding lines that do not look like an MCP server name.

For Codex/Claude catalogs, the code uses `codex mcp list --json`, `claude mcp list` and `plugin list --available --json` on each CLI. Confirm support via the installed version/help. An empty MCP list is a valid result; an error or unknown output is not an empty list. The current expanded catalog covers Codex/Claude; Gemini has discovery of configured MCP. Local and DeepSeek share the Codex engine's ecosystem, which does not by itself prove every resource is compatible with those models.

**Acceptance:** a per-provider matrix covering every category, with source, state and gaps. Summarize the found models to the user before configuring the selection. Distinguish `discovered`, `configured`, `enabled`, `authenticated`, `tested` and `unsupported`.

## ⚙️ CP-05 — Fill in and check the panels

1. Fill in the cards through the supported configuration mechanisms, reusing real IDs and icons. Identify **Local model via Codex** and **DeepSeek via Codex** when those are the effective combinations; do not announce alternative engines that are not yet implemented.
2. Present the models and resources already found and enabled in the source profile. Reconcile each inventory item with the provider panel: ID, state, selection and origin. Detected does not mean authorized for every Tail Harness task.
3. The installation defaults to discovering and activating resources that are already installed/configured, authenticated and supported by Tail Harness, reusing already-authorized permissions and projects. In routine updates, preserve an explicit deactivation made by the person. A present credential does not prove authentication; do not perform interactive login, install CLIs, download models, widen access, or trigger paid inference to complete the matrix. Keep reading the inventory separate from writing the selection. Saving an enabled model can start the harness: check ports and reach before saving.
4. Explicitly record resources the panel does not represent. Do not invent fields in `settings.json`, nor claim an artifact/skill is in the panel just because it exists on the server. For a required resource with no support, mark the checkpoint blocked and describe the needed implementation; for an optional one, record the accepted limitation.
5. After saving, wait for the effective configuration: check `config_revision` and the absence of `config_reload_error` in `/v1/version`, plus the expected model, project and permissions in `/v1/models?project_id=ID`. The administrative save can precede the conversation process's reload; do not submit a task before this convergence. Reopen/reload the panel and compare the persisted configuration with the matrix. Verify names, icons, model, engine, selected integrations and projects actually available in the conversation.

**Acceptance:** no known resource silently omitted; representable items are on the correct cards, and gaps have explicit handling. At least one authorized combination must work for operational delivery.

As an installation default, classify separately **cataloged**, **installed/configured**, **credential present**, **authenticated**, **enabled** and **tested**. Activate everything that is installed/configured, authenticated and has an implemented contract, without changing the project list or permissions. A catalog entry without installation stays available for browsing, not installed; a resource without authentication stays pending login; an unsupported resource stays marked unsupported. Configured connectors can only be selected when compatible with the current provider/engine and staying within the already-granted scope. A catalog or configuration entry does not prove real operation; mark it tested only after a minimal, authorized, read-only call.

## 🧪 CP-06 — Mandatory post-installation tests

Run tests targeted at the installation and the selected contracts, using the commands in the README's Development section. Fixtures do not prove real account access. Real tests must use synthetic data, an authorized temporary project and a short budget, with no GPU benchmarks or unauthorized paid inference.

| Test | Procedure and required result |
| --- | --- |
| Installed package | Run `"$TH_VENV/bin/python" -m control.install_check`. It always runs in a fresh temporary directory with a free port, and must pass with valid HTTP/assets/API and no enabled provider — this validates process startup and the API/asset surface, not where the code is imported from. For a wheel install it also confirms the import resolves outside the checkout; for an editable install (`pip install -e`) the import still resolves back into the checkout by design (that is what editable means), so a pass there does not certify import isolation, only startup and the API/asset surface. Does not replace testing the final instance. |
| Final administration | Open the chosen URL; check `/api/state`, static files, absence of a loading error, and correct state/version. Preserve the cookie/origin across requests. |
| Discovery and panel | Compare the matrix, catalog and cards after a reload. Test an empty list and a failed query without confusing them; examine messages and persistence. |
| Conversation readiness | Wait for a full connection: models, projects, permissions and history loaded. Check `/v1/models` and `/v1/projects` in the authorized session; an HTTP 200 on the home page is not enough. |
| Real execution per enabled combination | Send a short task to each enabled model/engine combination: answer with a marker, read a synthetic file via a tool and produce a small result in the test project. Check the terminal state, a non-empty response, the effective model and the absence of a hidden fallback. Without authorization/quota, record a blocker, not an approval by fixture. |
| Continuity and artifact | Continue the same task using an earlier synthetic piece of information; reload and check the history. Open/download the result and check its content. Attachments/modalities are approved only after testing the supported format. |
| Permissions and cancellation | In a synthetic scenario, check approval/denial per policy and cancellation of one's own job. Do not grant additional access just to make the test pass. Dangerous negative tests stay in isolated fixtures. |
| Enabled connector/plugin | When allowed, perform one minimal read-only operation, with scope and test data; check real execution. A listed configuration with no successful call is marked untested. |
| Restart and recovery | With no active jobs and within authorization, restart only Tail Harness; check the return of administration, configured resumption, settings/history and reconnection. Do not stop a shared runtime. A real boot can only be declared tested after actual observation. |
| VPN, if requested | Test an authorized client, blocking of an unauthorized client, and administration being unreachable externally. Do not open a public firewall or enable sharing for convenience. |

Targeted regressions available in this revision (confirm the files before running them):

```sh
"$TH_VENV/bin/python" -m pytest -q tests/test_distribution.py tests/test_integration_catalog.py
node tests/admin-integrations.spec.cjs
```

The first command validates distribution, startup and catalog, not the whole installation. `tests/admin-integrations.spec.cjs` runs entirely against a mocked `admin.test` fixture and does not talk to the installed instance at all — the `ADMIN_URL`/`HARNESS_URL` environment variables some CI setups export for it are not read by this spec. A green result here is evidence for the connector-selection UI logic, never for CP-04's real discovery against this server; do not cite it as installed-instance evidence in the CP-08 report. Run pytest in its default mode: some tests import helpers from other files under `tests/`; `--import-mode=importlib` without adjusting those imports breaks collection. When checking a wheel, run it outside the checkout so as not to accidentally import the source instead of the package. The UI test requires Node, Playwright and Chromium; set `PLAYWRIGHT_MODULE` if needed. Select additional tests for the adapter and permissions actually configured. For browser cases that depend on a server, read the fixture and start separate test servers. `scripts/test-ui.sh` runs the entire UI regression and reserves 18094/18095: use it only at the appropriate milestone and check for conflicts beforehand. A missing dependency or inability to open a browser is a blocked test, not a passed one.

**Acceptance:** results recorded per test and combination; no open required failures. Package success, fixture success and end-to-end success are different kinds of evidence.

## 🛠️ CP-07 — Handle failures and resume

Before changing anything, collect the sanitized error, time, command, version, effective process/port and user, and state. Check the unit's logs (`journalctl --user -u tail-harness -n 50`) or the manual session's. Avoid environment dumps and configs with secrets.

| Failure | Agent action |
| --- | --- |
| Port in use | Identify the occupant, ask for an alternative, check and update references. Never kill the occupant by default. |
| CLI not found by the service | Compare PATH/binary/user against the terminal and fix the unit or supported configuration; do not automatically reinstall the CLI. |
| Invalid account/token | Check the credential mechanism and the user's access; guide them to the official flow or private provisioning. Do not ask for a token in the conversation or silently swap identity. |
| Model listed, execution fails | Check the adapter revision, protocol, endpoint, tool capability, quota and the concrete error. Do not switch to another provider/model without an applicable decision. |
| Resource missing from the panel | Distinguish incomplete discovery, unsupported, and a UI failure; record the ID and source. Do not enable it through ad hoc editing or declare the integration complete. |
| HTTP 401/403 or a blocked interface | Check the session/cookie, origin, identity and permissions; do not disable controls. |
| Dependency, service or test failure | Fix the demonstrated cause, rerun the affected test and record before/after. If the same failure reappears, review the hypothesis; do not repeat indefinitely. |

Reversible fixes within scope are the agent's responsibility. A destructive change, a new identity, unauthorized new spend/access, or a pending user choice requires clarification. Before restoring a backup, check for later work/data so nothing is lost. A rollback affects only files/processes created or changed during this installation, never someone else's credentials or services.

**Acceptance:** failures fixed with a retest, or blockers documented with a concrete next action. When resuming, check the real state and reuse checkpoints that are still valid; changes invalidate only the affected evidence.

## 📋 CP-08 — Delivery and completion criteria

Deliver a sanitized report outside version control, in an agreed location, with:

- installed revision, user, checkout, Python, state, ports, URLs and startup mode;
- the matrix of models/engines, features, connectors, plugins and artifacts, including panel gaps;
- the user's decisions and applied permissions, with no credential values;
- checkpoints and tests: command/action, expected, observed, time, result and local evidence;
- fixes made, untested resources, limitations and resume/rollback instructions.

In the final installation message, explain **how to open it, how to start/stop it, how to rerun the short tests and where to diagnose errors**. Use effective paths and ports, not placeholders. State which tests you, the installing agent, ran; do not hand the user tests you could have run yourself. When interactive login, a decision or an authorization is still missing, state exactly what is pending.

Only declare **"installation completed and tested"** when the required checkpoints pass, at least one combination is operational, and every required resource in scope is validated. Otherwise, deliver **"partial installation — blockers: …"**. Optional, not-applicable resources need a justification; do not turn failures into "not applicable".

## Implementation references

- [Entrypoint and ports](../control/cli.py), [dependencies](../pyproject.toml), [legacy unit and shortcut model](../control/install.py).
- [Discovery](../control/discovery.py), [integration inventory](../control/integrations.py), [CLI catalog](../control/integration_catalog.py).
- [Administration and configuration validation](../control/manager.py), [adapter contracts](../adapters/README.md).
- [Installed-package smoke test](../control/install_check.py), [UI regression](../scripts/test-ui.sh).

This specification describes the work the agent must perform on each installation. Reviewing this document does not certify an installation, account, model or connector on this server.
