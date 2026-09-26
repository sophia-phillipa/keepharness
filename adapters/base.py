"""Harness-owned provider interfaces; each ``<provider>/backend.py`` module implements them.

The protocols are structural: a backend module satisfies them by defining the functions,
without subclassing anything.
"""

from typing import Protocol


class ProviderAdapter(Protocol):
    """Runs one turn in the provider's own (native) session."""

    async def run_native(
        self, config, prompt, event, project, model, effort, session_dir, approve
    ) -> dict: ...


class ScopedProviderAdapter(ProviderAdapter, Protocol):
    """Also runs one turn in a disposable, isolated workspace."""

    async def run_scoped(
        self,
        config,
        prompt,
        event,
        project=None,
        model=...,
        effort=...,
        staged=None,
        session_dir=None,
    ) -> dict: ...
