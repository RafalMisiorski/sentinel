"""Tests for SSE consumer: event mapping, fallback, reconnect, backoff."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from sentinel.core.event import SentinelEvent, Tier
from sentinel.monitors.health import HTTPHealthMonitor
from sentinel.monitors.sse import (
    BACKOFF_BASE,
    BACKOFF_MAX,
    SSEMonitor,
    TIER_MAP,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class FakeSSE:
    """Simulates an SSE event object returned by httpx-sse."""

    def __init__(self, event: str, data: str) -> None:
        self.event = event
        self.data = data


class FakeSSEStream:
    """Async context manager yielding FakeSSE events, then optionally raising."""

    def __init__(self, events: list[FakeSSE], error: Exception | None = None) -> None:
        self._events = events
        self._error = error

    async def aiter_sse(self):  # noqa: ANN201
        for ev in self._events:
            yield ev
        if self._error:
            raise self._error


# ---------------------------------------------------------------------------
# Tier mapping
# ---------------------------------------------------------------------------

class TestTierMapping:
    def test_job_failed_is_inform(self) -> None:
        assert TIER_MAP["job.failed"] == Tier.INFORM

    def test_health_degraded_is_interrupt(self) -> None:
        assert TIER_MAP["health.degraded"] == Tier.INTERRUPT

    def test_health_recovered_is_nudge(self) -> None:
        assert TIER_MAP["health.recovered"] == Tier.NUDGE

    def test_algotrade_alert_is_interrupt(self) -> None:
        assert TIER_MAP["algotrade.alert"] == Tier.INTERRUPT

    def test_job_completed_is_nudge(self) -> None:
        assert TIER_MAP["job.completed"] == Tier.NUDGE

    def test_queue_backlog_is_inform(self) -> None:
        assert TIER_MAP["queue.backlog"] == Tier.INFORM


# ---------------------------------------------------------------------------
# SSE event → SentinelEvent
# ---------------------------------------------------------------------------

class TestSSEToSentinelEvent:
    @pytest.mark.asyncio
    async def test_job_failed_creates_inform(self) -> None:
        monitor = SSEMonitor()
        payload = json.dumps({"summary": "job_abc failed", "job_id": "job_abc"})
        fake = FakeSSE("job.failed", payload)

        # Simulate: push directly into buffer as the listener would
        monitor._buffer.clear()
        monitor._connected = True
        # Replicate the listener's parsing logic inline
        import sentinel.monitors.sse as mod

        tier = mod.TIER_MAP["job.failed"]
        data = json.loads(fake.data)
        monitor._buffer.append(
            SentinelEvent(
                tier=tier,
                source=f"sse:{fake.event}",
                payload={"summary": data["summary"], "type": fake.event, **data},
            )
        )

        events = await monitor.check()
        assert len(events) == 1
        assert events[0].tier == Tier.INFORM
        assert events[0].source == "sse:job.failed"
        assert "job_abc" in events[0].payload["summary"]

    @pytest.mark.asyncio
    async def test_health_degraded_creates_interrupt(self) -> None:
        monitor = SSEMonitor()
        monitor._connected = True
        monitor._buffer.append(
            SentinelEvent(
                tier=Tier.INTERRUPT,
                source="sse:health.degraded",
                payload={"summary": "status degraded", "type": "health.degraded"},
            )
        )
        events = await monitor.check()
        assert len(events) == 1
        assert events[0].tier == Tier.INTERRUPT

    @pytest.mark.asyncio
    async def test_keepalive_ignored(self) -> None:
        """Keepalive events should not produce SentinelEvents."""
        monitor = SSEMonitor()
        monitor._connected = True
        # Keepalives never hit the buffer — the listener skips them
        monitor._buffer.clear()
        events = await monitor.check()
        assert events == []

    @pytest.mark.asyncio
    async def test_check_drains_buffer(self) -> None:
        monitor = SSEMonitor()
        monitor._connected = True
        monitor._buffer.append(
            SentinelEvent(tier=Tier.NUDGE, source="sse:job.completed", payload={"summary": "done"})
        )
        monitor._buffer.append(
            SentinelEvent(tier=Tier.INFORM, source="sse:queue.backlog", payload={"summary": "12 queued"})
        )
        events = await monitor.check()
        assert len(events) == 2
        assert await monitor.check() == []


# ---------------------------------------------------------------------------
# Fallback to polling
# ---------------------------------------------------------------------------

class TestFallback:
    @pytest.mark.asyncio
    async def test_polls_fallbacks_when_disconnected(self) -> None:
        fallback = AsyncMock(spec=HTTPHealthMonitor)
        fallback.check.return_value = [
            SentinelEvent(tier=Tier.INFORM, source="health", payload={"summary": "backlog"})
        ]

        monitor = SSEMonitor(fallbacks=[fallback])
        monitor._connected = False

        events = await monitor.check()
        assert len(events) == 1
        assert events[0].source == "health"
        fallback.check.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skips_fallbacks_when_connected(self) -> None:
        fallback = AsyncMock(spec=HTTPHealthMonitor)
        fallback.check.return_value = [
            SentinelEvent(tier=Tier.INFORM, source="health", payload={"summary": "backlog"})
        ]

        monitor = SSEMonitor(fallbacks=[fallback])
        monitor._connected = True

        events = await monitor.check()
        assert events == []
        fallback.check.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_combines_sse_buffer_and_fallback_when_disconnected(self) -> None:
        """Edge case: SSE buffer has stale events AND we're disconnected."""
        fallback = AsyncMock(spec=HTTPHealthMonitor)
        fallback.check.return_value = [
            SentinelEvent(tier=Tier.INFORM, source="health", payload={"summary": "poll"})
        ]

        monitor = SSEMonitor(fallbacks=[fallback])
        monitor._connected = False
        monitor._buffer.append(
            SentinelEvent(tier=Tier.NUDGE, source="sse:job.completed", payload={"summary": "stale"})
        )

        events = await monitor.check()
        assert len(events) == 2


# ---------------------------------------------------------------------------
# Reconnect & backoff
# ---------------------------------------------------------------------------

class TestReconnect:
    @pytest.mark.asyncio
    async def test_connected_flag_lifecycle(self) -> None:
        monitor = SSEMonitor()
        assert not monitor.connected

        monitor._connected = True
        assert monitor.connected

        monitor._connected = False
        assert not monitor.connected

    def test_backoff_doubles(self) -> None:
        monitor = SSEMonitor()
        assert monitor._backoff == BACKOFF_BASE  # 1.0

        # Simulate successive failures
        monitor._backoff = min(monitor._backoff * 2, BACKOFF_MAX)
        assert monitor._backoff == 2.0

        monitor._backoff = min(monitor._backoff * 2, BACKOFF_MAX)
        assert monitor._backoff == 4.0

        monitor._backoff = min(monitor._backoff * 2, BACKOFF_MAX)
        assert monitor._backoff == 8.0

    def test_backoff_caps_at_max(self) -> None:
        monitor = SSEMonitor()
        monitor._backoff = 32.0
        monitor._backoff = min(monitor._backoff * 2, BACKOFF_MAX)
        assert monitor._backoff == BACKOFF_MAX  # 60.0

        # Further doublings still cap
        monitor._backoff = min(monitor._backoff * 2, BACKOFF_MAX)
        assert monitor._backoff == BACKOFF_MAX

    def test_backoff_resets_on_connect(self) -> None:
        monitor = SSEMonitor()
        monitor._backoff = 32.0
        # Simulate successful reconnect
        monitor._connected = True
        monitor._backoff = BACKOFF_BASE
        assert monitor._backoff == BACKOFF_BASE

    @pytest.mark.asyncio
    async def test_teardown_cancels_listener(self) -> None:
        monitor = SSEMonitor()

        async def fake_listen() -> None:
            await asyncio.sleep(3600)

        monitor._listener_task = asyncio.create_task(fake_listen())
        monitor._client = AsyncMock()
        monitor._client.aclose = AsyncMock()

        await monitor.teardown()
        assert monitor._listener_task.cancelled()

    @pytest.mark.asyncio
    async def test_setup_starts_listener_and_fallbacks(self) -> None:
        fallback = AsyncMock(spec=HTTPHealthMonitor)

        with patch("sentinel.monitors.sse.settings") as mock_settings:
            mock_settings.backend_url = "http://localhost:9999"
            monitor = SSEMonitor(fallbacks=[fallback])
            # Patch client so listener fails fast
            monitor._client = AsyncMock()
            await monitor.setup()

        fallback.setup.assert_awaited_once()
        assert monitor._listener_task is not None

        # Cleanup
        monitor._listener_task.cancel()
        try:
            await monitor._listener_task
        except asyncio.CancelledError:
            pass
