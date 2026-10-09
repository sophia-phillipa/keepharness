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
NOT_ALLOWED = "This provider does not use host connectors or plugins; Codex and Claude Code use their own native configuration."
GEMINI_READ_ONLY = "Read-only access turns connectors off for Gemini."
READ_ONLY = "Read-only access turns connectors and plugins off."
NATIVE = "Availability and approvals follow the native CLI configuration and selected access mode."
NATIVE_BACKENDS = frozenset({"codex", "claude"})
ASKS = "Each connector call asks for your approval."
UNATTENDED = "Connector calls run without asking (full access)."
INVENTORY_UNREADABLE = "Could not read the connector and plugin inventory."
DEEPSEEK_OWN_HOME = "DeepSeek runs in its own home and sees none of your Codex or Claude Code connectors or plugins."


class Route(NamedTuple):
    project_id: str
    backend: str
    model: str
    execution_mode: str
    access_mode: str


class Limits(NamedTuple):
    blocked: str  # why no item is effective on this route
    note: str  # one sentence about approvals for this route


def route_limits(config: Settings, route: Route) -> Limits:
    """What the adapters do with allowed integrations on this route (adapters/*/native.py)."""
    if route.execution_mode == "scoped":
        return Limits(ISOLATED, ISOLATED)
    if route.backend in NATIVE_BACKENDS:
        return Limits("", NATIVE)
    permissions = approval_policy.effective_permissions(
        maestro.model_permissions(config, route.backend, route.model, route.project_id),
        route.access_mode,
    )
    if route.backend == "gemini":
        return Limits(GEMINI_READ_ONLY if route.access_mode == "read_only" else "", "")
    if route.access_mode == "read_only":
        return Limits(READ_ONLY, "")
    # Automatic is bounded to the project, so a connector call asks there too (D11).
    if route.access_mode in ("ask", "auto"):
        return Limits("", ASKS)
    unattended = (
        config.get(route.backend, {}).get("unrestricted") is True
        and route.access_mode == "full"
        and bool(permissions.get("shell"))
    )
    return Limits("", UNATTENDED if unattended else "")


def item_reason(limits: Limits) -> str:
    """Why an item is not effective on the route; empty when it is."""
    return limits.blocked or NOT_ALLOWED


def read_catalog() -> dict[str, list[Item]] | None:
    """Every provider's installed items, read once per view; ``None`` when unreadable."""
    try:
        return inventory()
    except Exception as error:  # a hand-edited CLI profile must not fail the view
        logger.warning("Connector inventory unreadable: %s", type(error).__name__)
        return None


def load_items(backend: str, catalog: dict[str, list[Item]] | None) -> tuple[list[Item], list[str]]:
    """The backend's installed items; DeepSeek's own home sees no host connectors (D01)."""
    if backend == "deepseek":
        return [], [DEEPSEEK_OWN_HOME]
    return read_inventory(backend, catalog)


def read_inventory(
    backend: str, catalog: dict[str, list[Item]] | None
) -> tuple[list[Item], list[str]]:
    """The provider's installed connectors then plugins, as ``(items, warnings)``."""
    if catalog is None:
        return [], [INVENTORY_UNREADABLE]
    installed = catalog.get(backend, [])
    items = [
        {key: item.get(key) for key in PUBLIC_KEYS}
        for item in installed
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


def bare_name(item: Item) -> str:
    """The connector's name without kind prefix or marketplace."""
    name = str(item.get("name") or item.get("id") or "")
    return re.sub(r"^(?:mcp|plugin):", "", name, flags=re.I).split("@")[0]


def family_key(item: Item) -> str:
    """The bare name, normalised, so one tool matches across providers."""
    return re.sub(r"[_ ]", "-", bare_name(item).lower())


def build(
    config: Settings,
    route: Route,
    usage_rows: Sequence[sqlite3.Row],
) -> Item:
    """The ``/v1/integrations`` response for one route; ``usage_rows`` come from the repository."""
    catalog = read_catalog()
    items, warnings = load_items(route.backend, catalog)
    limits = route_limits(config, route)
    used, other_tools = attribute_usage(items, usage_rows)
    for item in items:
        item["allowed"] = False  # no harness allow list: native CLIs follow their own config
        item["reason"] = item_reason(limits)
        item["effective"] = not item["reason"]
        if route.backend in NATIVE_BACKENDS and route.execution_mode == "native":
            item.update(allowed=None, effective=None, reason=NATIVE)
        item["used"] = used.get(item["id"], no_usage())
    return {
        "backend": route.backend,
        "execution_mode": route.execution_mode,
        "access_mode": route.access_mode,
        "effective_note": limits.note,
        "items": items,
        "other_tools": other_tools,
        "elsewhere": [],
        "window_days": WINDOW_DAYS,
        "warnings": warnings,
    }
