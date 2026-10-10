"""Read-only, bounded connector and plugin catalogues from installed CLIs."""

import asyncio
import ipaddress
import json
import os
import re
import signal
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

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


def _plugin_metadata(plugin):
    """Return bounded public display metadata, never arbitrary manifest fields."""
    metadata = {}
    marketplace = plugin.get("marketplaceName") or plugin.get("marketplace")
    for key, value in (("marketplace", marketplace), ("version", plugin.get("version"))):
        if isinstance(value, str) and value.strip():
            metadata[key] = value.strip()[:200]
    developer = plugin.get("developer") or plugin.get("author")
    if isinstance(developer, dict):
        developer = developer.get("name")
    if isinstance(developer, str) and developer.strip():
        metadata["developer"] = developer.strip()[:200]
    source = plugin.get("homepage") or plugin.get("repository")
    if isinstance(source, dict):
        source = source.get("url")
    if isinstance(source, str) and (source := public_source(source)):
        metadata["source"] = source
    for key in ("apps", "skills"):
        values = []
        for entry in plugin.get(key, []) if isinstance(plugin.get(key), list) else []:
            value = entry.get("name") if isinstance(entry, dict) else entry
            if isinstance(value, str) and value.strip():
                values.append(value.strip()[:200])
            if len(values) == 50:
                break
        if values:
            metadata[key] = values
    return metadata


def public_source(value):
    """Return a public HTTP(S) source without credentials or request-specific data."""
    # urlsplit silently removes some controls; reject them before parsing.
    if (
        any(
            character.isspace() or ord(character) < 32 or ord(character) == 127
            for character in value
        )
        or "\\" in value
    ):
        return ""
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        port = parsed.port
    except (TypeError, ValueError):
        return ""
    if parsed.scheme not in {"http", "https"} or not host:
        return ""
    # Validate the complete authority: encoded hosts, zones, empty ports and
    # bracket suffixes must not acquire a different meaning in the browser.
    authority = parsed.netloc.rsplit("@", 1)[-1]
    if not re.fullmatch(r"(?:\[[0-9a-fA-F:.]+\]|[A-Za-z0-9.-]+)(?::[0-9]+)?", authority):
        return ""
    normalized_host = host.removesuffix(".").lower()
    try:
        address = ipaddress.ip_address(normalized_host)
    except ValueError:
        labels = normalized_host.split(".")
        if (
            len(normalized_host) > 253
            or len(labels) < 2
            or any(
                not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels
            )
            or normalized_host.endswith((".localhost", ".local", ".internal", ".lan"))
            # Browsers treat a numeric final label as an IPv4 address attempt.
            or re.fullmatch(r"(?:[0-9]+|0x[0-9a-f]*)", labels[-1])
        ):
            return ""
        netloc = normalized_host
    else:
        if not address.is_global or address.is_multicast:
            return ""
        netloc = f"[{address.compressed}]" if address.version == 6 else address.compressed
    if port is not None:
        netloc += f":{port}"
    source = urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))
    return source if len(source) <= 500 else ""


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
                    **_plugin_metadata(plugin),
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
        name, separator, rest = line.partition(":")
        name = name.strip()
        if separator and name.startswith("claude.ai ") and name[10:].strip():
            # Claude account connectors: shown with their health, not selectable.
            marker = re.search(r" - ([\u2714\u2718!]) ", rest + " ")
            status = {"\u2714": "connected", "!": "needs_authentication", "\u2718": "failed"}
            items.append(
                {
                    "id": f"account-app:{name}",
                    "name": name,
                    "kind": "account-app",
                    "status": status[marker.group(1)] if marker else "unknown",
                }
            )
        elif (
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


HELP_LIMIT = 64 * 1024
SUBCOMMAND_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")
# (provider, realpath, st_size, st_mtime_ns) -> capabilities read from the help pages.
CAPABILITY_MEMO: dict[tuple[str, str, int, int], frozenset[str]] = {}


def parse_subcommands(text: str) -> frozenset[str]:
    """Return the names of the first `Commands:` table of a commander or clap help page."""
    if len(text.encode(errors="replace")) > HELP_LIMIT:
        return frozenset()
    names: set[str] = set()
    in_table = False
    indent: int | None = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if not in_table:
            in_table = line == "Commands:"
            continue
        if not line:
            break
        depth = len(line) - len(line.lstrip(" "))
        if indent is None:
            indent = depth
        if depth < indent:
            break
        if depth == indent:
            names.update(
                name for name in line.split()[0].split("|") if SUBCOMMAND_NAME.fullmatch(name)
            )
    return frozenset(names)


def _memo_key(provider: str, binary: str) -> tuple[str, str, int, int]:
    status = os.stat(binary)
    return (provider, os.path.realpath(binary), status.st_size, status.st_mtime_ns)


def capability_cached(provider: str, binary: str) -> frozenset[str] | None:
    """Return the memoized capabilities of this binary version; never runs a command."""
    try:
        return CAPABILITY_MEMO.get(_memo_key(provider, binary))
    except OSError:
        return None


async def capabilities(provider: str, binary: str) -> frozenset[str] | None:
    """Probe the help pages once per binary version; None when a page is unreadable."""
    try:
        key = _memo_key(provider, binary)
    except OSError:
        return None
    found = CAPABILITY_MEMO.get(key)
    if found is None:
        found = await _probe_capabilities(binary)
        if found is not None:
            CAPABILITY_MEMO[key] = found
    return found


async def _probe_capabilities(binary: str) -> frozenset[str] | None:
    (plugin_code, plugin_help), (market_code, market_help) = await asyncio.gather(
        _run(binary, "plugin", "--help"), _run(binary, "plugin", "marketplace", "--help")
    )
    plugin_verbs = parse_subcommands(plugin_help) if plugin_code == 0 else frozenset()
    market_verbs = parse_subcommands(market_help) if market_code == 0 else frozenset()
    if not plugin_verbs or not market_verbs:
        return None
    if "marketplace" in plugin_verbs and "add" in market_verbs:
        return frozenset({"marketplace_add"})
    return frozenset()


async def catalog(provider, binary, fallback=None):
    """Return the known provider catalogue without commands, credentials, or stderr."""
    if provider not in {"codex", "claude"}:
        return {
            "items": [],
            "warnings": ["Catalog not supported for this provider."],
            "actions": [],
        }
    if not isinstance(binary, str) or not binary:
        return {"items": [], "warnings": ["The provider's CLI was not found."], "actions": []}

    plugin_command = (binary, "plugin", "list", "--available", "--json")
    mcp_command = (
        (binary, "mcp", "list", "--json") if provider == "codex" else (binary, "mcp", "list")
    )
    mcp_result, plugin_result, probed = await asyncio.gather(
        _run(*mcp_command), _run(*plugin_command), capabilities(provider, binary)
    )
    mcp_code, mcp_output = mcp_result
    plugin_code, plugin_output = plugin_result
    mcp_items = _codex_mcp(mcp_output) if provider == "codex" else _claude_mcp(mcp_output)
    plugin_items = _plugins(plugin_output)
    mcp_ok = mcp_code == 0 and mcp_items is not None
    actions: set[str] = set()
    if mcp_ok:
        actions.add("connector_add")
    if probed and "marketplace_add" in probed:
        actions.add("marketplace_add")
    warnings = []
    if not mcp_ok:
        mcp_items = _fallback(provider, fallback)
        warnings.append(
            "Could not read the CLI's MCP catalog; already configured connectors were used."
        )
    if plugin_code != 0 or plugin_items is None:
        plugin_items = []
        warnings.append("Could not read the CLI's plugin catalog.")
    return {
        "items": mcp_items + plugin_items,
        "warnings": warnings,
        "actions": sorted(actions),
    }
