"""Read-only view of the connectors (MCP servers) and plugins a conversation route can use.

For one provider, project and route (execution and access mode) it answers what is installed
on this host, what the administrator allowed, what is effective for this run, and what the
project's recent runs used. It never changes configuration, never starts a provider CLI and
never returns a command, argument, URL, header or environment value from a provider profile.
"""

import logging
import re
import sqlite3
from collections.abc import Sequence
from typing import Any, NamedTuple

from control.integrations import inventory

from . import approval_policy, maestro

logger = logging.getLogger(__name__)

# The live harness settings and the CLI inventory are free-form JSON/TOML dicts.
Item = dict[str, Any]
Settings = dict[str, Any]

WINDOW_DAYS = 30
TOOLS_PER_ITEM = 5
OTHER_TOOLS_LIMIT = 20
USAGE_TOOL_LIMIT = 500  # distinct tool names read per request
NAME_LIMIT = 120
INTERNAL_PREFIX = "harness_effects"  # the harness's own publication server, not a user connector
PUBLIC_KEYS = ("id", "kind", "name", "transport", "status")

ISOLATED = "Isolated conversations use no host connectors or plugins."
NOT_ALLOWED = "Not allowed for this provider. Change it in Settings › System › Providers."
GEMINI_READ_ONLY = "Read-only access turns connectors off for Gemini."
GEMINI_INTERNET = "Gemini connectors need the internet permission."
ASKS = "Each connector call asks for your approval."
UNATTENDED = "Connector calls run without asking (full access)."
INVENTORY_UNREADABLE = "Could not read the connector and plugin inventory."


class Route(NamedTuple):
    project_id: str
    backend: str
    model: str
    execution_mode: str
    access_mode: str


class Limits(NamedTuple):
    blocked: str  # why no item is effective on this route
    blocked_if_allowed: str  # why an allowed item is still not effective
    note: str  # one sentence about approvals for this route


def route_limits(config: Settings, route: Route) -> Limits:
    """What the adapters do with allowed integrations on this route (adapters/*/native.py)."""
    if route.execution_mode == "scoped":
        return Limits(ISOLATED, "", ISOLATED)
    permissions = approval_policy.effective_permissions(
        maestro.model_permissions(config, route.backend, route.model, route.project_id),
        route.access_mode,
    )
    if route.backend == "gemini":
        return Limits(
            GEMINI_READ_ONLY if route.access_mode == "read_only" else "",
            "" if permissions.get("internet") else GEMINI_INTERNET,
            "",
        )
    if route.backend == "claude" and route.access_mode == "ask":
        return Limits("", "", ASKS)
    unattended = (
        config.get(route.backend, {}).get("unrestricted") is True
        and route.access_mode not in ("ask", "read_only")
        and bool(permissions.get("shell"))
    )
    return Limits("", "", UNATTENDED if unattended else "")


def item_reason(allowed: bool, limits: Limits) -> str:
    """Why an item is not effective on the route; empty when it is."""
    if limits.blocked:
        return limits.blocked
    return limits.blocked_if_allowed if allowed else NOT_ALLOWED


def with_plugin_catalog(config: Settings, backend: str, installed: list[Item]) -> list[Item]:
    """Swap the profile's plugins for the list the adapter really toggles (``codex plugin list``)."""
    catalog = config.get(backend, {}).get("plugin_inventory")
    if not isinstance(catalog, list):
        return installed
    plugins = [
        {"id": plugin, "kind": "plugin", "name": plugin.split(":", 1)[1], "status": "installed"}
        for plugin in catalog
        if isinstance(plugin, str) and plugin.startswith("plugin:")
    ]
    return [item for item in installed if item.get("kind") != "plugin"] + plugins


def read_inventory(config: Settings, backend: str) -> tuple[list[Item], list[str]]:
    """The provider's installed connectors then plugins, as ``(items, warnings)``."""
    try:
        installed = inventory().get(backend, [])
    except Exception as error:  # a hand-edited CLI profile must not fail the view
        logger.warning("Connector inventory unreadable: %s", type(error).__name__)
        return [], [INVENTORY_UNREADABLE]
    items = [
        {key: item.get(key) for key in PUBLIC_KEYS}
        for item in with_plugin_catalog(config, backend, installed)
        if not item["name"].startswith(INTERNAL_PREFIX)
    ]
    return sorted(items, key=lambda item: (item["kind"] != "mcp", item["name"])), []


def mcp_parts(tool: str) -> tuple[str | None, str]:
    """``mcp__<server>__<tool>`` as ``(server, tool)``; ``(None, tool)`` for any other name."""
    if tool.startswith("mcp__"):
        server, _, name = tool[5:].partition("__")
        if server and name:
            return server, name
    return None, tool


def no_usage() -> Item:
    return {"count": 0, "last_used": None, "tools": []}


def add_use(figures: Item, name: str, row: sqlite3.Row) -> None:
    """Fold one usage row into a connector's figures; rows arrive most-used first."""
    figures["count"] += row["uses"]
    figures["last_used"] = max(figures["last_used"] or 0, row["last_used"])
    if len(figures["tools"]) < TOOLS_PER_ITEM:
        figures["tools"].append(name[:NAME_LIMIT])


def attribute_usage(
    items: list[Item], rows: Sequence[sqlite3.Row]
) -> tuple[dict[str, Item], list[Item]]:
    """Split usage rows into per-connector figures and the tools no connector owns.

    Claude spells every ``[^A-Za-z0-9_-]`` of a server name as ``_`` inside tool names; Codex
    and Gemini tool names carry no server, so their usage stays under the other tools.
    """
    owners = {
        re.sub(r"[^A-Za-z0-9_-]", "_", item["name"]): item["id"]
        for item in items
        if item["kind"] == "mcp"
    }
    used: dict[str, Item] = {}
    other: list[Item] = []
    for row in rows:
        tool = row["tool"]
        if not isinstance(tool, str) or not tool:
            continue
        server, name = mcp_parts(tool)
        if server is not None and server.startswith(INTERNAL_PREFIX):
            continue
        item_id = owners.get(server)
        if item_id is None:
            other.append(
                {"name": tool[:NAME_LIMIT], "count": row["uses"], "last_used": row["last_used"]}
            )
        else:
            add_use(used.setdefault(item_id, no_usage()), name, row)
    return used, other[:OTHER_TOOLS_LIMIT]


def build(config: Settings, route: Route, usage_rows: Sequence[sqlite3.Row]) -> Item:
    """The ``/v1/integrations`` response for one route; ``usage_rows`` come from the repository."""
    items, warnings = read_inventory(config, route.backend)
    allowed = set(config.get("services", {}).get(route.backend, {}).get("integrations", []))
    limits = route_limits(config, route)
    used, other_tools = attribute_usage(items, usage_rows)
    for item in items:
        item["allowed"] = item["id"] in allowed
        item["reason"] = item_reason(item["allowed"], limits)
        item["effective"] = not item["reason"]
        item["used"] = used.get(item["id"], no_usage())
    return {
        "backend": route.backend,
        "execution_mode": route.execution_mode,
        "access_mode": route.access_mode,
        "effective_note": limits.note,
        "items": items,
        "other_tools": other_tools,
        "window_days": WINDOW_DAYS,
        "warnings": warnings,
    }
