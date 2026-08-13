"""In-process broadcast registry for live, durable run events.

Each run has a small history buffer plus one queue per subscriber. Producers
assign the canonical run-local sequence before fan-out and persist the same
event asynchronously. This keeps multiple SSE clients independent while the
durable log remains the source for restart and cross-worker replay.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from personal_ai_os.common.utils import LogSanitizer

_QUEUE_SIZE = 10_000


@dataclass
class _RunStream:
    monitor: asyncio.Queue = field(
        default_factory=lambda: asyncio.Queue(maxsize=_QUEUE_SIZE)
    )
    subscribers: set[asyncio.Queue] = field(default_factory=set)
    history: list[dict[str, Any]] = field(default_factory=list)
    pending_persistence: set[asyncio.Task] = field(default_factory=set)
    seq: int = 0


_LIVE: dict[str, _RunStream] = {}


def register(run_id, *, initial_seq: int = 0) -> asyncio.Queue:
    """Register ``run_id`` and return its compatibility monitor queue."""
    key = str(run_id)
    if key in _LIVE:
        raise RuntimeError(f"run {key} already has a live stream")
    state = _RunStream(seq=max(0, int(initial_seq)))
    _LIVE[key] = state
    return state.monitor


def unregister(run_id) -> None:
    _LIVE.pop(str(run_id), None)


def get(run_id) -> asyncio.Queue | None:
    """Return the compatibility monitor queue, or ``None`` when not live."""
    state = _LIVE.get(str(run_id))
    return state.monitor if state is not None else None


def subscribe(run_id) -> asyncio.Queue | None:
    """Create an independent subscriber seeded with this process's history."""
    state = _LIVE.get(str(run_id))
    if state is None:
        return None
    queue: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_SIZE)
    for event in state.history:
        _put(queue, event)
    state.subscribers.add(queue)
    return queue


def unsubscribe(run_id, queue: asyncio.Queue) -> None:
    state = _LIVE.get(str(run_id))
    if state is not None:
        state.subscribers.discard(queue)


def push(run_id, event: str, data: dict) -> dict | None:
    """Sequence, fan out and durably enqueue an event for a live run."""
    key = str(run_id)
    state = _LIVE.get(key)
    if state is None:
        return None
    state.seq += 1
    safe_data = LogSanitizer.sanitize_dict(dict(data))
    item = {
        "event": event,
        "data": safe_data,
        "seq": state.seq,
        "event_id": f"{key}:{state.seq}",
        "timestamp": datetime.now(UTC).isoformat(),
    }
    state.history.append(item)
    if len(state.history) > _QUEUE_SIZE:
        del state.history[: len(state.history) - _QUEUE_SIZE]
    _put(state.monitor, item)
    for queue in tuple(state.subscribers):
        _put(queue, item)

    try:
        from personal_ai_os.agent_runtime.event_log import append_run_event

        task = asyncio.get_running_loop().create_task(
            append_run_event(run_id, event, safe_data, seq=state.seq)
        )
    except RuntimeError:
        return item
    state.pending_persistence.add(task)
    task.add_done_callback(state.pending_persistence.discard)
    return item


async def flush_persistence(run_id) -> None:
    """Wait for all durable writes scheduled for ``run_id`` so far."""
    state = _LIVE.get(str(run_id))
    if state is None:
        return
    while state.pending_persistence:
        pending = tuple(state.pending_persistence)
        await asyncio.gather(*pending, return_exceptions=True)


def live_count() -> int:
    return len(_LIVE)


def _put(queue: asyncio.Queue, item: dict) -> None:
    """Never silently lose backpressure: emit a gap and retain the new item."""
    try:
        queue.put_nowait(item)
        return
    except asyncio.QueueFull:
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            return
    if queue.maxsize < 2:
        try:
            queue.put_nowait(item)
        except asyncio.QueueFull:
            pass
        return
    while queue.qsize() > queue.maxsize - 2:
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            break
    gap = {
        "event": "stream.gap",
        "data": {
            "message": "live subscriber fell behind; reconnect from durable cursor",
            "to_seq": item.get("seq"),
        },
    }
    try:
        queue.put_nowait(gap)
        queue.put_nowait(item)
    except asyncio.QueueFull:
        pass
