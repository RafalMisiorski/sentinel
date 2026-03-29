"""Minimal working example — NH events + Telegram push."""

import asyncio
import os

# Configure before importing sentinel modules.
os.environ.setdefault("SENTINEL_NH_URL", "http://localhost:8000")
os.environ.setdefault("SENTINEL_TELEGRAM_BOT_TOKEN", "your-bot-token")
os.environ.setdefault("SENTINEL_TELEGRAM_CHAT_ID", "your-chat-id")

from sentinel.adapters.desktop import DesktopAdapter  # noqa: E402
from sentinel.adapters.telegram import TelegramAdapter  # noqa: E402
from sentinel.core.cortical_filter import CorticalFilter  # noqa: E402
from sentinel.core.event import Decision  # noqa: E402
from sentinel.monitors.nh_events import NHEventsMonitor  # noqa: E402


async def main() -> None:
    monitor = NHEventsMonitor()
    cortical = CorticalFilter()
    adapters = [TelegramAdapter(), DesktopAdapter()]

    for a in adapters:
        await a.setup()
    await monitor.setup()

    print("Listening for NH events... (Ctrl+C to stop)")
    try:
        while True:
            events = await monitor.check()
            for event in events:
                decision = cortical.accept(event)
                if decision == Decision.PUSH:
                    text = event.display_text()
                    for adapter in adapters:
                        await adapter.push(event, text)
            await asyncio.sleep(2)
    except KeyboardInterrupt:
        pass
    finally:
        await monitor.teardown()
        for a in adapters:
            await a.teardown()


if __name__ == "__main__":
    asyncio.run(main())
