"""Approval endpoints — list pending, approve / reject / edit (HITL)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select

from personal_ai_os.db.models import Approval
from personal_ai_os.db.session import session_scope

from ..deps import get_services, resolve_user
from ..schemas import ApprovalEditBody
from ..serializers import approval_to_dict

router = APIRouter(prefix="/v1/approvals", tags=["approvals"])


async def _load_owned(approval_id: UUID, owner_id) -> Approval | None:
    async with session_scope() as s:
        result = await s.execute(
            select(Approval).where(Approval.id == approval_id, Approval.owner_id == owner_id)
        )
        return result.scalar_one_or_none()


async def _resolve(
    approval: Approval,
    *,
    decision: str,
    user,
    services,
    edited_arguments: dict | None = None,
) -> dict:
    async with session_scope() as s:
        current = await s.get(Approval, approval.id)
        if current is None:
            raise HTTPException(status_code=404, detail="Approval not found")
        if current.status != "pending":
            raise HTTPException(status_code=409, detail=f"Approval already '{current.status}'")
        current.status = "approved" if decision == "approve" else "rejected"
        current.approved_by = user.id
        if edited_arguments is not None:
            current.arguments_preview = edited_arguments
        await s.flush()
        await s.refresh(current)
        data = approval_to_dict(current)

    engine = services.approval_engine
    if engine is not None:
        try:
            await engine.resolve(
                approval.id,
                decision=decision,
                approved_by=user.id,
                edited_arguments=edited_arguments,
            )
        except Exception:  # noqa: BLE001 - DB state is authoritative; engine is best-effort
            pass
    return data


@router.get("")
async def list_approvals(
    user=Depends(resolve_user),
    status: str = Query(default="pending"),
) -> list[dict]:
    """List approvals, defaulting to pending ones."""
    async with session_scope() as s:
        stmt = (
            select(Approval)
            .where(Approval.owner_id == user.id)
            .order_by(Approval.created_at.desc())
        )
        if status:
            stmt = stmt.where(Approval.status == status)
        result = await s.execute(stmt)
        return [approval_to_dict(a) for a in result.scalars().all()]


@router.post("/{approval_id}/approve")
async def approve(approval_id: UUID, user=Depends(resolve_user), services=Depends(get_services)) -> dict:
    approval = await _load_owned(approval_id, user.id)
    if approval is None:
        raise HTTPException(status_code=404, detail="Approval not found")
    return await _resolve(approval, decision="approve", user=user, services=services)


@router.post("/{approval_id}/reject")
async def reject(approval_id: UUID, user=Depends(resolve_user), services=Depends(get_services)) -> dict:
    approval = await _load_owned(approval_id, user.id)
    if approval is None:
        raise HTTPException(status_code=404, detail="Approval not found")
    return await _resolve(approval, decision="reject", user=user, services=services)


@router.post("/{approval_id}/edit")
async def edit(
    approval_id: UUID,
    body: ApprovalEditBody,
    user=Depends(resolve_user),
    services=Depends(get_services),
) -> dict:
    """Approve after editing the tool arguments."""
    approval = await _load_owned(approval_id, user.id)
    if approval is None:
        raise HTTPException(status_code=404, detail="Approval not found")
    return await _resolve(
        approval,
        decision="approve",
        user=user,
        services=services,
        edited_arguments=body.edited_arguments,
    )
