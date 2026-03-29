"""Desktop notification adapter for dev/testing."""

from __future__ import annotations

import logging

from sentinel.adapters.base import Adapter
from sentinel.core.event import SentinelEvent

log = logging.getLogger(__name__)


class DesktopAdapter(Adapter):
    """Logs to console and attempts OS-native toast notification."""

    async def push(self, event: SentinelEvent, display_text: str) -> None:
        log.info("DESKTOP PUSH: %s", display_text)
        try:
            # Windows toast via plyer (optional dep)
            from plyer import notification  # type: ignore[import-untyped]

            notification.notify(
                title=f"Sentinel [{event.tier.name}]",
                message=display_text,
                timeout=10,
            )
        except ImportError:
            pass  # plyer not installed — console log is enough
