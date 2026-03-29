"""Example: point Sentinel at any HTTP backend with health + jobs endpoints.

Your backend needs:
  GET /health                  -> {"status": "ok", "queue_size": 3}
  GET /api/jobs?status=failed  -> [{"id": "...", "description": "..."}]

That's it. Sentinel handles the rest.
"""

import asyncio
import os

# Point at your backend.
os.environ.setdefault("SENTINEL_BACKEND_URL", "http://localhost:8080")
os.environ.setdefault("SENTINEL_TELEGRAM_BOT_TOKEN", "your-bot-token")
os.environ.setdefault("SENTINEL_TELEGRAM_CHAT_ID", "your-chat-id")

from sentinel.adapters.desktop import DesktopAdapter  # noqa: E402
from sentinel.adapters.telegram import TelegramAdapter  # noqa: E402
from sentinel.core.cortical_filter import CorticalFilter  # noqa: E402
from sentinel.core.event import Decision  # noqa: E402
from sentinel.monitors.health import HTTPHealthMonitor, JobQueueMonitor  # noqa: E402


async def main() -> None:
    monitors = [HTTPHealthMonitor(), JobQueueMonitor()]
    cortical = CorticalFilter()
    adapters = [TelegramAdapter(), DesktopAdapter()]

    for a in adapters:
        await a.setup()
    for m in monitors:
        await m.setup()

    print("Watching backend... (Ctrl+C to stop)")
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
