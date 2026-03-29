"""Tests for Telegram command handler."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sentinel.core.attention_budget import AttentionBudget
from sentinel.core.event import SentinelEvent, Tier
from sentinel.commands.telegram_handler import (
    AlertLedger,
    SnoozeTracker,
    TelegramCommandHandler,
    HELP_TEXT,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_event(source: str = "sse:job.failed", tier: Tier = Tier.INFORM) -> SentinelEvent:
    return SentinelEvent(
        tier=tier,
        source=source,
        payload={"summary": "job_abc failed", "job_id": "job_abc", "type": "job.failed"},
    )


@pytest.fixture()
def ledger() -> AlertLedger:
    lg = AlertLedger()
    lg.track(100, _make_event())
    return lg


@pytest.fixture()
def budget(tmp_path) -> AttentionBudget:
    state = tmp_path / "state.json"
    with patch("sentinel.core.attention_budget.settings") as mock:
        mock.state_file = str(state)
        mock.attention_budget.interrupt = 5
        mock.attention_budget.inform = 20
        mock.attention_budget.nudge = 10
        yield AttentionBudget(state_path=state)


@pytest.fixture()
def snooze() -> SnoozeTracker:
    return SnoozeTracker()


@pytest.fixture()
def handler(ledger, budget, snooze) -> TelegramCommandHandler:
    h = TelegramCommandHandler(ledger=ledger, budget=budget, snooze_tracker=snooze)
    h._client = AsyncMock()
    h._client.post = AsyncMock()
    return h


def _make_update(text: str, reply_to: int | None = None, update_id: int = 1, chat_id: str = "123") -> dict:
    msg: dict = {
        "message_id": 200,
        "chat": {"id": int(chat_id)},
        "text": text,
    }
    if reply_to is not None:
        msg["reply_to_message"] = {"message_id": reply_to}
    return {"update_id": update_id, "message": msg}


# ---------------------------------------------------------------------------
# AlertLedger
# ---------------------------------------------------------------------------

class TestAlertLedger:
    def test_track_and_get(self) -> None:
        lg = AlertLedger()
        ev = _make_event()
        lg.track(42, ev)
        assert lg.get(42) is ev
        assert lg.get(999) is None

    def test_fifo_eviction(self) -> None:
        lg = AlertLedger(maxlen=3)
        for i in range(5):
            lg.track(i, _make_event())
        assert lg.get(0) is None
        assert lg.get(1) is None
        assert lg.get(2) is not None
        assert lg.get(4) is not None

    def test_acknowledge(self) -> None:
        lg = AlertLedger()
        ev = _make_event()
        lg.track(10, ev)
        assert not lg.is_acknowledged(10)
        assert lg.acknowledge(10)
        assert lg.is_acknowledged(10)
        assert not lg.acknowledge(999)


# ---------------------------------------------------------------------------
# SnoozeTracker
# ---------------------------------------------------------------------------

class TestSnoozeTracker:
    def test_snooze_and_check(self) -> None:
        s = SnoozeTracker()
        s.snooze("health", 30)
        assert s.is_snoozed("health")
        assert not s.is_snoozed("jobs")

    def test_expired_snooze(self) -> None:
        s = SnoozeTracker()
        s._snoozes["health"] = time.time() - 1  # already expired
        assert not s.is_snoozed("health")

    def test_active_snoozes(self) -> None:
        s = SnoozeTracker()
        s.snooze("health", 30)
        s._snoozes["old"] = time.time() - 1  # expired
        active = s.active()
        assert "health" in active
        assert "old" not in active


# ---------------------------------------------------------------------------
# Command dispatch
# ---------------------------------------------------------------------------

class TestAckCommand:
    @pytest.mark.asyncio
    async def test_ok_marks_acknowledged(self, handler, ledger) -> None:
        with patch("sentinel.commands.telegram_handler.settings") as mock_s:
            mock_s.telegram_bot_token = "tok"
            mock_s.telegram_chat_id = "123"
            mock_s.backend_url = "http://localhost:8100"
            await handler._dispatch("ok", 100, "123")

        assert ledger.is_acknowledged(100)
        handler._client.post.assert_awaited_once()
        call_kwargs = handler._client.post.call_args
        assert "Acknowledged" in call_kwargs.kwargs["json"]["text"]

    @pytest.mark.asyncio
    async def test_ack_same_as_ok(self, handler, ledger) -> None:
        with patch("sentinel.commands.telegram_handler.settings") as mock_s:
            mock_s.telegram_bot_token = "tok"
            mock_s.telegram_chat_id = "123"
            mock_s.backend_url = "http://localhost:8100"
            await handler._dispatch("ack", 100, "123")

        assert ledger.is_acknowledged(100)


class TestSnoozeCommand:
    @pytest.mark.asyncio
    async def test_snooze_default_30(self, handler, snooze) -> None:
        with patch("sentinel.commands.telegram_handler.settings") as mock_s:
            mock_s.telegram_bot_token = "tok"
            mock_s.telegram_chat_id = "123"
            mock_s.backend_url = "http://localhost:8100"
            await handler._dispatch("snooze", 100, "123")

        assert snooze.is_snoozed("sse:job.failed")

    @pytest.mark.asyncio
    async def test_snooze_custom_minutes(self, handler, snooze) -> None:
        with patch("sentinel.commands.telegram_handler.settings") as mock_s:
            mock_s.telegram_bot_token = "tok"
            mock_s.telegram_chat_id = "123"
            mock_s.backend_url = "http://localhost:8100"
            await handler._dispatch("snooze 60", 100, "123")

        assert snooze.is_snoozed("sse:job.failed")
        call_kwargs = handler._client.post.call_args
        assert "60m" in call_kwargs.kwargs["json"]["text"]


class TestStatusCommand:
    @pytest.mark.asyncio
    async def test_status_calls_backend(self, handler) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "sections": {
                "algotrade": {"summary": "1 position open"},
                "jobs": {"summary": "3 in queue"},
            }
        }
        mock_resp.raise_for_status = MagicMock()
        handler._client.get = AsyncMock(return_value=mock_resp)

        with patch("sentinel.commands.telegram_handler.settings") as mock_s:
            mock_s.telegram_bot_token = "tok"
            mock_s.telegram_chat_id = "123"
            mock_s.backend_url = "http://localhost:8100"
            await handler._dispatch("status", None, "123")

        handler._client.get.assert_awaited_once()
        call_kwargs = handler._client.post.call_args
        reply_text = call_kwargs.kwargs["json"]["text"]
        assert "algotrade" in reply_text
        assert "1 position" in reply_text


class TestBudgetCommand:
    @pytest.mark.asyncio
    async def test_budget_shows_remaining(self, handler, budget) -> None:
        with patch("sentinel.commands.telegram_handler.settings") as mock_s:
            mock_s.telegram_bot_token = "tok"
            mock_s.telegram_chat_id = "123"
            mock_s.backend_url = "http://localhost:8100"
            await handler._dispatch("budget", None, "123")

        call_kwargs = handler._client.post.call_args
        reply_text = call_kwargs.kwargs["json"]["text"]
        assert "INTERRUPT" in reply_text
        assert "5 remaining" in reply_text


class TestMoreCommand:
    @pytest.mark.asyncio
    async def test_more_expands_payload(self, handler) -> None:
        with patch("sentinel.commands.telegram_handler.settings") as mock_s:
            mock_s.telegram_bot_token = "tok"
            mock_s.telegram_chat_id = "123"
            mock_s.backend_url = "http://localhost:8100"
            await handler._dispatch("more", 100, "123")

        call_kwargs = handler._client.post.call_args
        reply_text = call_kwargs.kwargs["json"]["text"]
        assert "job_id" in reply_text
        assert "job_abc" in reply_text


class TestUnknownCommand:
    @pytest.mark.asyncio
    async def test_unknown_gets_help(self, handler) -> None:
        with patch("sentinel.commands.telegram_handler.settings") as mock_s:
            mock_s.telegram_bot_token = "tok"
            mock_s.telegram_chat_id = "123"
            mock_s.backend_url = "http://localhost:8100"
            await handler._dispatch("foobar", None, "123")

        call_kwargs = handler._client.post.call_args
        reply_text = call_kwargs.kwargs["json"]["text"]
        assert "ok / ack" in reply_text


class TestReplyToNonAlert:
    @pytest.mark.asyncio
    async def test_reply_to_unknown_msg(self, handler) -> None:
        with patch("sentinel.commands.telegram_handler.settings") as mock_s:
            mock_s.telegram_bot_token = "tok"
            mock_s.telegram_chat_id = "123"
            mock_s.backend_url = "http://localhost:8100"
            await handler._dispatch("ok", 999, "123")

        call_kwargs = handler._client.post.call_args
        assert "expired" in call_kwargs.kwargs["json"]["text"]
