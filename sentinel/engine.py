"""Main Sentinel engine loop."""

from __future__ import annotations

import asyncio
import logging

from sentinel.adapters.base import Adapter
from sentinel.adapters.desktop import DesktopAdapter
from sentinel.adapters.telegram import TelegramAdapter
from sentinel.config import settings
from sentinel.core.cortical_filter import CorticalFilter
from sentinel.core.event import Decision
from sentinel.monitors.algotrade import AlertsMonitor
from sentinel.monitors.base import Monitor
from sentinel.monitors.health import HTTPHealthMonitor, JobQueueMonitor
from sentinel.monitors.sse import SSEMonitor

log = logging.getLogger(__name__)


async def _poll_loop(
    monitor: Monitor,
    cortical: CorticalFilter,
    adapters: list[Adapter],
    interval: float,
) -> None:
    """Repeatedly check a monitor and dispatch events."""
    while True:
        try:
            events = await monitor.check()
            for event in events:
                decision = cortical.accept(event)
                log.info(
                    "%s | %s | %s | score=%.2f | %s",
                    event.source,
                    event.tier.name,
                    decision.value,
                    cortical.score(event),
                    event.payload.get("summary", ""),
                )
                if decision == Decision.PUSH:
                    text = event.display_text()
                    await asyncio.gather(
                        *(a.push(event, text) for a in adapters)
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Error in monitor %s", type(monitor).__name__)
        await asyncio.sleep(interval)


async def run() -> None:
    """Start Sentinel — wire monitors, filter, and adapters."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    log.info("Sentinel starting — backend at %s", settings.backend_url)

    cortical = CorticalFilter()

    # SSE is the primary path; polling monitors are fallbacks when SSE is down.
    sse_monitor = SSEMonitor(
        fallbacks=[HTTPHealthMonitor(), JobQueueMonitor()],
    )

    monitors: list[tuple[Monitor, float]] = [
        (sse_monitor, 2.0),       # drain SSE buffer (or poll fallbacks) every 2s
        (AlertsMonitor(), float(settings.poll_interval)),  # no SSE equivalent yet
    ]

    adapters: list[Adapter] = [TelegramAdapter(), DesktopAdapter()]

    # Setup phase
    for adapter in adapters:
        await adapter.setup()
    for monitor, _ in monitors:
        await monitor.setup()

    tg_status = "configured" if settings.telegram_bot_token else "NOT configured"
    log.info(
        "Running %d monitors, %d adapters (Telegram: %s)",
        len(monitors), len(adapters), tg_status,
    )

    tasks = [
        asyncio.create_task(_poll_loop(m, cortical, adapters, interval))
        for m, interval in monitors
    ]

    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        pass
    finally:
        for monitor, _ in monitors:
            await monitor.teardown()
        for adapter in adapters:
            await adapter.teardown()
        log.info("Sentinel stopped")
