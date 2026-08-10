"""Audit event listing endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from personal_ai_os.db.models import AuditEvent
from personal_ai_os.db.session import session_scope

from ..deps import resolve_user
from ..serializers import audit_to_dict

router = APIRouter(prefix="/v1/audit", tags=["audit"])


@router.get("")
async def list_audit_events(
    user=Depends(resolve_user),
    limit: int = Query(default=50, ge=1, le=500),
) -> list[dict]:
    """Most recent audit events for the caller."""
    async with session_scope() as s:
        result = await s.execute(
            select(AuditEvent)
            .where(AuditEvent.owner_id == user.id)
            .order_by(AuditEvent.created_at.desc())
            .limit(limit)
        )
        return [audit_to_dict(e) for e in result.scalars().all()]
