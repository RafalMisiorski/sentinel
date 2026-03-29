"""Daily attention budget tracker with midnight reset."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from sentinel.config import settings
from sentinel.core.event import Tier


class AttentionBudget:
    """Tracks how many pushes have been spent today per tier."""

    def __init__(self, state_path: str | Path | None = None) -> None:
        self._path = Path(state_path or settings.state_file)
        self._limits = {
            Tier.INTERRUPT: settings.attention_budget.interrupt,
            Tier.INFORM: settings.attention_budget.inform,
            Tier.NUDGE: settings.attention_budget.nudge,
        }
        self._counts: dict[Tier, int] = {t: 0 for t in Tier}
        self._day: str = self._today()
        self._load()

    @staticmethod
    def _today() -> str:
        return time.strftime("%Y-%m-%d")

    def _load(self) -> None:
        if not self._path.exists():
            return
        data: dict[str, Any] = json.loads(self._path.read_text())
        if data.get("day") != self._today():
            return  # stale — start fresh
        for tier in Tier:
            self._counts[tier] = data.get("counts", {}).get(tier.name, 0)
        self._day = data["day"]

    def _save(self) -> None:
        data = {
            "day": self._day,
            "counts": {t.name: c for t, c in self._counts.items()},
        }
        self._path.write_text(json.dumps(data, indent=2))

    def _maybe_reset(self) -> None:
        today = self._today()
        if self._day != today:
            self._counts = {t: 0 for t in Tier}
            self._day = today
            self._save()

    def remaining(self, tier: Tier) -> int:
        self._maybe_reset()
        return self._limits[tier] - self._counts[tier]

    def spend(self, tier: Tier) -> bool:
        """Spend one unit. Returns True if budget was available."""
        self._maybe_reset()
        if self._counts[tier] >= self._limits[tier]:
            return False
        self._counts[tier] += 1
        self._save()
        return True
