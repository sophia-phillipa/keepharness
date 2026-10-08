"""Owner-bound, volatile conversation services with scoped disposable artifacts."""

import asyncio
import copy
import fcntl
import logging
import re
import shutil
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field

from ..errors import APIError

temporary_execution = ContextVar("temporary_chat_execution", default=False)
LEASE_SECONDS = 120


class TemporaryLogFilter(logging.Filter):
    def filter(self, record):
        return not temporary_execution.get()


def protect_logs():
    """Task-local suppression also follows asyncio tasks and to_thread workers."""
    loggers = [logging.getLogger(), *logging.Logger.manager.loggerDict.values()]
    for logger in loggers:
        if isinstance(logger, logging.Logger):
            for handler in logger.handlers:
                if not any(isinstance(item, TemporaryLogFilter) for item in handler.filters):
                    handler.addFilter(TemporaryLogFilter())


@dataclass
class Session:
    owner: str
    service: object
    worker: asyncio.Task
    lock: object
    deadline: float
    requests: set = field(default_factory=set)


class TemporaryChatService:
    def __init__(self, service):
        self.service = service
        self.root = service.root / "temporary-chats"
        self.sessions = {}
        if self.root.is_symlink():
            raise APIError("temporary_storage_unavailable")
        self.root.mkdir(mode=0o700, exist_ok=True)
        # A lock protects another still-running harness using this state directory.
        for folder in self.root.iterdir():
            if (
                folder.is_symlink()
                or not folder.is_dir()
                or not re.fullmatch(r"[a-f0-9]{32}", folder.name)
            ):
                continue
            if (folder / ".lock").is_symlink():
                continue
            with (folder / ".lock").open("a") as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    continue
                shutil.rmtree(folder)

    def open(self, identity):
        from .conversation_service import ConversationService

        if sum(session.owner == identity[0] for session in self.sessions.values()) >= 8:
            raise APIError("temporary_session_limit", 429)
        sid = uuid.uuid4().hex
        root = self.root / sid
        root.mkdir(mode=0o700)
        lock = (root / ".lock").open("a")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        config = copy.deepcopy(self.service.config)
        config.update(temporary_chat=True, state_dir=str(root), sessions_dir=str(root / "sessions"))
        token = temporary_execution.set(True)
        protect_logs()
        try:
            service = ConversationService(config, temporary_parent=self.service)
            for name in (
                "requests",
                "write_ownership",
                "provider_slots",
                "provider_inflight",
                "provider_lanes",
                "job_lanes",
                "lane_resumers",
                "parked_capacity",
            ):
                setattr(service, name, getattr(self.service, name))
            worker = asyncio.create_task(service.worker())
        except BaseException:
            lock.close()
            shutil.rmtree(root)
            raise
        finally:
            temporary_execution.reset(token)
        self.sessions[sid] = Session(
            identity[0], service, worker, lock, time.monotonic() + LEASE_SECONDS
        )
        return sid

    def get(self, identity, sid, *, closing=False):
        session = self.sessions.get(sid)
        if session is None or session.owner != identity[0]:
            raise APIError("temporary_session_not_found", 404)
        if not closing:
            if session.deadline <= time.monotonic():
                raise APIError("temporary_session_not_found", 404)
            session.deadline = time.monotonic() + LEASE_SECONDS
        return session

    async def close(self, identity, sid):
        session = self.get(identity, sid, closing=True)
        del self.sessions[sid]
        cleanup = asyncio.create_task(self._dispose(session))
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            # A closing tab or shutdown must not abandon cleanup midway through.
            await cleanup
            raise

    async def _dispose(self, session):
        session.service.temporary_closed = True
        pending = [task for task in session.requests if task is not asyncio.current_task()]
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        session.worker.cancel()
        await asyncio.gather(session.worker, return_exceptions=True)
        await session.service.effects.close()
        session.service.db.close()
        try:
            await asyncio.to_thread(shutil.rmtree, session.service.root)
        finally:
            session.lock.close()

    async def close_all(self):
        for sid, session in list(self.sessions.items()):
            if sid in self.sessions:
                await self.close((session.owner, {}), sid)

    async def expire(self):
        for sid, session in list(self.sessions.items()):
            if sid in self.sessions and session.deadline <= time.monotonic():
                await self.close((session.owner, {}), sid)

    async def sweep(self):
        while True:
            await asyncio.sleep(15)
            await self.expire()

    def route(self, request, identity):
        sid = request.headers.get("x-keepharness-temporary")
        if not sid:
            return self.service
        session = self.get(identity, sid)
        request.state.temporary_session = session
        session.requests.add(asyncio.current_task())
        path = request.url.path
        if path.endswith(
            ("/continuation", "/artifacts/result.json", "/save-workflow", "/download")
        ):
            raise APIError("temporary_operation_unsupported", 409)
        if path.startswith(
            ("/v1/jobs", "/v1/files", "/v1/conversations/", "/v1/approvals/", "/v1/effects/")
        ) or path in ("/v1/assess", "/v1/approval-rules", "/v1/project-files/attach"):
            return session.service
        if request.method not in ("GET", "HEAD"):
            raise APIError("temporary_operation_unsupported", 409)
        return self.service
