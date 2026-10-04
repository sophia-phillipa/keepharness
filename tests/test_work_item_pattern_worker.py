"""The pattern worker's deadline covers matching only, never interpreter startup."""

import time

import pytest

from agent_service import work_items
from agent_service.errors import APIError

STARTUP_DELAY = 0.6
CATASTROPHIC = {"work_item_pattern": "(a+)+$"}
CATASTROPHIC_DATA = {"invocations": [{"args": "a" * 100000 + "!"}]}


@pytest.fixture
def slow_startup(monkeypatch):
    """Delay the worker's start-up well past the match budget."""
    monkeypatch.setattr(work_items, "_MATCH_SECONDS", 0.2)
    monkeypatch.setattr(
        work_items,
        "_MATCH_REFERENCES",
        f"import time\ntime.sleep({STARTUP_DELAY})\n" + work_items._MATCH_REFERENCES,
    )


@pytest.fixture
def children(monkeypatch):
    started = []
    original = work_items.subprocess.Popen

    def record(*args, **kwargs):
        child = original(*args, **kwargs)
        started.append(child)
        return child

    monkeypatch.setattr(work_items.subprocess, "Popen", record)
    return started


def test_valid_pattern_survives_slow_worker_startup(slow_startup, children):
    result = work_items.invocation_reference(
        {}, {"work_item_pattern": r"TASK-\d+"}, {"invocations": [{"args": "see TASK-7"}]}
    )
    assert result == "TASK-7"
    assert len(children) == 1 and children[0].returncode == 0


def test_catastrophic_pattern_is_cut_off_after_slow_startup(slow_startup, children):
    started = time.monotonic()
    with pytest.raises(APIError, match="invalid_work_item_pattern"):
        work_items.invocation_reference({}, CATASTROPHIC, CATASTROPHIC_DATA)
    assert time.monotonic() - started < STARTUP_DELAY + 3
    assert len(children) == 1 and children[0].returncode is not None


def test_worker_that_never_signals_ready_is_killed(monkeypatch, children):
    monkeypatch.setattr(work_items, "_STARTUP_SECONDS", 0.3)
    monkeypatch.setattr(work_items, "_MATCH_REFERENCES", "import time\ntime.sleep(60)\n")
    started = time.monotonic()
    with pytest.raises(APIError, match="invalid_work_item_pattern"):
        work_items.invocation_reference(
            {}, {"work_item_pattern": "x"}, {"invocations": [{"args": "x"}]}
        )
    assert time.monotonic() - started < 5
    assert len(children) == 1 and children[0].returncode is not None
