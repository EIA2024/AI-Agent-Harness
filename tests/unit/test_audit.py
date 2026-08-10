"""AuditLogger tests: DB write + event bus publish."""

import uuid

from sqlalchemy import select

from personal_ai_os.common.models import DomainEvent
from personal_ai_os.db import session as db_session
from personal_ai_os.db.models import AuditEvent
from personal_ai_os.observability.audit import AUDIT_LOGGED, AuditLogger
from personal_ai_os.scheduler.event_bus import EventBus


async def test_log_writes_audit_row(seeded_db):
    owner_id, _ = seeded_db
    logger = AuditLogger()
    await logger.log(
        owner_id=owner_id,
        actor_type="user",
        actor_id=str(owner_id),
        event_type="approval.granted",
        resource_type="approval",
        resource_id="approval-1",
        details={"tool": "send_mail", "note": "ok"},
    )
    async with db_session.session_scope() as s:
        rows = (await s.execute(select(AuditEvent))).scalars().all()
        assert len(rows) == 1
        assert rows[0].event_type == "approval.granted"
        assert rows[0].actor_type == "user"
        assert rows[0].resource_id == "approval-1"
        assert rows[0].details["tool"] == "send_mail"


async def test_log_publishes_audit_event(seeded_db):
    owner_id, _ = seeded_db
    bus = EventBus()
    received = []

    async def handler(ev):
        received.append(ev)

    bus.subscribe(AUDIT_LOGGED, handler)
    logger = AuditLogger(event_bus=bus)
    await logger.log(owner_id=owner_id, actor_type="system", actor_id="runtime",
                     event_type="run.started", details={"run_id": "r1"})

    assert len(received) == 1
    assert isinstance(received[0], DomainEvent)
    assert received[0].type == AUDIT_LOGGED
    assert received[0].payload["event_type"] == "run.started"


async def test_log_sanitizes_secrets(seeded_db):
    owner_id, _ = seeded_db
    logger = AuditLogger()
    await logger.log(
        owner_id=owner_id,
        actor_type="user",
        actor_id="me",
        event_type="credential.used",
        details={"api_key": "sk-abcdefghijklmnopqrstuvwxyz123456"},
    )
    async with db_session.session_scope() as s:
        rows = (await s.execute(select(AuditEvent))).scalars().all()
        assert rows[0].details["api_key"] == "[REDACTED]"


async def test_custom_store_used_when_injected():
    class FakeStore:
        def __init__(self):
            self.rows = []

        def append(self, **kwargs):
            self.rows.append(kwargs)

    store = FakeStore()
    logger = AuditLogger(store=store)
    await logger.log(owner_id=uuid.uuid4(), actor_type="tool", actor_id="search",
                     event_type="tool.completed", details={})
    assert len(store.rows) == 1
    assert store.rows[0]["event_type"] == "tool.completed"
