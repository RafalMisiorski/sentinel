"""Pydantic settings loaded from environment variables and .env file."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings


class AttentionLimits(BaseSettings):
    """Daily push limits per tier."""

    interrupt: int = 5
    inform: int = 20
    nudge: int = 10


class Settings(BaseSettings):
    model_config = {"env_prefix": "SENTINEL_", "env_file": ".env", "env_file_encoding": "utf-8"}

    backend_url: str = "http://localhost:8100"
    backend_api_key: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    poll_interval: int = Field(default=60, description="Seconds between polls")
    health_poll_interval: int = Field(default=30, description="Seconds between health checks")
    drawdown_threshold: float = Field(default=5.0, description="Drawdown % that triggers INTERRUPT")
    queue_size_warn: int = Field(default=10, description="Queue size that triggers INFORM")
    attention_budget: AttentionLimits = AttentionLimits()
    state_file: str = "sentinel_state.json"


settings = Settings()
