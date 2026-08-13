"""SQL persistence + hybrid retrieval for long-term memory (dept 05).

Implements the ``MemoryStore`` protocol (``common/protocols.py``) on top of
``MemoryRow`` / ``MemoryLink`` / ``AuditEvent`` via ``db.session.session_scope``.

* Every memory carries provenance (``source_type`` / ``source_id`` /
  ``source_event_id``) — required by blueprint constraint #5.
* Retrieval is hybrid: DB filter + keyword + semantic + importance + recency.
* ``forget`` is a soft delete that also clears links and writes an audit event.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, or_, select

from ..common.models import (
    DomainEvent,
    EventTypes,
    Memory,
    MemoryCreate,
    MemoryQuery,
)
from ..db.models import AuditEvent, MemoryLink, MemoryRow
from ..db.session import session_scope
from ..model_gateway.embeddings import DeterministicEmbedding
from .retrieval import hybrid_rank


class SQLMemoryStore:
    """MemoryStore backed by SQLAlchemy (SQLite in tests, Postgres in prod)."""

    def __init__(self, *, embedding_provider=None, event_bus=None):
        self.embeddings = embedding_provider or DeterministicEmbedding()
        #: optional event publisher (dept 08); used to announce memory.deleted
        self.event_bus = event_bus

    # -- conversion --------------------------------------------------------

    @staticmethod
    def to_memory(row: MemoryRow, score: float | None = None) -> Memory:
        return Memory(
            id=row.id,
            owner_id=row.owner_id,
            type=row.type,
            scope=row.scope,
            content=row.content,
            summary=row.summary,
            importance=row.importance if row.importance is not None else 0.5,
            confidence=row.confidence if row.confidence is not None else 0.5,
            source_type=row.source_type,
            source_id=row.source_id,
            sensitivity=row.sensitivity,
            status=row.status,
            created_at=row.created_at,
            score=score,
        )

    # -- retrieval ---------------------------------------------------------

    async def search(self, query: MemoryQuery) -> list[Memory]:
        limit = max(1, min(query.limit or 10, 100))
        async with session_scope() as session:
            stmt = select(MemoryRow).where(
                MemoryRow.owner_id == query.owner_id,
                MemoryRow.status == "active",
            )
            if query.scope:
                stmt = stmt.where(MemoryRow.scope == query.scope)
            if query.types:
                stmt = stmt.where(MemoryRow.type.in_(query.types))
            if query.min_confidence:
                stmt = stmt.where(MemoryRow.confidence >= query.min_confidence)
            # Pre-filter with a candidate pool larger than the final limit so
            # hybrid_rank has enough material to score. This prevents loading
            # an unbounded row set for owners with many memories.
            stmt = stmt.order_by(MemoryRow.created_at.desc()).limit(max(limit * 5, 100))
            rows = (await session.execute(stmt)).scalars().all()

        candidates = [self.to_memory(row) for row in rows]
        if not candidates or not (query.query or "").strip():
            ranked = [{"memory": m, "score": 0.0} for m in candidates]
        else:
            ranked = await hybrid_rank(candidates, query.query, self.embeddings, top_k=limit)
        result = []
        for item in ranked:
            memory = item["memory"]
            memory.score = item["score"]
            result.append(memory)
        return result[:limit]

    # -- write -------------------------------------------------------------

    async def write(self, memory: MemoryCreate) -> Memory:
        if not memory.source_type:
            raise ValueError(
                "Memory provenance is required: source_type must be set "
                "(blueprint constraint #5)."
            )
        # Dedup: identical (owner, type, content) active memory -> return existing.
        async with session_scope() as session:
            existing = (
                await session.execute(
                    select(MemoryRow).where(
                        MemoryRow.owner_id == memory.owner_id,
                        MemoryRow.type == memory.type,
                        MemoryRow.content == memory.content,
                        MemoryRow.status == "active",
                    )
                )
            ).scalars().first()
            if existing is not None:
                return self.to_memory(existing)

            row = MemoryRow(
                owner_id=memory.owner_id,
                agent_id=memory.agent_id,
                type=memory.type,
                scope=memory.scope,
                scope_id=memory.scope_id,
                content=memory.content,
                summary=memory.summary,
                importance=memory.importance,
                confidence=memory.confidence,
                source_type=memory.source_type,
                source_id=memory.source_id,
                source_event_id=memory.source_event_id,
                sensitivity=memory.sensitivity,
                status="active",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            session.add(row)
            await session.flush()
            return self.to_memory(row)

    # -- read --------------------------------------------------------------

    async def get(self, memory_id: UUID, *, owner_id: UUID) -> Memory | None:
        async with session_scope() as session:
            row = (
                await session.execute(
                    select(MemoryRow).where(
                        MemoryRow.id == memory_id, MemoryRow.owner_id == owner_id
                    )
                )
            ).scalar_one_or_none()
            if row is None or row.status == "deleted":
                return None
            return self.to_memory(row)

    async def update(
        self,
        memory_id: UUID,
        *,
        owner_id: UUID,
        content: str | None = None,
        summary: str | None = None,
        importance: float | None = None,
        confidence: float | None = None,
        sensitivity: str | None = None,
    ) -> Memory | None:
        async with session_scope() as session:
            row = (
                await session.execute(
                    select(MemoryRow).where(
                        MemoryRow.id == memory_id, MemoryRow.owner_id == owner_id
                    )
                )
            ).scalar_one_or_none()
            if row is None or row.status == "deleted":
                return None
            if content is not None:
                row.content = content
            if summary is not None:
                row.summary = summary
            if importance is not None:
                row.importance = importance
            if confidence is not None:
                row.confidence = confidence
            if sensitivity is not None:
                row.sensitivity = sensitivity
            row.updated_at = datetime.now(UTC)
            await session.flush()
            return self.to_memory(row)

    # -- forget ------------------------------------------------------------

    async def forget(self, memory_id: UUID, *, owner_id: UUID) -> None:
        """Soft-delete a memory: status -> 'deleted', drop links, write audit."""
        async with session_scope() as session:
            row = (
                await session.execute(
                    select(MemoryRow).where(
                        MemoryRow.id == memory_id, MemoryRow.owner_id == owner_id
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                return
            row.status = "deleted"
            row.updated_at = datetime.now(UTC)

            await session.execute(
                delete(MemoryLink).where(
                    or_(MemoryLink.source_id == memory_id, MemoryLink.target_id == memory_id)
                )
            )

            session.add(
                AuditEvent(
                    owner_id=row.owner_id,
                    actor_type="system",
                    actor_id=None,
                    event_type=EventTypes.MEMORY_DELETED,
                    resource_type="memory",
                    resource_id=str(memory_id),
                    details={"memory_id": str(memory_id), "scope": row.scope},
                )
            )
            await session.flush()

        if self.event_bus is not None:
            await self.event_bus.publish(
                DomainEvent(
                    type=EventTypes.MEMORY_DELETED,
                    owner_id=row.owner_id,
                    payload={"memory_id": str(memory_id)},
                )
            )

    # -- listing -----------------------------------------------------------

    async def list(
        self,
        *,
        owner_id: UUID,
        scope: str | None = None,
        types: list[str] | None = None,
        limit: int = 50,
        status: str = "active",
    ) -> list[Memory]:
        async with session_scope() as session:
            stmt = select(MemoryRow).where(
                MemoryRow.owner_id == owner_id,
                MemoryRow.status == status,
            )
            if scope:
                stmt = stmt.where(MemoryRow.scope == scope)
            if types:
                stmt = stmt.where(MemoryRow.type.in_(types))
            stmt = stmt.order_by(MemoryRow.created_at.desc()).limit(limit)
            rows = (await session.execute(stmt)).scalars().all()
            return [self.to_memory(row) for row in rows]
