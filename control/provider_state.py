"""Provider state for the admin panel: one service over the Codex and Claude Code adapters.

The adapters block (CLI sessions, file writes), so every call runs in a thread under one
``asyncio.Lock`` per provider. Bodies carry snapshot fields and adapter-authored messages
only (at most ``MESSAGE_LIMIT`` characters); logs carry error codes, never messages.
"""

import asyncio
import dataclasses
import logging
import time
from collections.abc import Callable, Mapping
from functools import partial
from pathlib import Path

from starlette.responses import JSONResponse

from adapters.claude.state import ClaudeStateAdapter
from adapters.codex.state import CodexStateAdapter
from adapters.shared.process import redact_paths
from adapters.shared.provider_state import (
    ProviderCommandError,
    ProviderStateAdapter,
    ProviderStateConflictError,
    StateSnapshot,
    _ProviderStateError,
)
from agent_service.errors import APIError

PROVIDERS = ("codex", "claude")
COALESCE_SECONDS = 5.0
MESSAGE_LIMIT = 300
NO_PROJECT = "sem-projeto"

logger = logging.getLogger(__name__)


def snapshot_json(snapshot: StateSnapshot) -> dict:
    return dataclasses.asdict(snapshot)  # tuples become JSON arrays when serialized


def error_response(exc: _ProviderStateError, **extra) -> JSONResponse:
    """The control envelope ``{"error": code}`` plus the adapter's message, capped."""
    logger.warning("Provider state request failed: %s", exc.code)
    field = "provider_message" if isinstance(exc, ProviderCommandError) else "message"
    body = {"error": exc.code, **extra}
    if not isinstance(exc, ProviderStateConflictError):
        text = redact_paths(str(exc)) if field == "provider_message" else str(exc)
        body[field] = text[:MESSAGE_LIMIT]
    return JSONResponse(body, exc.status)


class ProviderStateService:
    def __init__(
        self,
        state: Path,
        projects: Callable[[], list[dict]],
        adapters: Mapping[str, ProviderStateAdapter] | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.state = Path(state)
        self.projects = projects
        self.adapters = adapters  # built on first use
        self.clock = clock
        self.locks: dict[str, asyncio.Lock] = {}
        self.cache: dict[tuple[str, str], tuple[float, StateSnapshot]] = {}

    def resolve(self, provider: str, project_id: str) -> Path | None:
        """The project's folder (``None`` for "No project"); 404 for an unknown provider or project."""
        if provider not in PROVIDERS:
            raise APIError("provider_unknown", 404)
        if project_id == NO_PROJECT:
            return None
        for project in self.projects():
            if project.get("id") == project_id:
                return Path(project["root"])
        raise APIError("project_unknown", 404)

    def _adapter(self, provider: str) -> ProviderStateAdapter:
        if self.adapters is None:
            self.adapters = {
                "codex": CodexStateAdapter(),
                "claude": ClaudeStateAdapter(self.state),
            }
        return self.adapters[provider]

    async def _read_fresh(self, provider: str, project_id: str, root: Path | None) -> StateSnapshot:
        snapshot = await asyncio.to_thread(self._adapter(provider).read_state, root)
        self.cache[provider, project_id] = (self.clock(), snapshot)
        return snapshot

    async def read(self, provider: str, project_id: str) -> dict | JSONResponse:
        root = self.resolve(provider, project_id)
        try:
            async with self.locks.setdefault(provider, asyncio.Lock()):
                cached = self.cache.get((provider, project_id))
                if cached and self.clock() - cached[0] < COALESCE_SECONDS:
                    snapshot = cached[1]
                else:
                    snapshot = await self._read_fresh(provider, project_id, root)
        except _ProviderStateError as exc:
            return error_response(exc)
        return {"snapshot": snapshot_json(snapshot), "external_changes": []}

    async def write(
        self,
        provider: str,
        project_id: str,
        item_id: str,
        scope: str,
        enabled: bool,
        fingerprint: str,
    ) -> dict | JSONResponse:
        """``{"snapshot"}`` on success, the error response (409 with a fresh snapshot) otherwise."""
        root = self.resolve(provider, project_id)
        async with self.locks.setdefault(provider, asyncio.Lock()):
            adapter = self._adapter(provider)
            try:
                snapshot = await asyncio.to_thread(
                    partial(
                        adapter.set_enabled,
                        item_id,
                        scope,
                        enabled,
                        fingerprint,
                        project_root=root,
                    )
                )
            except ProviderStateConflictError as exc:
                fresh = await self._read_fresh(provider, project_id, root)
                return error_response(exc, snapshot=snapshot_json(fresh), external_changes=[])
            except _ProviderStateError as exc:
                return error_response(exc)
            finally:
                # a write can change what any project of this provider sees, even a failed one
                for key in [key for key in self.cache if key[0] == provider]:
                    del self.cache[key]
            self.cache[provider, project_id] = (self.clock(), snapshot)
        return {"snapshot": snapshot_json(snapshot)}
