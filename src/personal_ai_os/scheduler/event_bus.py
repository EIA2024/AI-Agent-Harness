"""In-process event bus.

Department 08 (Scheduler) owns the event bus. It is a thin pub/sub over the
shared ``DomainEvent`` contract: publishers call :meth:`EventBus.publish`, and
subscribers register a per-event-type handler with :meth:`EventBus.subscribe`.

Design rules (from blueprint §8 / department 08):
- Handlers are invoked *concurrently* via ``asyncio.gather``.
- A failing handler must never break the bus or the other handlers — failures
  are captured and surfaced on the returned result only.
- The bus is intentionally in-process for now; cross-process fan-out is a later
  (scheduler dept) concern.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable

from personal_ai_os.common.models import DomainEvent

Handler = Callable[[DomainEvent], Awaitable[None]]


class EventBus:
    """Minimal async pub/sub event bus.

    Usage::

        bus = EventBus()
        bus.subscribe(EventTypes.RUN_STARTED, my_handler)

        await bus.publish(DomainEvent(type=EventTypes.RUN_STARTED, owner_id=...))
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, list[Handler]] = defaultdict(list)
        # Swallowed handler errors are recorded here so tests / observability
        # can assert on isolation behaviour.
        self._handler_errors: list[tuple[str, BaseException]] = []

    def subscribe(self, event_type: str, handler: Handler) -> None:
        """Register ``handler`` to be invoked for every event of ``event_type``."""
        if event_type not in self._subscribers or handler not in self._subscribers[event_type]:
            self._subscribers[event_type].append(handler)

    def unsubscribe(self, event_type: str, handler: Handler) -> None:
        """Remove a previously registered handler (no-op if absent)."""
        if event_type in self._subscribers and handler in self._subscribers[event_type]:
            self._subscribers[event_type].remove(handler)

    async def publish(self, event: DomainEvent) -> list[BaseException | None]:
        """Dispatch ``event`` to all handlers for its type, concurrently.

        Handlers may be async functions or plain callables. A handler raising
        an exception does not stop other handlers; the raised exception is
        collected and returned (and recorded on :attr:`_handler_errors`).
        """
        snapshot = list(self._subscribers.get(event.type, []))
        if not snapshot:
            return []

        async def _dispatch(handler: Handler) -> None:
            result = handler(event)
            if hasattr(result, "__await__"):
                await result

        results = await asyncio.gather(
            *(_dispatch(handler) for handler in snapshot),
            return_exceptions=True,
        )
        errors: list[BaseException | None] = []
        for handler, result in zip(snapshot, results, strict=False):
            if isinstance(result, BaseException):
                self._handler_errors.append((event.type, result))
                errors.append(result)
            else:
                errors.append(None)
        return errors

    def subscribers(self, event_type: str) -> list[Handler]:
        """Test / introspection helper: live copy of handlers for an event type."""
        return list(self._subscribers.get(event_type, []))

    @property
    def handler_errors(self) -> list[tuple[str, BaseException]]:
        return list(self._handler_errors)

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        types = ", ".join(f"{t}:{len(h)}" for t, h in self._subscribers.items())
        return f"<EventBus {types or 'empty'}>"
