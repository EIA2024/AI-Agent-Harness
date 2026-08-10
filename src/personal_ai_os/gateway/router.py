"""SessionRouter — maps normalized inbound messages to persistent sessions.

Routing rule (blueprint §6): the same ``channel + external_conversation_id``
for one owner resolves to the same active session; a new session is created on
first contact. Sessions are persisted via the shared async SQLAlchemy factory.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select

from personal_ai_os.common.models import InboundMessage
from personal_ai_os.db.models import Session
from personal_ai_os.db.session import session_scope


def _coerce_uuid(value: UUID | str) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


class SessionRouter:
    """Find-or-create sessions for inbound traffic."""

    def __init__(self, *, agent_id: UUID | None = None):
        self.agent_id = agent_id

    async def get_or_create(
        self,
        msg: InboundMessage,
        *,
        project_id: UUID | None = None,
    ) -> Session:
        """Return the active session for ``msg``, creating it when needed.

        1. Look up an existing *active* session by owner + channel +
           external_conversation_id.
        2. Found -> refresh ``last_active_at`` and return it.
        3. Not found -> create ``Session(owner_id, channel,
           external_conversation_id=conversation_key, title=first 20 chars)``.
        """
        async with session_scope() as session:
            result = await session.execute(
                select(Session)
                .where(
                    Session.owner_id == msg.owner_id,
                    Session.channel == msg.channel,
                    Session.external_conversation_id == msg.conversation_key,
                    Session.status == "active",
                )
                .order_by(Session.last_active_at.desc())
                .limit(1)
            )
            sess = result.scalar_one_or_none()
            if sess is not None:
                sess.last_active_at = datetime.utcnow()
                if project_id is not None:
                    sess.project_id = project_id
                if self.agent_id is not None and sess.agent_id is None:
                    sess.agent_id = self.agent_id
                await session.flush()
                return sess

            title = (msg.text or "")[:20] if msg.text else None
            sess = Session(
                owner_id=msg.owner_id,
                agent_id=self.agent_id,
                channel=msg.channel,
                external_conversation_id=msg.conversation_key,
                project_id=project_id,
                title=title,
                status="active",
            )
            session.add(sess)
            await session.flush()
            await session.refresh(sess)
            return sess

    async def get(self, session_id: UUID | str) -> Session | None:
        """Fetch a session by id (any status) or ``None`` if it does not exist."""
        async with session_scope() as session:
            return await session.get(Session, _coerce_uuid(session_id))

    async def touch(self, session_id: UUID | str) -> None:
        """Bump ``last_active_at`` (no-op when the session is missing)."""
        async with session_scope() as session:
            sess = await session.get(Session, _coerce_uuid(session_id))
            if sess is not None:
                sess.last_active_at = datetime.utcnow()

    async def archive(self, session_id: UUID | str) -> None:
        """Mark a session as archived (no-op when missing)."""
        async with session_scope() as session:
            sess = await session.get(Session, _coerce_uuid(session_id))
            if sess is not None:
                sess.status = "archived"
