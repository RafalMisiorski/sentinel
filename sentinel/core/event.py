"""Core event model flowing through the Sentinel pipeline."""

from __future__ import annotations

import enum
import time
from typing import Any

from pydantic import BaseModel, Field


class Tier(enum.IntEnum):
    """Urgency tier — lower number = more urgent."""

    INTERRUPT = 1  # Drop everything
    INFORM = 2     # Worth knowing now
    NUDGE = 3      # Batch-friendly


class Decision(enum.Enum):
    PUSH = "PUSH"
    QUEUE = "QUEUE"
    DROP = "DROP"


class SentinelEvent(BaseModel):
    tier: Tier
    source: str
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: float = Field(default_factory=time.time)
    decay_minutes: float = 30.0

    @property
    def urgency(self) -> float:
        """Higher urgency for lower tier numbers."""
        return 4 - self.tier  # INTERRUPT=3, INFORM=2, NUDGE=1

    @property
    def age_minutes(self) -> float:
        return (time.time() - self.timestamp) / 60.0

    @property
    def decay_rate(self) -> float:
        """1.0 when fresh, decays toward 0 as age approaches decay_minutes."""
        ratio = self.age_minutes / self.decay_minutes
        return max(0.0, 1.0 - ratio)

    def display_text(self) -> str:
        summary = self.payload.get("summary", str(self.payload))
        return f"[{self.tier.name}] {self.source}: {summary}"
