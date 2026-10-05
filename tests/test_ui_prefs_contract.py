"""The browser half of the UI state store stays inside the server's caps (WP6)."""

import re
from pathlib import Path

from agent_service import ui_state

AGENT = Path(__file__).resolve().parents[1] / "agent_service"
UI = (AGENT / "ui.js").read_text(encoding="utf-8")
PREFS = (AGENT / "ui-prefs.js").read_text(encoding="utf-8")


def test_navigation_scroll_limit_fits_the_store_cap():
    limit = int(re.search(r"const NAV_LIMIT = (\d+);", UI).group(1))
    assert limit <= ui_state.ITEM_CAPS["conversation_scroll"]


def test_client_reads_caps_from_the_server_limits():
    assert "limits.max_items" in PREFS
    for key in ui_state.ITEM_CAPS:
        # No pruned key is mentioned next to a number: the caps come from GET /v1/ui-state.
        assert not re.search(rf"{key}\W{{1,6}}\d", PREFS), key


def test_every_store_key_has_a_client_conversion():
    for key in ui_state.SCHEMA:
        assert re.search(rf"^\s+{key}: \{{", PREFS, re.M), key
