"""Cortical filter: scores events and decides PUSH / QUEUE / DROP."""

from __future__ import annotations

from sentinel.core.attention_budget import AttentionBudget
from sentinel.core.event import Decision, SentinelEvent, Tier

# Context cost per tier — higher-tier interruptions cost more attention.
CONTEXT_COST = {
    Tier.INTERRUPT: 3.0,
    Tier.INFORM: 1.5,
    Tier.NUDGE: 1.0,
}

PUSH_THRESHOLD = 0.5


class CorticalFilter:
    """Decides whether an event deserves a push right now."""

    def __init__(self, budget: AttentionBudget | None = None) -> None:
        self.budget = budget or AttentionBudget()

    def score(self, event: SentinelEvent) -> float:
        """push_score = urgency * (1 / context_cost) * decay_rate"""
        cost = CONTEXT_COST.get(event.tier, 1.0)
        return event.urgency * (1.0 / cost) * event.decay_rate

    def evaluate(self, event: SentinelEvent) -> Decision:
        sc = self.score(event)

        if sc < PUSH_THRESHOLD:
            return Decision.DROP

        if self.budget.remaining(event.tier) <= 0:
            # Budget exhausted — INTERRUPT still queues, others drop.
            return Decision.QUEUE if event.tier == Tier.INTERRUPT else Decision.DROP

        return Decision.PUSH

    def accept(self, event: SentinelEvent) -> Decision:
        """Evaluate and spend budget if pushing."""
        decision = self.evaluate(event)
        if decision == Decision.PUSH:
            self.budget.spend(event.tier)
        return decision
