"""Approval endpoints — list pending, approve / reject / edit (HITL)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select

from personal_ai_os.db.models import Approval
from personal_ai_os.db.session import session_scope
from personal_ai_os.policy_engine.approval import (
    ApprovalAuthMethodRequiredError,
    ApprovalExpiredError,
    ApprovalInvalidDecisionError,
    ApprovalNotFoundError,
    ApprovalNotPendingError,
)

from ..deps import get_services, resolve_user
from ..schemas import ApprovalEditBody
from ..serializers import approval_to_dict

router = APIRouter(prefix="/v1/approvals", tags=["approvals"])


def _normalize_decision(raw: str | None) -> str:
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
    async with session_scope() as session:
        result = await session.execute(
            select(Approval).where(
                Approval.id == approval_id, Approval.owner_id == owner_id
            )
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
    """Resolve only through ApprovalEngine; missing security wiring is 503."""
    engine = services.approval_engine
    if engine is None:
        raise HTTPException(
            status_code=503,
            detail="Approval engine unavailable; refusing to mutate approval state",
        )
    try:
        await engine.resolve(
            approval.id,
            decision=decision,
            approved_by=user.id,
            edited_arguments=edited_arguments,
        )
    except ApprovalNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ApprovalNotPendingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ApprovalExpiredError as exc:
        raise HTTPException(status_code=410, detail=str(exc)) from exc
    except ApprovalInvalidDecisionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ApprovalAuthMethodRequiredError as exc:
        # No passkey/step-up verifier is wired yet. Security requirement must
        # never be silently downgraded to ordinary API-key authentication.
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    current = await _load_owned(approval.id, user.id)
    if current is None:
        raise HTTPException(status_code=404, detail="Approval not found")
    return approval_to_dict(current)


@router.get("")
async def list_approvals(
    user=Depends(resolve_user),
    status: str = Query(default="pending"),
) -> list[dict]:
    async with session_scope() as session:
        stmt = (
            select(Approval)
            .where(Approval.owner_id == user.id)
            .order_by(Approval.created_at.desc())
        )
        if status:
            stmt = stmt.where(Approval.status == status)
        result = await session.execute(stmt)
        return [approval_to_dict(item) for item in result.scalars().all()]


@router.post("/{approval_id}/approve")
async def approve(
    approval_id: UUID,
    user=Depends(resolve_user),
    services=Depends(get_services),
) -> dict:
    approval = await _load_owned(approval_id, user.id)
    if approval is None:
        raise HTTPException(status_code=404, detail="Approval not found")
    return await _resolve(
        approval, decision="approved", user=user, services=services
    )


@router.post("/{approval_id}/reject")
async def reject(
    approval_id: UUID,
    user=Depends(resolve_user),
    services=Depends(get_services),
) -> dict:
    approval = await _load_owned(approval_id, user.id)
    if approval is None:
        raise HTTPException(status_code=404, detail="Approval not found")
    return await _resolve(
        approval, decision="rejected", user=user, services=services
    )


@router.post("/{approval_id}/edit")
async def edit(
    approval_id: UUID,
    body: ApprovalEditBody,
    user=Depends(resolve_user),
    services=Depends(get_services),
) -> dict:
    approval = await _load_owned(approval_id, user.id)
    if approval is None:
        raise HTTPException(status_code=404, detail="Approval not found")
    return await _resolve(
        approval,
        decision="approved_with_edits",
        user=user,
        services=services,
        edited_arguments=body.edited_arguments,
    )
