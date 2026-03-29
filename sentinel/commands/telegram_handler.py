"""Telegram command handler — listens for replies to alerts and executes commands."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from typing import Any

import httpx

from sentinel.config import settings
from sentinel.core.attention_budget import AttentionBudget
from sentinel.core.event import SentinelEvent, Tier

log = logging.getLogger(__name__)

HELP_TEXT = (
    "Commands (reply to an alert):\n"
    "  ok / ack — acknowledge\n"
    "  more / ? — expand details\n"
    "  do / execute — trigger default action\n"
    "  snooze [N] — suppress source for N min (default 30)\n"
    "\nGlobal commands:\n"
    "  budget — attention budget status\n"
    "  status — backend daily context summary"
)

MAX_TRACKED = 50


class AlertLedger:
    """Maps Telegram message_id -> SentinelEvent (FIFO, max size)."""

    def __init__(self, maxlen: int = MAX_TRACKED) -> None:
        self._map: OrderedDict[int, SentinelEvent] = OrderedDict()
        self._maxlen = maxlen
        self._acknowledged: set[int] = set()

    def track(self, message_id: int, event: SentinelEvent) -> None:
        self._map[message_id] = event
        while len(self._map) > self._maxlen:
            self._map.popitem(last=False)

    def get(self, message_id: int) -> SentinelEvent | None:
        return self._map.get(message_id)

    def acknowledge(self, message_id: int) -> bool:
        if message_id in self._map:
            self._acknowledged.add(message_id)
            return True
        return False

    def is_acknowledged(self, message_id: int) -> bool:
        return message_id in self._acknowledged


class SnoozeTracker:
    """Tracks snoozed sources with expiry times."""

    def __init__(self) -> None:
        self._snoozes: dict[str, float] = {}

    def snooze(self, source: str, minutes: float = 30) -> None:
        self._snoozes[source] = time.time() + minutes * 60

    def is_snoozed(self, source: str) -> bool:
        expiry = self._snoozes.get(source)
        if expiry is None:
            return False
        if time.time() >= expiry:
            del self._snoozes[source]
            return False
        return True

    def active(self) -> dict[str, float]:
        """Return {source: remaining_minutes} for active snoozes."""
        now = time.time()
        result = {}
        expired = []
        for source, expiry in self._snoozes.items():
            remaining = (expiry - now) / 60
            if remaining > 0:
                result[source] = remaining
            else:
                expired.append(source)
        for s in expired:
            del self._snoozes[s]
        return result


class TelegramCommandHandler:
    """Polls getUpdates for reply commands and dispatches them."""

    def __init__(
        self,
        ledger: AlertLedger,
        budget: AttentionBudget,
        snooze_tracker: SnoozeTracker | None = None,
    ) -> None:
        self.ledger = ledger
        self.budget = budget
        self.snooze = snooze_tracker or SnoozeTracker()
        self._client: httpx.AsyncClient | None = None
        self._offset: int = 0

    async def setup(self) -> None:
        self._client = httpx.AsyncClient(timeout=30.0)

    async def teardown(self) -> None:
        if self._client:
            await self._client.aclose()

    async def poll_loop(self) -> None:
        """Long-poll getUpdates and handle commands forever."""
        if not settings.telegram_bot_token:
            log.warning("Telegram not configured — command handler disabled")
            return
        while True:
            try:
                await self._poll_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Telegram command poll error")
                await asyncio.sleep(5)

    async def _poll_once(self) -> None:
        assert self._client is not None
        url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/getUpdates"
        resp = await self._client.get(
            url,
            params={"offset": self._offset, "timeout": 20},
            timeout=30.0,
        )
        resp.raise_for_status()
        data = resp.json()

        for update in data.get("result", []):
            self._offset = update["update_id"] + 1
            msg = update.get("message")
            if not msg:
                continue
            chat_id = str(msg.get("chat", {}).get("id", ""))
            if chat_id != settings.telegram_chat_id:
                continue
            text = (msg.get("text") or "").strip().lower()
            if not text:
                continue

            reply_to = msg.get("reply_to_message", {}).get("message_id")
            await self._dispatch(text, reply_to, chat_id)

    async def _dispatch(self, text: str, reply_to: int | None, chat_id: str) -> None:
        # Global commands (no reply needed)
        if text == "budget":
            await self._cmd_budget(chat_id)
            return
        if text == "status":
            await self._cmd_status(chat_id)
            return

        # Reply-based commands
        if reply_to is None:
            if text in ("ok", "ack", "more", "?", "do", "execute") or text.startswith("snooze"):
                await self._reply(chat_id, "Reply to an alert message to use this command.")
            else:
                await self._reply(chat_id, HELP_TEXT)
            return

        event = self.ledger.get(reply_to)
        if event is None:
            await self._reply(chat_id, "Can't find the original alert. It may have expired from tracking.")
            return

        if text in ("ok", "ack"):
            await self._cmd_ack(chat_id, reply_to, event)
        elif text in ("more", "?"):
            await self._cmd_more(chat_id, event)
        elif text in ("do", "execute"):
            await self._cmd_execute(chat_id, event)
        elif text.startswith("snooze"):
            await self._cmd_snooze(chat_id, text, event)
        else:
            await self._reply(chat_id, HELP_TEXT)

    async def _cmd_ack(self, chat_id: str, msg_id: int, event: SentinelEvent) -> None:
        self.ledger.acknowledge(msg_id)
        log.info("ACK: %s from %s", event.tier.name, event.source)
        await self._reply(chat_id, f"Acknowledged: {event.source}")

    async def _cmd_more(self, chat_id: str, event: SentinelEvent) -> None:
        lines = [f"[{event.tier.name}] {event.source}"]
        for k, v in event.payload.items():
            lines.append(f"  {k}: {v}")
        lines.append(f"  age: {event.age_minutes:.1f}m")
        lines.append(f"  decay: {event.decay_rate:.2f}")
        await self._reply(chat_id, "\n".join(lines))

    async def _cmd_execute(self, chat_id: str, event: SentinelEvent) -> None:
        action = event.payload.get("default_action")
        if not action:
            await self._reply(chat_id, "No default action defined for this event.")
            return
        log.info("EXECUTE: %s for %s", action, event.source)
        await self._reply(chat_id, f"Executing: {action}")

    async def _cmd_snooze(self, chat_id: str, text: str, event: SentinelEvent) -> None:
        parts = text.split()
        minutes = 30
        if len(parts) >= 2:
            try:
                minutes = int(parts[1])
            except ValueError:
                await self._reply(chat_id, "Usage: snooze [minutes]")
                return
        self.snooze.snooze(event.source, minutes)
        log.info("SNOOZE: %s for %dm", event.source, minutes)
        await self._reply(chat_id, f"Snoozed {event.source} for {minutes}m")

    async def _cmd_budget(self, chat_id: str) -> None:
        lines = ["Attention Budget:"]
        for tier in Tier:
            remaining = self.budget.remaining(tier)
            lines.append(f"  {tier.name}: {remaining} remaining")
        snoozes = self.snooze.active()
        if snoozes:
            lines.append("Active snoozes:")
            for src, mins in snoozes.items():
                lines.append(f"  {src}: {mins:.0f}m left")
        await self._reply(chat_id, "\n".join(lines))

    async def _cmd_status(self, chat_id: str) -> None:
        assert self._client is not None
        try:
            resp = await self._client.get(
                f"{settings.backend_url}/api/context/daily", timeout=10.0
            )
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            await self._reply(chat_id, f"Backend unavailable: {exc}")
            return

        sections = data.get("sections", data)
        if isinstance(sections, dict):
            lines = ["Daily Context:"]
            for key, val in sections.items():
                if isinstance(val, dict):
                    summary = val.get("summary", str(val)[:100])
                else:
                    summary = str(val)[:100]
                lines.append(f"  {key}: {summary}")
            await self._reply(chat_id, "\n".join(lines))
        else:
            await self._reply(chat_id, str(data)[:1000])

    async def _reply(self, chat_id: str, text: str) -> None:
        assert self._client is not None
        url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
        try:
            await self._client.post(url, json={"chat_id": chat_id, "text": text})
        except httpx.HTTPError as exc:
            log.error("Telegram reply failed: %s", exc)
