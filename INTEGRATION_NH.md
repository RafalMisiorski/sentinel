# Sentinel ↔ Neural Holding Integration Brief

**For:** CC instance managing Neural Holding (`D:/Projects/Neural_Holding`)
**From:** Sentinel project (`~/github/sentinel`)
**Date:** 2026-03-29

---

## What is Sentinel?

Proactive push-notification engine that watches NH and delivers alerts to smart glasses (Even Realities G2) via Telegram. Repo: `~/github/sentinel`, GitHub: `RafalMisiorski/sentinel`.

Sentinel is already live and polling these NH endpoints every 30-60s:

| Sentinel Monitor | NH Endpoint | What it checks |
|---|---|---|
| `NHHealthMonitor` | `GET /health` | `status != "ok"` → INTERRUPT. `queue_size >= 10` → INFORM |
| `NHJobsMonitor` | `GET /api/jobs?status=failed&limit=10` | New job IDs since last poll → INFORM |
| `AlertsMonitor` | `GET /api/newsfeed/alerts` | `trigger_count` delta → tier mapped from `priority` field |

This works but is polling-based. Below are three integration tiers — pick what fits.

---

## Tier 1: SSE Event Stream (highest value)

**Add `GET /events/stream` to NH** — an SSE endpoint that emits lifecycle events in real-time.

Sentinel already has the consumer scaffolding for SSE (uses `httpx-sse`). The endpoint should emit:

```
event: job.failed
data: {"job_id": "job_abc123", "type": "fix", "description": "...", "error": "..."}

event: job.completed
data: {"job_id": "job_abc123", "type": "test", "output_summary": "..."}

event: health.degraded
data: {"status": "degraded", "governor": "paused", "queue_size": 15}

event: health.recovered
data: {"status": "ok", "governor": "active"}

event: alert.triggered
data: {"alert_id": "alert_cyberattacks", "name": "Cyberattacks", "priority": "critical", "match_count": 3}

event: queue.backlog
data: {"queue_size": 12, "oldest_pending_minutes": 45}
```

**Where to emit from in NH:**
- `job.failed` / `job.completed` — wherever job status transitions happen (likely in the governor or job executor)
- `health.degraded` — health check logic or governor state change
- `alert.triggered` — newsfeed alert scan handler (`POST /api/newsfeed/alerts/scan` or its background task)

**Implementation pattern** (FastAPI):
```python
import asyncio
from fastapi import Request
from sse_starlette.sse import EventSourceResponse

# Module-level event bus
_event_bus: asyncio.Queue = asyncio.Queue(maxsize=1000)

async def emit_event(event_type: str, data: dict):
    """Call this from anywhere in NH to publish an event."""
    await _event_bus.put({"event": event_type, "data": json.dumps(data)})

@app.get("/events/stream")
async def events_stream(request: Request):
    async def generator():
        while True:
            if await request.is_disconnected():
                break
            try:
                event = await asyncio.wait_for(_event_bus.get(), timeout=30)
                yield event
            except asyncio.TimeoutError:
                yield {"event": "keepalive", "data": ""}
    return EventSourceResponse(generator())
```

Dep: `pip install sse-starlette`

Once this endpoint exists, Sentinel's `NHEventsMonitor` (the SSE consumer) can be re-enabled in `sentinel/monitors/nh_events.py` — the code is already there from the initial scaffold, just needs the tier mapping updated to match the actual event names.

---

## Tier 2: Webhook Push (simpler alternative)

If SSE is too much plumbing, NH can push directly to Sentinel via a webhook.

**Add to Sentinel:** a small `POST /webhook` endpoint (not yet built — ~20 lines of FastAPI).
**Add to NH:** fire `httpx.post("http://localhost:PORT/webhook", json={...})` on key events.

Lower priority than SSE since it requires Sentinel to run a server.

---

## Tier 3: Enrich Existing Endpoints (minimal effort)

The three endpoints Sentinel already polls work. Small improvements that help:

1. **`GET /api/jobs?status=failed`** — add a `failed_at` timestamp field so Sentinel can sort by recency instead of tracking seen IDs
2. **`GET /health`** — add `uptime_seconds` and `last_error` fields for richer alerting
3. **`GET /api/newsfeed/alerts`** — add `last_triggered_at` ISO timestamp (currently only `trigger_count` is available, so Sentinel can't tell *when* the last trigger happened)

---

## Sentinel Event Model (for reference)

When NH emits events (via SSE or webhook), Sentinel maps them to:

```python
class SentinelEvent:
    tier: Tier          # INTERRUPT (1), INFORM (2), NUDGE (3)
    source: str         # "neural-holding"
    payload: dict       # must contain "summary" key for display
    timestamp: float    # unix epoch
    decay_minutes: float  # how long until event becomes stale (default 30)
```

**Tier mapping from NH priority:**
- `critical` → INTERRUPT (max 5/day)
- `high` → INFORM (max 20/day)
- `medium` / `low` → NUDGE (max 10/day)

---

## Config

Sentinel reads from `~/github/sentinel/.env`:
```
SENTINEL_NH_URL=http://localhost:8100
SENTINEL_TELEGRAM_BOT_TOKEN=<shared with NH>
SENTINEL_TELEGRAM_CHAT_ID=<shared with NH>
```

NH is confirmed running on `:8100`. Sentinel uses the same Telegram bot/chat as NH's Guardian notifier.

---

## TL;DR for NH CC

1. **Quick win:** Add `failed_at` to job objects, `last_triggered_at` to alerts — Sentinel polls these already
2. **Real integration:** Add `GET /events/stream` SSE endpoint + `emit_event()` calls at job state transitions and alert triggers
3. **Event names to emit:** `job.failed`, `job.completed`, `health.degraded`, `health.recovered`, `alert.triggered`, `queue.backlog`
