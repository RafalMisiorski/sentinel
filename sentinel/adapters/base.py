"""Abstract base for push adapters."""

from __future__ import annotations

import abc

from sentinel.core.event import SentinelEvent


class Adapter(abc.ABC):
    """Delivers a scored event to the user."""

    @abc.abstractmethod
    async def push(self, event: SentinelEvent, display_text: str) -> None:
        ...

    async def setup(self) -> None:
        """Optional init."""

    async def teardown(self) -> None:
        """Optional cleanup."""
