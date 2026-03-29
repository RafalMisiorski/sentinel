"""Tests for cortical filter: budget exhaustion, tier scoring, midnight reset."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from sentinel.core.attention_budget import AttentionBudget
from sentinel.core.cortical_filter import CorticalFilter
from sentinel.core.event import Decision, SentinelEvent, Tier


def _make_event(tier: Tier = Tier.INFORM, **kwargs) -> SentinelEvent:
    return SentinelEvent(
        tier=tier,
        source="test",
        payload={"summary": "test event"},
        **kwargs,
    )


@pytest.fixture()
def budget(tmp_path: Path) -> AttentionBudget:
    state = tmp_path / "state.json"
    with patch("sentinel.core.attention_budget.settings") as mock_settings:
        mock_settings.state_file = str(state)
        mock_settings.attention_budget.interrupt = 2
        mock_settings.attention_budget.inform = 3
        mock_settings.attention_budget.nudge = 2
        yield AttentionBudget(state_path=state)


@pytest.fixture()
def cortical(budget: AttentionBudget) -> CorticalFilter:
    return CorticalFilter(budget=budget)


class TestScoring:
    def test_fresh_interrupt_scores_highest(self, cortical: CorticalFilter) -> None:
        event = _make_event(Tier.INTERRUPT)
        score = cortical.score(event)
        assert score > 0.5

    def test_decayed_event_scores_lower(self, cortical: CorticalFilter) -> None:
        fresh = _make_event(Tier.INFORM)
        old = _make_event(Tier.INFORM, timestamp=time.time() - 1800, decay_minutes=30)
        assert cortical.score(fresh) > cortical.score(old)

    def test_fully_decayed_event_drops(self, cortical: CorticalFilter) -> None:
        event = _make_event(
            Tier.NUDGE, timestamp=time.time() - 3600, decay_minutes=30
        )
        decision = cortical.evaluate(event)
        assert decision == Decision.DROP


class TestBudgetExhaustion:
    def test_push_until_budget_empty(self, cortical: CorticalFilter) -> None:
        decisions = []
        for _ in range(5):
            event = _make_event(Tier.INFORM)
            decisions.append(cortical.accept(event))

        pushes = [d for d in decisions if d == Decision.PUSH]
        assert len(pushes) == 3  # budget limit

    def test_interrupt_queues_when_exhausted(self, cortical: CorticalFilter) -> None:
        # Exhaust interrupt budget (limit=2)
        for _ in range(2):
            cortical.accept(_make_event(Tier.INTERRUPT))
        decision = cortical.accept(_make_event(Tier.INTERRUPT))
        assert decision == Decision.QUEUE

    def test_inform_drops_when_exhausted(self, cortical: CorticalFilter) -> None:
        for _ in range(3):
            cortical.accept(_make_event(Tier.INFORM))
        decision = cortical.accept(_make_event(Tier.INFORM))
        assert decision == Decision.DROP


class TestMidnightReset:
    def test_budget_resets_on_new_day(self, tmp_path: Path) -> None:
        state = tmp_path / "state.json"
        # Write stale state from yesterday
        state.write_text(
            json.dumps(
                {
                    "day": "2020-01-01",
                    "counts": {"INTERRUPT": 99, "INFORM": 99, "NUDGE": 99},
                }
            )
        )
        with patch("sentinel.core.attention_budget.settings") as mock:
            mock.state_file = str(state)
            mock.attention_budget.interrupt = 5
            mock.attention_budget.inform = 20
            mock.attention_budget.nudge = 10
            budget = AttentionBudget(state_path=state)

        assert budget.remaining(Tier.INTERRUPT) == 5
        assert budget.remaining(Tier.INFORM) == 20
