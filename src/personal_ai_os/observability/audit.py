"""Audit logging for Personal AI OS (department 11 observability).

Every security-relevant action (approval granted, credential used, destructive
tool invoked, ...) is recorded through :class:`AuditLogger`. Records go to the
``audit_events`` table (via ``db.session.session_scope``) and are also
published on the event bus as ``audit.logged`` so observability can fan out.

An optional ``store`` can be injected (e.g. a separate audit sink) — when given
it is used *instead of* the default DB write. The event bus publication happens
either way.
"""

from __future__ import annotations

import uuid
from typing import Any

from personal_ai_os.common.models import DomainEvent
from personal_ai_os.common.utils import LogSanitizer
from personal_ai_os.db import session as db_session
from personal_ai_os.db.models import AuditEvent

#: Event type published by :meth:`AuditLogger.log` after a record is persisted.
AUDIT_LOGGED = "audit.logged"


class AuditLogger:
    """Writes audit records to the ``audit_events`` table and the event bus."""

    def __init__(self, *, store: Any | None = None, event_bus: Any | None = None) -> None:
        self.store = store
        self.event_bus = event_bus

    async def log(
        self,
        *,
        owner_id,
        actor_type: str,
        actor_id: str,
        event_type: str,
        resource_type: str | None = None,
        resource_id: str | None = None,
        details: dict | None = None,
    ) -> None:
        """Persist one audit record (sanitized) and publish ``audit.logged``."""
        safe_details = LogSanitizer.sanitize_dict(dict(details or {}))
        owner_uuid = self._as_uuid(owner_id)

        if self.store is not None:
            # Custom sink injected at construction time.
            result = self.store.append(
                owner_id=owner_uuid,
                actor_type=actor_type,
                actor_id=actor_id,
                event_type=event_type,
                resource_type=resource_type,
                resource_id=resource_id,
                details=safe_details,
            )
            if hasattr(result, "__await__"):
                await result
        else:
            async with db_session.session_scope() as session:
                session.add(
                    AuditEvent(
                        owner_id=owner_uuid,
                        actor_type=actor_type,
                        actor_id=actor_id,
                        event_type=event_type,
                        resource_type=resource_type,
                        resource_id=resource_id,
                        details=safe_details,
                    )
                )

        if self.event_bus is not None:
            await self.event_bus.publish(
                DomainEvent(
                    type=AUDIT_LOGGED,
                    owner_id=owner_uuid,
                    payload={
                        "actor_type": actor_type,
                        "actor_id": actor_id,
                        "event_type": event_type,
                        "resource_type": resource_type,
                        "resource_id": resource_id,
                        "details": safe_details,
                    },
                )
            )

    @staticmethod
    def _as_uuid(value) -> uuid.UUID:
        if isinstance(value, uuid.UUID):
            return value
        return uuid.UUID(str(value))
