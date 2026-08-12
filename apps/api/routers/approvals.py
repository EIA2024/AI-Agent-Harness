"""Approval endpoints — list pending, approve / reject / edit (HITL)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select

from personal_ai_os.db.models import Approval
from personal_ai_os.db.session import session_scope
from personal_ai_os.policy_engine.approval import ApprovalNotPendingError

from ..deps import get_services, resolve_user
from ..schemas import ApprovalEditBody
from ..serializers import approval_to_dict

router = APIRouter(prefix="/v1/approvals", tags=["approvals"])


def _normalize_decision(raw: str | None) -> str:
    """Map loose client strings to the canonical ApprovalEngine decision values."""
    if raw is None:
        return "approved"
    canonical = {
        "approve": "approved",
        "approved": "approved",
        "edit": "approved_with_edits",
        "approve_with_edits": "approved_with_edits",
        "approved_with_edits": "approved_with_edits",
        "reject": "rejected",
        "rejected": "rejected",
    }
    return canonical.get(raw.strip().lower(), raw)


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
    """Resolve an approval through the ApprovalEngine (for hash binding + receipt).

    The engine owns the resolution; the API layer normalises the client's
    decision string and delegates. The DB row is updated here directly as well
    so that tests with fake engines and production with the real engine both
    work correctly — the engine's own ``session_scope`` transaction handles its
    side (receipt + hash), and the API's ``session_scope`` handles the visible
    row state.
    """
    engine = services.approval_engine

    # Double-resolve guard FIRST: a second approve/reject on an already-resolved
    # approval is a 409. (Must be checked before the engine runs, because the
    # real engine flips the row status in its own transaction.)
    async with session_scope() as s:
        current = await s.get(Approval, approval.id)
        if current is None:
            raise HTTPException(status_code=404, detail="Approval not found")
        if current.status != "pending":
            raise HTTPException(status_code=409, detail=f"Approval already '{current.status}'")

    if engine is not None:
        try:
            await engine.resolve(
                approval.id,
                decision=decision,
                approved_by=user.id,
                edited_arguments=edited_arguments,
            )
        except ApprovalNotPendingError:
            # A concurrent request resolved it between our pending check and the
            # engine; the read-back below reflects the final state. P0-004: this
            # is the ONLY tolerated engine error — anything else (expired, DB
            # failure) must propagate and fail the request closed.
            pass

    # Read back the final state: the real engine already set it; a fake engine
    # (or engine-less test wiring) leaves it pending for us to set directly.
    async with session_scope() as s:
        current = await s.get(Approval, approval.id)
        if current is None:
            raise HTTPException(status_code=404, detail="Approval not found")
        if current.status == "pending":
            current.status = "approved" if decision in ("approved", "approved_with_edits") else "rejected"
            current.approved_by = user.id
            if edited_arguments is not None:
                current.arguments_preview = edited_arguments
            await s.flush()
            await s.refresh(current)
        return approval_to_dict(current)


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
    return await _resolve(approval, decision=_normalize_decision("approved"), user=user, services=services)


@router.post("/{approval_id}/reject")
async def reject(approval_id: UUID, user=Depends(resolve_user), services=Depends(get_services)) -> dict:
    approval = await _load_owned(approval_id, user.id)
    if approval is None:
        raise HTTPException(status_code=404, detail="Approval not found")
    return await _resolve(approval, decision=_normalize_decision("rejected"), user=user, services=services)


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
        decision=_normalize_decision("approved_with_edits"),
        user=user,
        services=services,
        edited_arguments=body.edited_arguments,
    )
