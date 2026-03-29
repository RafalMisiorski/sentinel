"""Tests for monitors: mock health, mock failed jobs, mock alerts."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from sentinel.core.event import Tier
from sentinel.monitors.algotrade import AlertsMonitor
from sentinel.monitors.health import HTTPHealthMonitor, JobQueueMonitor


class TestHTTPHealthMonitor:
    @pytest.mark.asyncio
    async def test_healthy_returns_empty(self) -> None:
        monitor = HTTPHealthMonitor()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"status": "ok", "queue_size": 3}
        mock_resp.raise_for_status = MagicMock()
        monitor._client = AsyncMock()
        monitor._client.get.return_value = mock_resp

        events = await monitor.check()
        assert events == []

    @pytest.mark.asyncio
    async def test_degraded_status_triggers_interrupt(self) -> None:
        monitor = HTTPHealthMonitor()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"status": "degraded", "queue_size": 1}
        mock_resp.raise_for_status = MagicMock()
        monitor._client = AsyncMock()
        monitor._client.get.return_value = mock_resp

        events = await monitor.check()
        assert len(events) == 1
        assert events[0].tier == Tier.INTERRUPT
        assert "degraded" in events[0].payload["summary"]

    @pytest.mark.asyncio
    async def test_unreachable_triggers_interrupt(self) -> None:
        monitor = HTTPHealthMonitor()
        monitor._client = AsyncMock()
        monitor._client.get.side_effect = httpx.ConnectError("refused")

        events = await monitor.check()
        assert len(events) == 1
        assert events[0].tier == Tier.INTERRUPT
        assert "unreachable" in events[0].payload["summary"]

    @pytest.mark.asyncio
    async def test_queue_backlog_triggers_inform(self) -> None:
        monitor = HTTPHealthMonitor()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"status": "ok", "queue_size": 15}
        mock_resp.raise_for_status = MagicMock()
        monitor._client = AsyncMock()
        monitor._client.get.return_value = mock_resp

        events = await monitor.check()
        assert len(events) == 1
        assert events[0].tier == Tier.INFORM
        assert "backlog" in events[0].payload["summary"]


class TestJobQueueMonitor:
    @pytest.mark.asyncio
    async def test_first_run_baselines(self) -> None:
        monitor = JobQueueMonitor()
        mock_resp = MagicMock()
        mock_resp.json.return_value = [{"id": "job_1", "type": "test", "description": "old fail"}]
        mock_resp.raise_for_status = MagicMock()
        monitor._client = AsyncMock()
        monitor._client.get.return_value = mock_resp

        events = await monitor.check()
        assert events == []  # First run — baseline only

    @pytest.mark.asyncio
    async def test_new_failure_triggers_inform(self) -> None:
        monitor = JobQueueMonitor()
        monitor._client = AsyncMock()

        # First run — baseline
        resp1 = MagicMock()
        resp1.json.return_value = [{"id": "job_1", "type": "test", "description": "old"}]
        resp1.raise_for_status = MagicMock()
        monitor._client.get.return_value = resp1
        await monitor.check()

        # Second run — new failure
        resp2 = MagicMock()
        resp2.json.return_value = [
            {"id": "job_1", "type": "test", "description": "old"},
            {"id": "job_2", "type": "fix", "description": "broken deploy"},
        ]
        resp2.raise_for_status = MagicMock()
        monitor._client.get.return_value = resp2

        events = await monitor.check()
        assert len(events) == 1
        assert events[0].tier == Tier.INFORM
        assert "broken deploy" in events[0].payload["summary"]


class TestAlertsMonitor:
    @pytest.mark.asyncio
    async def test_first_run_baselines(self) -> None:
        monitor = AlertsMonitor()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "alerts": [{"id": "a1", "name": "Test", "priority": "high", "trigger_count": 5, "enabled": True}]
        }
        mock_resp.raise_for_status = MagicMock()
        monitor._client = AsyncMock()
        monitor._client.get.return_value = mock_resp

        events = await monitor.check()
        assert events == []

    @pytest.mark.asyncio
    async def test_new_trigger_creates_event(self) -> None:
        monitor = AlertsMonitor()
        monitor._client = AsyncMock()

        # Baseline
        resp1 = MagicMock()
        resp1.json.return_value = {
            "alerts": [{"id": "a1", "name": "Cyber", "priority": "critical", "trigger_count": 5, "enabled": True}]
        }
        resp1.raise_for_status = MagicMock()
        monitor._client.get.return_value = resp1
        await monitor.check()

        # New triggers
        resp2 = MagicMock()
        resp2.json.return_value = {
            "alerts": [{"id": "a1", "name": "Cyber", "priority": "critical", "trigger_count": 7, "enabled": True}]
        }
        resp2.raise_for_status = MagicMock()
        monitor._client.get.return_value = resp2

        events = await monitor.check()
        assert len(events) == 1
        assert events[0].tier == Tier.INTERRUPT  # critical priority
        assert events[0].payload["new_triggers"] == 2

    @pytest.mark.asyncio
    async def test_disabled_alert_ignored(self) -> None:
        monitor = AlertsMonitor()
        monitor._first_run = False
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "alerts": [{"id": "a1", "name": "Off", "priority": "critical", "trigger_count": 99, "enabled": False}]
        }
        mock_resp.raise_for_status = MagicMock()
        monitor._client = AsyncMock()
        monitor._client.get.return_value = mock_resp

        events = await monitor.check()
        assert events == []

    @pytest.mark.asyncio
    async def test_http_error_returns_empty(self) -> None:
        monitor = AlertsMonitor()
        monitor._client = AsyncMock()
        monitor._client.get.side_effect = httpx.ConnectError("refused")

        events = await monitor.check()
        assert events == []
