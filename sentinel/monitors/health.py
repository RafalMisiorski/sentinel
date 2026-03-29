"""Generic HTTP monitors: health endpoint and job queue polling."""

from __future__ import annotations

import logging

import httpx

from sentinel.config import settings
from sentinel.core.event import SentinelEvent, Tier
from sentinel.monitors.base import Monitor

log = logging.getLogger(__name__)


class HTTPHealthMonitor(Monitor):
    """Polls GET /health — works with any backend returning {status, queue_size}."""

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._last_status: str = "ok"

    async def setup(self) -> None:
        self._client = httpx.AsyncClient(base_url=settings.backend_url, timeout=10.0)

    async def check(self) -> list[SentinelEvent]:
        assert self._client is not None
        events: list[SentinelEvent] = []
        try:
            resp = await self._client.get("/health")
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            events.append(
                SentinelEvent(
                    tier=Tier.INTERRUPT,
                    source="health",
                    payload={"summary": f"Backend unreachable: {exc}"},
                    decay_minutes=10.0,
                )
            )
            return events

        status = data.get("status", "unknown")
        queue_size = data.get("queue_size", 0)

        if status != "ok":
            events.append(
                SentinelEvent(
                    tier=Tier.INTERRUPT,
                    source="health",
                    payload={
                        "summary": f"Backend status: {status}",
                        "status": status,
                        "raw": data,
                    },
                    decay_minutes=10.0,
                )
            )
        elif self._last_status != "ok":
            events.append(
                SentinelEvent(
                    tier=Tier.NUDGE,
                    source="health",
                    payload={"summary": "Backend recovered — status ok"},
                )
            )

        self._last_status = status

        if queue_size >= settings.queue_size_warn:
            events.append(
                SentinelEvent(
                    tier=Tier.INFORM,
                    source="health",
                    payload={
                        "summary": f"Queue backlog: {queue_size} jobs queued",
                        "queue_size": queue_size,
                    },
                )
            )

        return events

    async def teardown(self) -> None:
        if self._client:
            await self._client.aclose()


class JobQueueMonitor(Monitor):
    """Polls /api/jobs?status=failed — detects new job failures."""

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._seen_ids: set[str] = set()
        self._first_run: bool = True

    async def setup(self) -> None:
        self._client = httpx.AsyncClient(base_url=settings.backend_url, timeout=10.0)

    async def check(self) -> list[SentinelEvent]:
        assert self._client is not None
        events: list[SentinelEvent] = []
        try:
            resp = await self._client.get("/api/jobs", params={"status": "failed", "limit": 10})
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("Jobs poll failed: %s", exc)
            return events

        jobs = data if isinstance(data, list) else data.get("jobs", [])

        for job in jobs:
            job_id = job.get("id", "")
            if job_id in self._seen_ids:
                continue
            self._seen_ids.add(job_id)

            if self._first_run:
                continue

            desc = job.get("description", job.get("type", "unknown"))
            events.append(
                SentinelEvent(
                    tier=Tier.INFORM,
                    source="jobs",
                    payload={
                        "summary": f"Job failed: {desc[:80]}",
                        "job_id": job_id,
                        "type": job.get("type", ""),
                        "description": desc,
                    },
                )
            )

        self._first_run = False
        return events

    async def teardown(self) -> None:
        if self._client:
            await self._client.aclose()
