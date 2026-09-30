"""Per-execution active time; human waits pause only the active deadline."""

import asyncio
import math
import time
from contextlib import asynccontextmanager, contextmanager


def timeout_seconds(config, key, default):
    value = config.get(key, default)
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
    ):
        raise ValueError(f"invalid_{key}")
    return value


class RuntimeBudget:
    def __init__(self, clock=None):
        self.clock = clock or time.monotonic
        self.started = self.clock()
        self.wait_started = None
        self.wait_depth = 0
        self.wait_seconds = 0
        self.deadline = None
        self.maximum = None

    @property
    def elapsed_seconds(self):
        return self.clock() - self.started

    @property
    def human_wait_seconds(self):
        return self.wait_seconds + (self.clock() - self.wait_started if self.wait_depth else 0)

    @property
    def active_seconds(self):
        return max(0, self.elapsed_seconds - self.human_wait_seconds)

    def metrics(self):
        return {
            "active_seconds": self.active_seconds,
            "elapsed_seconds": self.elapsed_seconds,
            "human_wait_seconds": self.human_wait_seconds,
        }

    def reschedule(self):
        if self.deadline is not None and not self.deadline.expired():
            when = None
            if self.maximum is not None and not self.wait_depth:
                when = asyncio.get_running_loop().time() + max(
                    0, self.maximum - self.active_seconds
                )
            self.deadline.reschedule(when)

    @contextmanager
    def human_wait(self):
        if not self.wait_depth:
            self.wait_started = self.clock()
        self.wait_depth += 1
        self.reschedule()
        try:
            yield
        finally:
            self.wait_depth -= 1
            if not self.wait_depth:
                self.wait_seconds += self.clock() - self.wait_started
                self.wait_started = None
            self.reschedule()

    @asynccontextmanager
    async def limit(self, maximum):
        self.maximum = maximum
        async with asyncio.timeout(None) as deadline:
            self.deadline = deadline
            self.reschedule()
            try:
                yield self
            finally:
                self.deadline = None
