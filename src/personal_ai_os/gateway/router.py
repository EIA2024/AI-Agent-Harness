"""SessionRouter — maps normalized inbound messages to persistent sessions."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from personal_ai_os.common.models import InboundMessage
from personal_ai_os.db.models import Session
from personal_ai_os.db.session import session_scope


def _coerce_uuid(value: UUID | str) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


class SessionRouter:
    """Find-or-create sessions for inbound traffic.

    The database partial unique index is authoritative for concurrent first
    contact. A losing creator catches IntegrityError and reads the winner.
    """

    def __init__(self, *, agent_id: UUID | None = None):
        self.agent_id = agent_id

    async def _find_active(self, msg: InboundMessage) -> Session | None:
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
                sess.last_active_at = datetime.now(UTC)
                return sess
            return None

    async def get_or_create(
        self,
        msg: InboundMessage,
        *,
        project_id: UUID | None = None,
    ) -> Session:
        existing = await self._find_active(msg)
        if existing is not None:
            if project_id is not None or (self.agent_id is not None and existing.agent_id is None):
                async with session_scope() as session:
                    sess = await session.get(Session, existing.id)
                    if sess is not None:
                        if project_id is not None:
                            sess.project_id = project_id
                        if self.agent_id is not None and sess.agent_id is None:
                            sess.agent_id = self.agent_id
                        sess.last_active_at = datetime.now(UTC)
                        await session.flush()
                        return sess
            return existing

        title = (msg.text or "")[:20] if msg.text else None
        try:
            async with session_scope() as session:
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
        except IntegrityError:
            # A concurrent request inserted the same active conversation. The
            # unique partial index picks the winner; return it deterministically.
            winner = await self._find_active(msg)
            if winner is not None:
                return winner
            raise

    async def get(self, session_id: UUID | str) -> Session | None:
        async with session_scope() as session:
            return await session.get(Session, _coerce_uuid(session_id))

    async def touch(self, session_id: UUID | str) -> None:
        async with session_scope() as session:
            sess = await session.get(Session, _coerce_uuid(session_id))
            if sess is not None:
                sess.last_active_at = datetime.now(UTC)

    async def archive(self, session_id: UUID | str) -> None:
        async with session_scope() as session:
            sess = await session.get(Session, _coerce_uuid(session_id))
            if sess is not None:
                sess.status = "archived"
