"""Example: Sentinel configured for Neural Holding (reference backend).

Neural Holding exposes:
  GET /health                  -> {status, queue_size, governor}
  GET /api/jobs?status=failed  -> [{id, type, description, ...}]
  GET /api/newsfeed/alerts     -> {alerts: [{id, name, priority, trigger_count}]}

All three monitors are wired up. See INTEGRATION_NH.md for SSE upgrade path.
"""

import asyncio
import os

os.environ.setdefault("SENTINEL_BACKEND_URL", "http://localhost:8100")
os.environ.setdefault("SENTINEL_TELEGRAM_BOT_TOKEN", "your-bot-token")
os.environ.setdefault("SENTINEL_TELEGRAM_CHAT_ID", "your-chat-id")

from sentinel.adapters.desktop import DesktopAdapter  # noqa: E402
from sentinel.adapters.telegram import TelegramAdapter  # noqa: E402
from sentinel.core.cortical_filter import CorticalFilter  # noqa: E402
from sentinel.core.event import Decision  # noqa: E402
from sentinel.monitors.algotrade import AlertsMonitor  # noqa: E402
from sentinel.monitors.health import HTTPHealthMonitor, JobQueueMonitor  # noqa: E402


async def main() -> None:
    monitors = [HTTPHealthMonitor(), JobQueueMonitor(), AlertsMonitor()]
    cortical = CorticalFilter()
    adapters = [TelegramAdapter(), DesktopAdapter()]

    for a in adapters:
        await a.setup()
    for m in monitors:
        await m.setup()

    print("Watching Neural Holding... (Ctrl+C to stop)")
    try:
        while True:
            for monitor in monitors:
                events = await monitor.check()
                for event in events:
                    decision = cortical.accept(event)
                    if decision == Decision.PUSH:
                        text = event.display_text()
                        for adapter in adapters:
                            await adapter.push(event, text)
            await asyncio.sleep(30)
    except KeyboardInterrupt:
        pass
    finally:
        for m in monitors:
            await m.teardown()
        for a in adapters:
            await a.teardown()


if __name__ == "__main__":
    asyncio.run(main())
