"""Shared setup for the scheduled-task tests: a service config, a fixed clock, no queue worker."""

import asyncio
import hashlib
from datetime import datetime
from pathlib import Path

import pytest

from agent_service import schedules
from agent_service.services.conversation_service import ConversationService

ALICE = {"Authorization": "Bearer a"}
BOB = {"Authorization": "Bearer b"}
DAILY = {"kind": "daily", "time": "09:00"}
VALID = {
    "title": "Morning digest",
    "prompt": "Summarize yesterday's changes.",
    "project_id": "p",
    "backend": "codex",
    "model": "gpt-6-astra",
    "effort": "low",
    "cadence": DAILY,
}


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def local(year, month, day, hour=0, minute=0):
    """A UNIX timestamp for a wall-clock time in the process time zone."""
    return int(datetime(year, month, day, hour, minute).timestamp())


def folder_of(config, owner="a"):
    return Path(config["state_dir"]) / "schedules" / digest(owner)[:16]


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture(autouse=True)
def idle_worker(monkeypatch):
    """No test here may start the queue worker: it would run queued jobs on a real provider."""

    async def idle(self):
        await asyncio.sleep(3600)

    monkeypatch.setattr(ConversationService, "worker", idle)


@pytest.fixture
def clock(zone, monkeypatch):
    """Saturday 2026-10-03 08:00 in UTC, moved by assigning ``clock.now``."""
    zone("UTC")
    fixed = Clock(local(2026, 10, 3, 8))
    monkeypatch.setattr(schedules, "clock", fixed)
    return fixed


@pytest.fixture
def config(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    return {
        "state_dir": str(state),
        "origins": [],
        "projects": {"p": {}, "q": {}, "sem-projeto": {}},
        "clients": {
            "a": {"sha256": digest("a"), "projects": ["p", "q", "sem-projeto"]},
            "b": {"sha256": digest("b"), "projects": ["p"]},
        },
        "services": {
            name: {
                "enabled": True,
                "mode": "native",
                "models": models,
                "projects": ["p", "sem-projeto"],
                "permissions": {"read": True},
            }
            for name, models in {
                "codex": ["gpt-6-astra", "gpt-6-lite"],
                "claude": ["sonnet"],
                "local": ["installed-model"],
            }.items()
        },
        "codex_models": {"gpt-6-astra": ["low", "high"], "gpt-6-lite": ["low"]},
    }
