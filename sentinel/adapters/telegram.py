"""Telegram Bot adapter — sends push notifications via Bot API."""

from __future__ import annotations

import logging

import httpx

from sentinel.adapters.base import Adapter
from sentinel.config import settings
from sentinel.core.event import SentinelEvent, Tier

log = logging.getLogger(__name__)

TIER_EMOJI = {
    Tier.INTERRUPT: "\U0001f534",  # red circle
    Tier.INFORM: "\U0001f7e1",     # yellow circle
    Tier.NUDGE: "\U0001f535",      # blue circle
}


class TelegramAdapter(Adapter):
    """Sends messages via Telegram Bot API."""

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    async def setup(self) -> None:
        self._client = httpx.AsyncClient(timeout=10.0)

    async def push(self, event: SentinelEvent, display_text: str) -> None:
        if not settings.telegram_bot_token or not settings.telegram_chat_id:
            log.warning("Telegram not configured — skipping push")
            return
        assert self._client is not None
        emoji = TIER_EMOJI.get(event.tier, "")
        text = f"{emoji} {display_text}"
        url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
        try:
            resp = await self._client.post(
                url,
                json={"chat_id": settings.telegram_chat_id, "text": text},
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            log.error("Telegram send failed: %s", exc)

    async def teardown(self) -> None:
        if self._client:
            await self._client.aclose()
