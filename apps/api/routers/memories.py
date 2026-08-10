"""Memory endpoints — CRUD + semantic search (blueprint §41)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select

from personal_ai_os.common.models import MemoryQuery
from personal_ai_os.db.models import MemoryRow
from personal_ai_os.db.session import session_scope

from ..deps import get_services, resolve_user
from ..schemas import MemoryCreateBody, MemorySearchBody, MemoryUpdateBody
from ..serializers import memory_to_dict

router = APIRouter(prefix="/v1/memories", tags=["memories"])


def _serialize_result(obj) -> dict:
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    if isinstance(obj, MemoryRow):
        return memory_to_dict(obj)
    return obj


@router.get("")
async def list_memories(
    user=Depends(resolve_user),
    type: str | None = Query(default=None),
    scope: str | None = Query(default=None),
    status: str | None = Query(default=None),
) -> list[dict]:
    """List memories, optionally filtered by ``type`` / ``scope`` / ``status``."""
    async with session_scope() as s:
        stmt = select(MemoryRow).where(MemoryRow.owner_id == user.id).order_by(MemoryRow.created_at.desc())
        if type:
            stmt = stmt.where(MemoryRow.type == type)
        if scope:
            stmt = stmt.where(MemoryRow.scope == scope)
        if status:
            stmt = stmt.where(MemoryRow.status == status)
        result = await s.execute(stmt)
        return [memory_to_dict(m) for m in result.scalars().all()]


@router.post("", status_code=201)
async def create_memory(body: MemoryCreateBody, user=Depends(resolve_user)) -> dict:
    """Create a memory manually."""
    async with session_scope() as s:
        m = MemoryRow(
            owner_id=user.id,
            type=body.type,
            scope=body.scope,
            content=body.content,
            summary=body.summary,
            importance=body.importance,
            confidence=body.confidence,
            source_type=body.source_type,
            sensitivity=body.sensitivity,
            status="active",
        )
        s.add(m)
        await s.flush()
        await s.refresh(m)
        return memory_to_dict(m)


@router.post("/search")
async def search_memories(
    body: MemorySearchBody,
    user=Depends(resolve_user),
    services=Depends(get_services),
) -> list[dict]:
    """Semantic search. Uses the injected ``memory_store`` when present,
    otherwise falls back to a content LIKE scan over active memories."""
    if services.memory_store is not None:
        query = MemoryQuery(owner_id=user.id, query=body.query, scope=body.scope, limit=body.limit)
        results = await services.memory_store.search(query)
        return [_serialize_result(m) for m in results]

    async with session_scope() as s:
        stmt = (
            select(MemoryRow)
            .where(MemoryRow.owner_id == user.id, MemoryRow.status == "active")
            .order_by(MemoryRow.importance.desc())
            .limit(body.limit)
        )
        if body.scope:
            stmt = stmt.where(MemoryRow.scope == body.scope)
        stmt = stmt.where(MemoryRow.content.ilike(f"%{body.query}%"))
        result = await s.execute(stmt)
        return [memory_to_dict(m) for m in result.scalars().all()]


@router.get("/{memory_id}")
async def get_memory(memory_id: UUID, user=Depends(resolve_user)) -> dict:
    async with session_scope() as s:
        result = await s.execute(
            select(MemoryRow).where(MemoryRow.id == memory_id, MemoryRow.owner_id == user.id)
        )
        m = result.scalar_one_or_none()
        if m is None:
            raise HTTPException(status_code=404, detail="Memory not found")
        return memory_to_dict(m)


@router.patch("/{memory_id}")
async def update_memory(memory_id: UUID, body: MemoryUpdateBody, user=Depends(resolve_user)) -> dict:
    async with session_scope() as s:
        result = await s.execute(
            select(MemoryRow).where(MemoryRow.id == memory_id, MemoryRow.owner_id == user.id)
        )
        m = result.scalar_one_or_none()
        if m is None:
            raise HTTPException(status_code=404, detail="Memory not found")
        if body.content is not None:
            m.content = body.content
        if body.summary is not None:
            m.summary = body.summary
        if body.importance is not None:
            m.importance = body.importance
        if body.confidence is not None:
            m.confidence = body.confidence
        if body.status is not None:
            m.status = body.status
        m.updated_at = datetime.utcnow()
        await s.flush()
        await s.refresh(m)
        return memory_to_dict(m)


@router.delete("/{memory_id}")
async def forget_memory(memory_id: UUID, user=Depends(resolve_user)) -> dict:
    """Forget a memory (soft delete: ``status`` -> ``forgotten``)."""
    async with session_scope() as s:
        result = await s.execute(
            select(MemoryRow).where(MemoryRow.id == memory_id, MemoryRow.owner_id == user.id)
        )
        m = result.scalar_one_or_none()
        if m is None:
            raise HTTPException(status_code=404, detail="Memory not found")
        m.status = "forgotten"
        m.updated_at = datetime.utcnow()
        await s.flush()
        await s.refresh(m)
        return memory_to_dict(m)
