"""Abstract base for all monitors."""

from __future__ import annotations

import abc

from sentinel.core.event import SentinelEvent


class Monitor(abc.ABC):
    """A monitor watches a source and yields events."""

    @abc.abstractmethod
    async def check(self) -> list[SentinelEvent]:
        """Poll or consume the source and return new events."""
        ...

    async def setup(self) -> None:
        """Optional one-time init (open connections, etc.)."""

    async def teardown(self) -> None:
        """Optional cleanup."""
