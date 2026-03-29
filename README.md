# Sentinel — Proactive Agent Framework for Smart Glasses

> Sentinel decides when to interrupt you — so your AI systems don't have to wait for you to ask.

[![Tests](https://img.shields.io/badge/tests-17%2F17-brightgreen)]()
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)]()
[![License](https://img.shields.io/badge/license-MIT-green)]()

## How It Works

```
Monitor → Event → Cortical Filter → Push Router → Your Glasses
```

Sentinel watches your infrastructure — job queues, health endpoints, alert feeds — and scores every event against a daily **attention budget**. Only what passes the filter reaches your display. Everything else is queued or dropped silently.

## Why Proactive?

Existing smart glasses projects (Brilliant Halo, VisionClaw, Mentra) are **reactive** — user asks, AI answers. Sentinel flips this: **AI decides what matters**, scores it against a budget, and pushes only what passes the cortical filter. No spam, no noise, no polling your phone every 5 minutes.

## Quickstart

```bash
pip install -e ".[dev]"
```

```bash
export SENTINEL_BACKEND_URL="http://localhost:8080"
export SENTINEL_TELEGRAM_BOT_TOKEN="your-token"
export SENTINEL_TELEGRAM_CHAT_ID="your-chat-id"
```

```bash
python -m sentinel
```

Your backend needs two endpoints:

| Endpoint | Response shape |
|----------|---------------|
| `GET /health` | `{"status": "ok", "queue_size": 3}` |
| `GET /api/jobs?status=failed` | `[{"id": "...", "description": "..."}]` |

That's it. Sentinel handles scoring, budgeting, and delivery.

## Architecture

```
 Monitors                    Core                     Adapters
 ────────                    ────                     ────────
                       ┌─────────────────┐
  Backend /health   ──→│                 │──→ Telegram Bot ──→ Smart Glasses
                       │ Cortical Filter │
  Backend /api/jobs ──→│  score & budget │──→ Desktop Notification
                       │                 │
  Alert Feeds       ──→│ PUSH/QUEUE/DROP │──→ [Your Adapter]
                       └────────┬────────┘
                                │
                       sentinel_state.json
```

### Attention Budget (Daily Limits)

| Tier | Max/Day | When |
|------|---------|------|
| **INTERRUPT** | 5 | Drop everything. System down, critical breach. |
| **INFORM** | 20 | Worth knowing now. Job failed, queue backlog. |
| **NUDGE** | 10 | Batch-friendly. Minor alerts, low-priority updates. |

**Scoring formula:**

```
push_score = urgency × (1 / context_cost) × decay_rate
```

Events decay over time. Stale events drop automatically. Budget resets at midnight.

## Monitors

| Monitor | Endpoint | Checks |
|---------|----------|--------|
| **HTTPHealthMonitor** | `GET /health` | Status != ok → INTERRUPT. Queue backlog → INFORM. |
| **JobQueueMonitor** | `GET /api/jobs?status=failed` | New failures since last poll → INFORM. |
| **AlertsMonitor** | `GET /api/newsfeed/alerts` | New trigger count deltas → tier by priority. |

Writing a custom monitor:

```python
from sentinel.monitors.base import Monitor
from sentinel.core.event import SentinelEvent, Tier

class MyMonitor(Monitor):
    async def check(self) -> list[SentinelEvent]:
        # Your logic here
        return [SentinelEvent(tier=Tier.INFORM, source="my-source", payload={...})]
```

## Built For

**Even Realities G2** smart glasses via Telegram bridge — but the adapter pattern supports any display target. Swap `TelegramAdapter` for your own: Slack, webhook, MQTT, direct BLE.

## Project Structure

```
sentinel/
├── config.py                 # Pydantic settings from env / .env
├── core/
│   ├── event.py              # SentinelEvent model (tier, decay, scoring)
│   ├── cortical_filter.py    # Score → PUSH / QUEUE / DROP
│   └── attention_budget.py   # Daily limits, midnight reset, persistence
├── monitors/
│   ├── health.py             # HTTPHealthMonitor + JobQueueMonitor
│   └── algotrade.py          # AlertsMonitor (alert feed polling)
├── adapters/
│   ├── telegram.py           # Telegram Bot API push
│   └── desktop.py            # Desktop notification (dev/testing)
└── engine.py                 # asyncio main loop
```

## Reference Implementation

Sentinel was built alongside [Neural Holding](https://github.com/RafalMisiorski/Neural_Holding), a personal AI orchestration platform that serves as the reference backend. The shipped monitors are configured for its API shape (`/health`, `/api/jobs`, `/api/newsfeed/alerts`) but work with any service exposing similar endpoints.

See [`INTEGRATION_NH.md`](INTEGRATION_NH.md) for the detailed integration guide including SSE streaming and webhook options.

## License

MIT
