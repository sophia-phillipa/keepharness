"""Provider capacity: dispatcher lanes and inference slots, both freed while a run waits for a person.

Two counters limit one provider, and both read ``services.<provider>.max_concurrent``:

- a dispatcher lane, taken when ``queue_worker.run`` starts a job (counted by the job's own
  backend) and freed when the job's task ends;
- an inference slot, taken by every ``ConversationService.infer`` (workflow steps
  too) and freed when that inference ends.

A run that waits for an approval or a gate holds neither (D14): ``parked`` frees both, and takes
them back after the decision, before the run goes on. A local model keeps both, because its
process keeps the GPU while it waits. State lives on the service, following the
``queue_worker.run(service)`` pattern.
"""

import asyncio
from contextlib import asynccontextmanager, nullcontext

from ..errors import APIError

# D14: a cloud provider runs two conversations at once unless the admin sets another number.
# A local model keeps one.
CLOUD_PROVIDERS = frozenset({"codex", "claude", "gemini", "deepseek"})


def maximum(config: dict, backend: str) -> object:
    """``services.<backend>.max_concurrent`` as stored, or its default; callers validate it."""
    default = 2 if backend in CLOUD_PROVIDERS else 1
    return config.get("services", {}).get(backend, {}).get("max_concurrent", default)


def valid_maximum(config: dict, backend: str) -> int | None:
    value = maximum(config, backend)
    return value if type(value) is int and value >= 1 else None


def lane_available(service, backend: str) -> bool:
    """A new dispatch fits, after runs coming back from an approval wait take their lane."""
    limit = valid_maximum(service.config, backend) or 1  # Dispatch validates the capacity.
    taken = service.provider_lanes.get(backend, 0) + service.lane_resumers.get(backend, 0)
    return taken < limit


def take_lane(service, job: str, backend: str) -> None:
    service.provider_lanes[backend] = service.provider_lanes.get(backend, 0) + 1
    service.job_lanes[job] = backend


def notify_lanes(service) -> None:
    """Wake the dispatcher and the runs waiting to take their lane back."""
    freed, service.lane_freed = service.lane_freed, asyncio.Event()
    freed.set()
    service.wake.set()


def free_lane(service, backend: str) -> None:
    service.provider_lanes[backend] -= 1
    notify_lanes(service)


def release_lane(service, job: str) -> None:
    """The job's task ended: free its lane, unless an approval wait already freed it."""
    backend = service.job_lanes.pop(job, None)
    held = service.parked_capacity.pop(job, None)
    if held is not None and held.pop("lane", None):
        return
    if backend is not None:
        free_lane(service, backend)


async def take_slot(service, backend: str) -> bool:
    """Wait for an inference slot and take it; ``True`` when the call had to wait."""
    condition = service.provider_slots.setdefault(backend, asyncio.Condition())
    waited = False
    async with condition:
        while True:
            limit = valid_maximum(service.config, backend)
            if limit is None:
                raise APIError("invalid_provider_capacity")
            if service.provider_inflight.get(backend, 0) < limit:
                service.provider_inflight[backend] = service.provider_inflight.get(backend, 0) + 1
                return waited
            waited = True
            await condition.wait()


async def free_slot(service, backend: str) -> None:
    service.provider_inflight[backend] -= 1
    condition = service.provider_slots.setdefault(backend, asyncio.Condition())
    async with condition:
        condition.notify_all()


async def release_slot(service, job: str, backend: str) -> None:
    """The inference ended: free its slot, unless an approval wait already freed it."""
    held = service.parked_capacity.get(job)
    if held is not None and held.pop("slot", None):
        return
    await free_slot(service, backend)


async def _park(service, job: str, held: dict) -> None:
    lane = service.job_lanes.get(job)
    slot = service.active_executors.get(job, (None,))[0]
    if "local" in (lane, slot):
        return
    if lane is not None:
        held["lane"] = lane
        free_lane(service, lane)
    if slot is not None:
        held["slot"] = slot
        await free_slot(service, slot)


def _restore(service, job: str, held: dict) -> None:
    """Hold again, at once, whatever the wait freed, so the job's owners free it exactly once."""
    if service.parked_capacity.get(job) is not held:
        return  # The job already ended; its owners did not count this capacity.
    service.parked_capacity.pop(job)
    if held.pop("lane", None):
        backend = service.job_lanes.get(job)
        if backend is not None:
            service.provider_lanes[backend] = service.provider_lanes.get(backend, 0) + 1
    if slot := held.pop("slot", None):
        service.provider_inflight[slot] = service.provider_inflight.get(slot, 0) + 1


async def _take_back(service, job: str, held: dict) -> None:
    """Take the lane, then the slot (the order they were first taken in), waiting for room."""
    try:
        if (lane := held.get("lane")) and service.parked_capacity.get(job) is held:
            service.lane_resumers[lane] = service.lane_resumers.get(lane, 0) + 1
            try:
                while service.provider_lanes.get(lane, 0) >= (
                    valid_maximum(service.config, lane) or 1
                ):
                    await service.lane_freed.wait()
            finally:
                service.lane_resumers[lane] -= 1
            if held.pop("lane", None):
                service.provider_lanes[lane] = service.provider_lanes.get(lane, 0) + 1
        if (slot := held.get("slot")) and service.parked_capacity.get(job) is held:
            await take_slot(service, slot)
            if not held.pop("slot", None):
                await free_slot(service, slot)  # The inference ended while this waited.
    finally:
        _restore(service, job, held)


@asynccontextmanager
async def parked(service, job: str):
    """A human wait: the active deadline pauses and the job frees its lane and slot (D14)."""
    budget = service.runtime_budgets.get(job)
    with budget.human_wait() if budget is not None else nullcontext():
        held = service.parked_capacity.get(job)
        if held is None:
            held = service.parked_capacity[job] = {"depth": 0}
            await _park(service, job, held)
        held["depth"] += 1
        try:
            yield
        except BaseException:
            held["depth"] -= 1
            if not held["depth"]:
                _restore(service, job, held)
            raise
        held["depth"] -= 1
        if not held["depth"]:
            await _take_back(service, job, held)
