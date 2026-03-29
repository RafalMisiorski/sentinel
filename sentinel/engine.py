"""Main Sentinel engine loop."""

from __future__ import annotations

import asyncio
import logging

from sentinel.adapters.base import Adapter
from sentinel.adapters.desktop import DesktopAdapter
from sentinel.adapters.telegram import TelegramAdapter
from sentinel.commands.telegram_handler import (
    AlertLedger,
    SnoozeTracker,
    TelegramCommandHandler,
)
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
    snooze: SnoozeTracker | None = None,
) -> None:
    """Repeatedly check a monitor and dispatch events."""
    while True:
        try:
            events = await monitor.check()
            for event in events:
                if snooze and snooze.is_snoozed(event.source):
                    log.debug("Snoozed: %s from %s", event.tier.name, event.source)
                    continue

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
    ledger = AlertLedger()
    snooze = SnoozeTracker()

    # SSE is the primary path; polling monitors are fallbacks when SSE is down.
    sse_monitor = SSEMonitor(
        fallbacks=[HTTPHealthMonitor(), JobQueueMonitor()],
    )

    monitors: list[tuple[Monitor, float]] = [
        (sse_monitor, 2.0),
        (AlertsMonitor(), float(settings.poll_interval)),
    ]

    adapters: list[Adapter] = [
        TelegramAdapter(ledger=ledger),
        DesktopAdapter(),
    ]

    # Command handler shares ledger, budget, and snooze tracker
    cmd_handler = TelegramCommandHandler(
        ledger=ledger,
        budget=cortical.budget,
        snooze_tracker=snooze,
    )

    # Setup phase
    for adapter in adapters:
        await adapter.setup()
    for monitor, _ in monitors:
        await monitor.setup()
    await cmd_handler.setup()

    tg_status = "configured" if settings.telegram_bot_token else "NOT configured"
    log.info(
        "Running %d monitors, %d adapters, commands=%s (Telegram: %s)",
        len(monitors), len(adapters), "on" if settings.telegram_bot_token else "off", tg_status,
    )

    tasks = [
        asyncio.create_task(_poll_loop(m, cortical, adapters, interval, snooze))
        for m, interval in monitors
    ]
    tasks.append(asyncio.create_task(cmd_handler.poll_loop()))

    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        pass
    finally:
        await cmd_handler.teardown()
        for monitor, _ in monitors:
            await monitor.teardown()
        for adapter in adapters:
            await adapter.teardown()
        log.info("Sentinel stopped")
