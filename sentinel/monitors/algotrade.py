"""Alert feed monitor — detects new trigger activity from any alert-compatible backend."""

from __future__ import annotations

import logging

import httpx

from sentinel.config import settings
from sentinel.core.event import SentinelEvent, Tier
from sentinel.monitors.base import Monitor

log = logging.getLogger(__name__)

PRIORITY_TIER = {
    "critical": Tier.INTERRUPT,
    "high": Tier.INFORM,
    "medium": Tier.NUDGE,
    "low": Tier.NUDGE,
}


class AlertsMonitor(Monitor):
    """Polls GET /api/newsfeed/alerts — detects new trigger activity."""

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._last_counts: dict[str, int] = {}
        self._first_run: bool = True

    async def setup(self) -> None:
        self._client = httpx.AsyncClient(base_url=settings.backend_url, timeout=10.0)

    async def check(self) -> list[SentinelEvent]:
        assert self._client is not None
        events: list[SentinelEvent] = []
        try:
            resp = await self._client.get("/api/newsfeed/alerts")
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("Alerts poll failed: %s", exc)
            return events

        alerts = data if isinstance(data, list) else data.get("alerts", [])

        for alert in alerts:
            alert_id = alert.get("id", "")
            name = alert.get("name", alert_id)
            priority = alert.get("priority", "medium")
            trigger_count = alert.get("trigger_count", 0)
            enabled = alert.get("enabled", True)

            if not enabled:
                continue

            prev_count = self._last_counts.get(alert_id, 0)
            self._last_counts[alert_id] = trigger_count

            if self._first_run:
                continue

            if trigger_count > prev_count:
                new_triggers = trigger_count - prev_count
                tier = PRIORITY_TIER.get(priority, Tier.NUDGE)
                events.append(
                    SentinelEvent(
                        tier=tier,
                        source="alerts",
                        payload={
                            "summary": f"{name}: {new_triggers} new trigger(s) [priority={priority}]",
                            "alert_id": alert_id,
                            "name": name,
                            "priority": priority,
                            "new_triggers": new_triggers,
                            "total_triggers": trigger_count,
                        },
                    )
                )

        self._first_run = False
        return events

    async def teardown(self) -> None:
        if self._client:
            await self._client.aclose()
