"""First-run wizard backend (#69, D-052): a status-only provider scan and the completion marker.

The scan only reads sign-in status. It never logs in or out, never launches Gemini, never reads
a token and never returns the CLI's own output, so nothing here can leak an identity.
"""

import asyncio
import logging
from datetime import datetime, timezone

from adapters.deepseek import account as deepseek
from adapters.gemini import account as gemini
from agent_service import ui_state
from agent_service.config import VERSION_FILE
from agent_service.errors import APIError

from . import discovery

logger = logging.getLogger(__name__)

PROVIDERS = ("codex", "claude", "gemini", "local", "deepseek")
PREFS = frozenset({"theme", "chat_selection", "always_on_top"})
OWNER = "local"  # the single owner (D-038/D-039): the store the harness reads for its UI


def _row(provider, found, signed_in, detail):
    return {"id": provider, "found": found, "signed_in": signed_in, "detail": detail}


async def _probe(manager, provider, info):
    """One provider's row; failures become a row, never an exception."""
    if provider == "deepseek":
        saved = deepseek.key_file(manager.state).is_file()
        return _row(
            provider,
            saved,
            None if saved else False,
            "key_saved_unverified" if saved else "signed_out",
        )
    if not info.get("found"):
        return _row(provider, False, False, "not_installed")
    if provider == "local":
        return _row(provider, True, True, "signed_in")
    if provider == "gemini":
        # Existence only: `gemini.check` launches the CLI and the credentials are never opened.
        if gemini.credential_present():
            return _row(provider, True, None, "credential_present_unverified")
        return _row(provider, True, False, "signed_out")
    try:
        result = await asyncio.wait_for(
            manager.signed_in(provider, info["binary"]), discovery.COMMAND_SECONDS + 2
        )
    except TimeoutError:
        return _row(provider, True, None, "timeout")
    except Exception:  # the CLI's error text can carry an account; log the id only
        logger.warning("First-run scan: %s probe failed", provider)
        return _row(provider, True, None, "error")
    if result.get("timed_out"):
        return _row(provider, True, None, "timeout")
    signed_in = result["signed_in"] is True
    return _row(provider, True, signed_in, "signed_in" if signed_in else "signed_out")


async def _scan(manager):
    if manager.inventory is None:
        await manager.refresh()
    found = {service["id"]: service for service in manager.inventory["services"]}
    rows = await asyncio.gather(*(_probe(manager, p, found.get(p, {})) for p in PROVIDERS))
    return {"providers": rows}


async def scan_first_run(request, manager, data):
    """Probe every provider at once, outside ``manager.lock``; touches no auth or model state.

    Single-flight: callers that arrive while a scan runs await that same scan and share its result.
    """
    task = manager._scan_task
    if task is None or task.done():
        task = manager._scan_task = asyncio.create_task(_scan(manager))
    return await asyncio.shield(task)


def _view(manager):
    return {
        "completed": bool(manager.settings["first_run"]["completed_at"]),
        "completed_at": manager.settings["first_run"]["completed_at"],
        "version": VERSION_FILE.read_text().strip(),
    }


def _mark(manager, completed_at):
    manager.settings["first_run"] = {"completed_at": completed_at}
    manager.state_repository.save_settings(manager.settings)


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def legacy_marker(settings):
    """The marker for settings that predate the wizard: done when some service is enabled."""
    enabled = any(spec.get("enabled") for spec in settings["services"].values())
    return {"completed_at": _now() if enabled else None}


async def read_first_run(request, manager):
    return _view(manager)


async def finish_first_run(request, manager, data):
    """Write the chosen prefs, then the marker; invalid prefs write and complete nothing."""
    prefs = data.get("prefs")
    if prefs is not None:
        if not isinstance(prefs, dict):
            raise APIError("ui_state_invalid_value", 422, field="prefs")
        for key in prefs:
            if key not in PREFS:
                raise APIError("ui_state_unknown_key", 422, field=str(key)[:64])
        if prefs:
            config = {"state_dir": str(manager.state / "runs")}
            await asyncio.to_thread(ui_state.update, config, OWNER, prefs)
    previous = manager.settings.get("first_run", {}).get("completed_at")
    _mark(manager, previous or _now())
    return _view(manager)


async def reset_first_run(request, manager, data):
    _mark(manager, None)
    return _view(manager)
