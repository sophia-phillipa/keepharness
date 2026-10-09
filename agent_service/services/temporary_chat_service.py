"""Owner-bound, volatile conversation services with scoped disposable artifacts."""

import asyncio
import copy
import fcntl
import logging
import os
import re
import shutil
import time
import uuid
from contextlib import contextmanager
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
    sid: str
    requests: set = field(default_factory=set)


class TemporaryChatService:
    def __init__(self, service):
        self.service = service
        self.root = service.root / "temporary-chats"
        # Provider run folders sit beside the harness state, like ordinary runs: the folder that
        # holds the keys is never a parent of a provider cwd (see runtime_config.base_config).
        sessions = service.sessions_root()
        self.sessions_base = sessions / "temporary-chats"
        self.sessions = {}
        for folder in (self.root, sessions, self.sessions_base):
            if folder.is_symlink():
                raise APIError("temporary_storage_unavailable")
            folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self._storage_lock():
            self._sweep_stale()

    @contextmanager
    def _storage_lock(self):
        """Serialize publication and removal across harness processes."""
        path = self.root.with_suffix(".lock")
        if path.is_symlink():
            raise APIError("temporary_storage_unavailable")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def _sweep_stale(self):
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
                self._remove_folders(folder.name)

    def _remove_folders(self, sid):
        """Delete one session's run folders and state folder; the caller holds the storage lock."""
        # A symlink is unlinked, never followed; one folder failing must not leave the other behind.
        failure = None
        for folder in (self.sessions_base / sid, self.root / sid):
            try:
                if folder.is_symlink():
                    folder.unlink()
                elif folder.exists():
                    shutil.rmtree(folder)
            except OSError as error:
                failure = failure or error
        if failure:
            raise failure

    def _publish(self, root):
        """Create the session folder and take its lease; blocks on other harness processes."""
        with self._storage_lock():
            root.mkdir(mode=0o700)
            lock = (root / ".lock").open("a")
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return lock

    def _discard(self, sid, lock):
        try:
            with self._storage_lock():
                self._remove_folders(sid)
        finally:
            if lock:
                lock.close()

    async def open(self, identity):
        sid = uuid.uuid4().hex
        publish = asyncio.ensure_future(asyncio.to_thread(self._publish, self.root / sid))
        try:
            return self._start(identity, sid, await asyncio.shield(publish))
        except BaseException:
            # Even a cancelled request must not leave its folder behind.
            await asyncio.shield(self._abandon(sid, publish))
            raise

    async def _abandon(self, sid, publish):
        try:
            lock = await publish
        except Exception:
            lock = None
        await asyncio.to_thread(self._discard, sid, lock)

    def _start(self, identity, sid, lock):
        from .conversation_service import ConversationService

        if sum(session.owner == identity[0] for session in self.sessions.values()) >= 8:
            raise APIError("temporary_session_limit", 429)
        root = self.root / sid
        config = copy.deepcopy(self.service.config)
        config.update(
            temporary_chat=True, state_dir=str(root), sessions_dir=str(self.sessions_base / sid)
        )
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
        finally:
            temporary_execution.reset(token)
        self.sessions[sid] = Session(
            identity[0], service, worker, lock, time.monotonic() + LEASE_SECONDS, sid
        )
        self.service.project_service.conversation_repositories.add(service.conversation_repository)
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
        self.service.project_service.conversation_repositories.discard(
            session.service.conversation_repository
        )
        session.service.db.close()
        await asyncio.to_thread(self._discard, session.sid, session.lock)

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
