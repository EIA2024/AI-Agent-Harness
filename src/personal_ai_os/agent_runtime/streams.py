"""Live per-run event streaming registry.

The graph nodes push token/step events onto a per-run ``asyncio.Queue`` while a
run is executing; the SSE endpoint (``GET /v1/runs/{id}/stream``) subscribes to
that queue to stream events live. When a run completes the runner unregisters
the queue, and the endpoint falls back to replaying persisted steps.

Event format: ``{"event": str, "data": dict}`` — matches the SSE wire format.
"""

from __future__ import annotations

import asyncio

_LIVE: dict[str, asyncio.Queue] = {}


def register(run_id) -> asyncio.Queue:
    """Register a live queue for ``run_id`` and return it."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=10_000)
    _LIVE[str(run_id)] = queue
    return queue


def unregister(run_id) -> None:
    _LIVE.pop(str(run_id), None)


def get(run_id) -> asyncio.Queue | None:
    """Return the live queue for a run, or ``None`` when it is not streaming."""
    return _LIVE.get(str(run_id))


def push(run_id, event: str, data: dict) -> None:
    """Push an event onto a run's live queue (no-op when not streaming)."""
    queue = _LIVE.get(str(run_id))
    if queue is None:
        return
    try:
        queue.put_nowait({"event": event, "data": data})
    except asyncio.QueueFull:  # noqa: PERF203 - drop rather than block the run
        pass


def live_count() -> int:
    return len(_LIVE)
