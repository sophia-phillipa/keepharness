# Model servers on your network

Use a model that runs on another machine of your tailnet or local network, through the existing `local` provider. The other machine runs any OpenAI-compatible server (llama.cpp `llama-server`, Ollama's OpenAI endpoint, LM Studio...) and the harness talks to it like it talks to a local one.

Initial scope: manual entry of the address in the admin panel, probe, persist, and expose the models of the server next to the local ones. There is no automatic discovery of tailnet machines.

## Behavior

1. In the admin panel, open **Providers**, then the **Local Model via Codex** card. The **Model server on your network** form takes an address (for example `http://machine.tailnet.ts.net:8080`) and an optional API key.
2. **Add server** asks the server for `<address>/v1/models`. Only a reachable server that answers with a model list is saved. Any failure is reported with its reason (could not connect, timed out, key rejected or required, unexpected HTTP status, not a model list, answer too large) and nothing is saved.
3. The saved servers are probed again on every inventory scan (**Check environment**, starting the harness, saving choices). Their models join the local ones in the model list; choose and save them like any other local model.
4. The list under the form shows each server with the number of models it listed, or **Unreachable** and the reason. **Remove** deletes the server and its key file after a confirmation.
5. An unreachable server never breaks a scan: it is skipped for that scan and reported as unreachable. It is probed again on the next one.

The machine must be reachable over Tailscale or the local network, and its server must listen on a network address (for `llama-server`, `--host 0.0.0.0` or the Tailscale address), not only on `127.0.0.1`.

### Address rules

The address is normalized to `scheme://host[:port]`:

| Rule | Examples |
|---|---|
| Scheme is `http` or `https` | `ftp://box` is rejected |
| A host name is required; ASCII letters, digits, `-`, `_` and dots, or an IP address (IPv6 in brackets) | `http://[fd7a:115c:a1e0::1]:8080` |
| Port is optional (1 to 65535); the default port of the scheme is dropped | `https://box:443/` becomes `https://box` |
| No user info | `http://user:pw@box` is rejected; use the key field |
| No query and no fragment | `http://box:8080?x=1` is rejected |
| Path is empty or `/v1` (it and a trailing slash are stripped) | `http://box:8080/v1/` becomes `http://box:8080`; `/v1/models` is rejected |
| Host is lowercased, surrounding whitespace removed, at most 300 characters | |

### Plain http outside the tailnet

An `http://` address travels unencrypted unless the connection is already private. These count as private: this computer (`127.0.0.0/8`, `::1`, `localhost`) and Tailscale (`100.64.0.0/10`, `fd7a:115c:a1e0::/48`, `*.ts.net` names; WireGuard encrypts them). `https://` is always accepted.

- An API key is **refused** for any other `http://` address, before any request is sent and with nothing saved: the error says the connection is unencrypted and the key would travel in plain text. Use `https://` or a Tailscale address.
- An address without a key is still accepted, and the answer carries a `warning` that prompts and answers travel unencrypted.

### Admin API

Both routes follow the existing admin rules: local-only guard, admin cookie, and the `X-Harness-Admin: 1` header.

| Route | Body | Answer |
|---|---|---|
| `POST /api/remote-model-add` | `{"url": "...", "key": "..."}` (`key` optional) | `{"url", "models": [ids], "has_key"}` plus `"warning"` for unencrypted http, or `400 {"error"}` |
| `POST /api/remote-model-remove` | `{"url": "..."}` | `{"removed": true}` or `400 {"error"}` |

Adding an address that is already saved keeps one entry and replaces its key; adding it without a key removes the old key. The audit log records `remote_model_added:<address>` and `remote_model_removed:<address>`, never the key.

## Data

- `settings.json` gets `"remote_models": [{"url": "<normalized address>"}]`. The key is omitted when the list is empty, so settings of people who do not use the feature are unchanged.
- The optional key is stored in `<state>/remote-model-<first 24 hex of sha256(address)>.key`, mode `0600`, written through a private temporary file, `fsync` and an atomic rename. The path is derived from the address, so no key reference is stored in settings.
- Settings saves and imports cannot change the list: `Manager.validate` copies the saved list and ignores any `remote_models` in the payload. Only the two routes above change it (they probe, normalize and handle the key). An exported settings file lists the addresses, never keys, and importing it does not add them.

### Runtime entries

Each model of a reachable server becomes an entry in the `local` service's `runtimes`, in the same shape the local `llama-server` entries have, so `build_local` and `adapters/local/backend.py` need no change:

```json
{"id": "qwen3:8b", "context": 32768, "runtime": "remote", "url": "http://machine.tailnet.ts.net:8080",
 "key_file": "<state>/remote-model-<hash>.key", "remote": true, "host": "machine.tailnet.ts.net"}
```

`key_file` is `""` when the server has no key. The inventory also carries `remote_servers`, one status per saved server: `url`, `host`, `reachable`, `models`, `has_key`, `error`.

## Security rules

- The key lives only in its own `0600` file. It is not in `settings.json`, `runtime.json` (only the `key_file` path is), logs, audit entries, operation output, exports or any API answer. Tests search every file under the state directory and every response for it.
- The probe uses `trust_env=False` (no proxy from the environment), `follow_redirects=False` (the key is never sent to a redirect target), a 3 second total limit, and a 256 KiB cap on the answer.
- Reasons shown to the user are fixed sentences; nothing from the remote answer is echoed except model ids that match the id shape the settings validator accepts (at most 100 per server).
- Only the local administrator can add an address (the usual admin guard), so a posted or imported payload cannot make the harness contact a new address.
- A model id is not offered by a network server when it is already served by a running local server, belongs to an installed Ollama model, may be served by a saved local profile (the panel's `managed-local` alias, the weights path and file name), or comes from an earlier saved network server: a network machine cannot take over a model you run elsewhere, even while your local server is stopped.
- The context size a server reports is used only when it is a whole number from 1 to 10,000,000.
- `/props` (read for the context size and vision, also by the model list and the image check) follows the same rules as the probe: `trust_env=False`, no redirects, a 3 second total limit, a 256 KiB cap, and a body that is not a JSON object is ignored. A slow, huge or malformed answer never delays or breaks `/v1/models`.
- At most 8 servers can be saved, so a scan stays bounded (servers are probed together).

## Limits

- A network model has no weights file on this machine, so no saved model profile: it starts with every permission off (chat only), the same default as a local model without a profile. Permission profiles for network models are a follow-up.
- Removing a server does not remove its model ids from the enabled list of the local service. Until you uncheck them, applying the configuration reports the model as unavailable, like a local server that stopped.
- `https` addresses need a certificate the system trusts.
- `/props` (vision and context size) is a llama.cpp endpoint; for other servers that read is skipped silently.
- The harness reaches the server when a turn runs, not when the model is selected; a server that goes offline fails that turn.
- A saved key is plain text in a private file, like the DeepSeek key.
