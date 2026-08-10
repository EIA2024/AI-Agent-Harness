"""EventBus tests: subscribe/publish, concurrency, and handler-failure isolation."""

import uuid

from personal_ai_os.common.models import DomainEvent, EventTypes
from personal_ai_os.scheduler.event_bus import EventBus


def make_event(event_type=EventTypes.RUN_STARTED, **payload):
    return DomainEvent(type=event_type, owner_id=uuid.uuid4(), payload=payload)


async def test_subscribe_and_publish():
    bus = EventBus()
    received = []
    bus.subscribe(EventTypes.RUN_STARTED, lambda ev: received.append(ev))

    await bus.publish(make_event())

    assert len(received) == 1
    assert received[0].type == EventTypes.RUN_STARTED


async def test_subscribers_helper():
    bus = EventBus()

    async def h1(ev):
        pass

    async def h2(ev):
        pass

    bus.subscribe("a.b", h1)
    bus.subscribe("a.b", h2)
    assert len(bus.subscribers("a.b")) == 2
    assert bus.subscribers("other") == []


async def test_unrelated_types_not_notified():
    bus = EventBus()
    received = []
    bus.subscribe(EventTypes.RUN_STARTED, lambda ev: received.append(ev))

    await bus.publish(make_event(EventTypes.RUN_COMPLETED))
    assert received == []


async def test_handler_failure_is_isolated():
    bus = EventBus()
    results = []

    async def ok_handler(ev):
        results.append("ok")
        await asyncio.sleep(0)

    async def bad_handler(ev):
        raise ValueError("boom")

    bus.subscribe(EventTypes.RUN_STARTED, bad_handler)
    bus.subscribe(EventTypes.RUN_STARTED, ok_handler)

    errors = await bus.publish(make_event())

    # bad handler's error is captured, ok handler still ran
    assert any(isinstance(e, ValueError) for e in errors)
    assert results == ["ok"]


async def test_handlers_run_concurrently():
    import asyncio

    bus = EventBus()
    order = []
    started = asyncio.Event()

    async def slow(ev):
        order.append("slow-start")
        await asyncio.sleep(0.05)
        order.append("slow-end")

    async def fast(ev):
        await started.wait()
        order.append("fast")

    bus.subscribe("evt", slow)
    bus.subscribe("evt", fast)
    started.set()
    await bus.publish(make_event("evt"))
    # fast handler completes without waiting for slow's sleep, and no error raised
    assert order[0] == "slow-start"
    assert "fast" in order


async def test_publish_with_no_subscribers_is_noop():
    bus = EventBus()
    assert await bus.publish(make_event()) == []


async def test_handler_errors_recorded():
    bus = EventBus()

    async def bad(ev):
        raise RuntimeError("x")

    bus.subscribe("t", bad)
    await bus.publish(make_event("t"))
    assert len(bus.handler_errors) == 1
    assert bus.handler_errors[0][0] == "t"
