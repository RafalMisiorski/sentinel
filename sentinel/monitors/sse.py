"""SSE consumer with automatic fallback to polling monitors."""

from __future__ import annotations

import asyncio
import json
import logging

import httpx
from httpx_sse import aconnect_sse

from sentinel.config import settings
from sentinel.core.event import SentinelEvent, Tier
from sentinel.monitors.base import Monitor

log = logging.getLogger(__name__)

SSE_TYPES = "job.failed,job.completed,health.degraded,health.recovered,algotrade.alert,queue.backlog"

TIER_MAP: dict[str, Tier] = {
    "job.failed": Tier.INFORM,
    "job.completed": Tier.NUDGE,
    "health.degraded": Tier.INTERRUPT,
    "health.recovered": Tier.NUDGE,
    "algotrade.alert": Tier.INTERRUPT,
    "queue.backlog": Tier.INFORM,
}

BACKOFF_BASE = 1.0
BACKOFF_MAX = 60.0


class SSEMonitor(Monitor):
    """Connects to /api/events/stream via SSE. Falls back to polling on disconnect."""

    def __init__(self, fallbacks: list[Monitor] | None = None) -> None:
        self._client: httpx.AsyncClient | None = None
        self._buffer: list[SentinelEvent] = []
        self._listener_task: asyncio.Task[None] | None = None
        self._connected: bool = False
        self._backoff: float = BACKOFF_BASE
        self._fallbacks: list[Monitor] = fallbacks or []

    @property
    def connected(self) -> bool:
        return self._connected

    async def setup(self) -> None:
        self._client = httpx.AsyncClient(base_url=settings.backend_url, timeout=None)
        for fb in self._fallbacks:
            await fb.setup()
        self._listener_task = asyncio.create_task(self._listen())

    async def _listen(self) -> None:
        assert self._client is not None
        while True:
            try:
                async with aconnect_sse(
                    self._client,
                    "GET",
                    "/api/events/stream",
                    params={"types": SSE_TYPES},
                ) as sse:
                    # Check HTTP status before treating as connected
                    if sse.response.status_code != 200:
                        raise httpx.HTTPStatusError(
                            f"SSE endpoint returned {sse.response.status_code}",
                            request=sse.response.request,
                            response=sse.response,
                        )

                    if not self._connected:
                        label = "SSE reconnected" if self._backoff > BACKOFF_BASE else "SSE connected"
                        log.info(label)
                    self._connected = True
                    self._backoff = BACKOFF_BASE

                    async for event in sse.aiter_sse():
                        if event.event == "keepalive":
                            continue
                        tier = TIER_MAP.get(event.event)
                        if tier is None:
                            continue
                        try:
                            data = json.loads(event.data)
                        except (json.JSONDecodeError, TypeError):
                            data = {"raw": event.data}
                        summary = data.get("summary", data.get("message", event.data[:200]))
                        self._buffer.append(
                            SentinelEvent(
                                tier=tier,
                                source=f"sse:{event.event}",
                                payload={"summary": summary, "type": event.event, **data},
                            )
                        )
            except asyncio.CancelledError:
                raise
            except (
                httpx.ConnectError,
                httpx.ReadError,
                httpx.RemoteProtocolError,
                httpx.HTTPStatusError,
            ) as exc:
                if self._connected:
                    log.warning("SSE lost — falling back to polling (%s)", exc)
                    self._connected = False
                log.debug("SSE reconnecting in %.0fs", self._backoff)
                await asyncio.sleep(self._backoff)
                self._backoff = min(self._backoff * 2, BACKOFF_MAX)

    async def check(self) -> list[SentinelEvent]:
        # Drain SSE buffer
        events = list(self._buffer)
        self._buffer.clear()

        # If SSE is down, poll fallbacks
        if not self._connected:
            for fb in self._fallbacks:
                events.extend(await fb.check())

        return events

    async def teardown(self) -> None:
        if self._listener_task:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass
        if self._client:
            await self._client.aclose()
        for fb in self._fallbacks:
            await fb.teardown()
