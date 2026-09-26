"""Read-only, bounded connector and plugin catalogues from installed CLIs."""

import asyncio
import json
import os
import signal
from pathlib import Path

OUTPUT_LIMIT = 8 * 1024 * 1024
# First CLI plugin discovery can take ~10 s; stay below the admin's 30 s deadline.
TIMEOUT_SECONDS = 15


async def _run(*args):
    """Run a fixed CLI command and return a bounded decoded stdout result."""
    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
        output = bytearray()
        async with asyncio.timeout(TIMEOUT_SECONDS):
            while chunk := await process.stdout.read(65536):
                remaining = OUTPUT_LIMIT - len(output)
                if remaining <= 0:
                    return 1, ""
                output.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    return 1, ""
            return await process.wait(), output.decode(errors="replace")
    except (OSError, TimeoutError):
        return 1, ""
    finally:
        if process and process.returncode is None:
            try:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGTERM)
                else:
                    process.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(process.wait(), 2)
            except asyncio.TimeoutError:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
                await process.wait()


def _plugin_identifier(plugin):
    identifier = plugin.get("pluginId") or plugin.get("id")
    if isinstance(identifier, str) and identifier:
        return identifier
    name = plugin.get("name")
    marketplace = plugin.get("marketplaceName")
    if isinstance(name, str) and isinstance(marketplace, str) and name and marketplace:
        return f"{name}@{marketplace}"
    return name if isinstance(name, str) else None


def _plugin_description(plugin):
    """Read only public descriptive fields from bounded local plugin manifests."""
    description = plugin.get("description")
    if isinstance(description, str) and description.strip():
        return description.strip()
    source = plugin.get("source")
    root = (
        source.get("path")
        if isinstance(source, dict) and source.get("source") == "local"
        else plugin.get("installPath")
    )
    if not root and plugin.get("installed") is True:
        parts = [plugin.get(key) for key in ("marketplaceName", "name", "version")]
        if all(
            isinstance(part, str)
            and part not in {"", ".", ".."}
            and "/" not in part
            and chr(92) not in part
            for part in parts
        ):
            root = str(
                Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
                / "plugins"
                / "cache"
                / Path(*parts)
            )
    if not isinstance(root, str) or not Path(root).is_absolute():
        return ""
    for directory in (".codex-plugin", ".claude-plugin"):
        path = Path(root) / directory / "plugin.json"
        try:
            with path.open("rb") as stream:
                raw = stream.read(65537)
            if len(raw) > 65536:
                continue
            manifest = json.loads(raw)
            if not isinstance(manifest, dict):
                continue
            interface = manifest.get("interface")
            interface = interface if isinstance(interface, dict) else {}
            for value in (
                interface.get("longDescription"),
                manifest.get("description"),
                interface.get("shortDescription"),
            ):
                if isinstance(value, str) and value.strip():
                    return value.strip()
        except (OSError, ValueError):
            continue
    return ""


def _plugins(payload):
    try:
        decoded = json.loads(payload)
    except (TypeError, ValueError):
        return None
    if not isinstance(decoded, dict):
        return None
    items = []
    seen = set()
    for default_status, entries in (
        ("installed", decoded.get("installed", [])),
        ("available", decoded.get("available", [])),
    ):
        if not isinstance(entries, list):
            continue
        for plugin in entries:
            if not isinstance(plugin, dict):
                continue
            identifier = _plugin_identifier(plugin)
            name = plugin.get("name") or identifier
            if (
                not isinstance(identifier, str)
                or not identifier
                or not isinstance(name, str)
                or identifier in seen
            ):
                continue
            seen.add(identifier)
            installed = plugin.get("installed") is True
            description = _plugin_description(
                {**plugin, "installed": installed or default_status == "installed"}
            )
            items.append(
                {
                    "id": f"plugin:{identifier}",
                    "name": name,
                    "kind": "plugin",
                    "status": "installed"
                    if installed or default_status == "installed"
                    else "available",
                    "enabled": plugin.get("enabled") is True,
                    **({"description": description} if description else {}),
                }
            )
    return items


def _codex_mcp(payload):
    try:
        decoded = json.loads(payload)
    except (TypeError, ValueError):
        return None
    if not isinstance(decoded, list):
        return None
    items = []
    for server in decoded:
        name = server.get("name") if isinstance(server, dict) else None
        if isinstance(name, str) and name:
            items.append(
                {
                    "id": f"mcp:{name}",
                    "name": name,
                    "kind": "mcp",
                    "status": "configured",
                    "enabled": server.get("enabled") is True,
                }
            )
    return items


async def installed_plugins(binary):
    """Return authoritative installed metadata, or None when discovery failed."""
    code, output = await _run(binary, "plugin", "list", "--json")
    try:
        decoded = json.loads(output)
    except (TypeError, ValueError):
        return None
    if code or not isinstance(decoded, dict) or not isinstance(decoded.get("installed"), list):
        return None
    return _plugins(json.dumps({"installed": decoded["installed"]}))


def _claude_mcp(payload):
    """Claude has no JSON `mcp list` output in the observed version."""
    if payload.strip().startswith("No MCP servers configured"):
        return []
    items = []
    for line in payload.splitlines():
        name, separator, _rest = line.partition(":")
        name = name.strip()
        if (
            separator
            and name
            and all(character.isalnum() or character in "_.-" for character in name)
        ):
            items.append({"id": f"mcp:{name}", "name": name, "kind": "mcp", "status": "configured"})
    return items or None


def _fallback(provider, fallback):
    if fallback is None:
        # The metadata reader is itself bounded to configured local files and
        # returns no command or environment details.
        from .integrations import inventory

        fallback = inventory()
    entries = fallback.get(provider, []) if isinstance(fallback, dict) else []
    return [item for item in entries if isinstance(item, dict) and item.get("kind") == "mcp"]


async def catalog(provider, binary, fallback=None):
    """Return the known provider catalogue without commands, credentials, or stderr."""
    if provider not in {"codex", "claude"}:
        return {"items": [], "warnings": ["Catalog not supported for this provider."]}
    if not isinstance(binary, str) or not binary:
        return {"items": [], "warnings": ["The provider's CLI was not found."]}

    plugin_command = (binary, "plugin", "list", "--available", "--json")
    mcp_command = (
        (binary, "mcp", "list", "--json") if provider == "codex" else (binary, "mcp", "list")
    )
    mcp_result, plugin_result = await asyncio.gather(_run(*mcp_command), _run(*plugin_command))
    mcp_code, mcp_output = mcp_result
    plugin_code, plugin_output = plugin_result
    mcp_items = _codex_mcp(mcp_output) if provider == "codex" else _claude_mcp(mcp_output)
    plugin_items = _plugins(plugin_output)
    warnings = []
    if mcp_code != 0 or mcp_items is None:
        mcp_items = _fallback(provider, fallback)
        warnings.append(
            "Could not read the CLI's MCP catalog; already configured connectors were used."
        )
    if plugin_code != 0 or plugin_items is None:
        plugin_items = []
        warnings.append("Could not read the CLI's plugin catalog.")
    return {"items": mcp_items + plugin_items, "warnings": warnings}
