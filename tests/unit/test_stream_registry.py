"""Live stream registry broadcast and durability contracts."""

from __future__ import annotations

import asyncio
import uuid

import pytest

from personal_ai_os.agent_runtime import event_log, streams
from personal_ai_os.db.models import Run, User
from personal_ai_os.db.session import session_scope


@pytest.mark.asyncio
async def test_two_subscribers_receive_the_same_ordered_events():
    run_id = uuid.uuid4()
    streams.register(run_id)
    first = streams.subscribe(run_id)
    second = streams.subscribe(run_id)
    assert first is not None and second is not None
    try:
        streams.push(run_id, "text.delta", {"text": "a"})
        streams.push(run_id, "run.completed", {"status": "completed"})

        first_events = [await first.get(), await first.get()]
        second_events = [await second.get(), await second.get()]
        assert first_events == second_events
        assert [event["seq"] for event in first_events] == [1, 2]
        assert [event["event"] for event in first_events] == [
            "text.delta",
            "run.completed",
        ]
    finally:
        streams.unsubscribe(run_id, first)
        streams.unsubscribe(run_id, second)
        streams.unregister(run_id)


@pytest.mark.asyncio
async def test_live_pushes_are_durable_before_unregister():
    async with session_scope() as session:
        user = User(username=f"stream-{uuid.uuid4().hex[:8]}")
        session.add(user)
        await session.flush()
        run = Run(owner_id=user.id, status="running", input={})
        session.add(run)
        await session.flush()
        run_id = run.id

    streams.register(run_id)
    try:
        streams.push(
            run_id,
            "text.delta",
            {"text": "answer", "token": "must-not-persist"},
        )
        streams.push(run_id, "run.completed", {"status": "completed"})
        await streams.flush_persistence(run_id)
    finally:
        streams.unregister(run_id)

    replay = await event_log.replay_run_events(run_id)
    assert [(item["seq"], item["event"]) for item in replay] == [
        (1, "text.delta"),
        (2, "run.completed"),
    ]
    assert replay[0]["data"]["token"] == "[REDACTED]"
    assert "must-not-persist" not in str(replay)


def test_full_subscriber_queue_keeps_gap_and_terminal_event():
    queue = asyncio.Queue(maxsize=2)
    queue.put_nowait({"event": "old-1", "seq": 1})
    queue.put_nowait({"event": "old-2", "seq": 2})

    streams._put(
        queue,
        {
            "event": "run.completed",
            "data": {},
            "seq": 3,
            "event_id": "r:3",
            "timestamp": "now",
        },
    )

    assert queue.get_nowait()["event"] == "stream.gap"
    assert queue.get_nowait()["event"] == "run.completed"
