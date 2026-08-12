"""Run lifecycle endpoints — inspect, cancel, resume."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from personal_ai_os.agent_runtime.runner import SessionBusyError
from personal_ai_os.db.models import Run, RunStep, ToolCall
from personal_ai_os.db.session import session_scope
from personal_ai_os.policy_engine.approval import ApprovalError

from ..deps import get_services, resolve_user
from ..schemas import RunResumeBody
from ..serializers import run_to_dict

router = APIRouter(prefix="/v1/runs", tags=["runs"])


@router.get("")
async def list_runs(
    session_id: UUID | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
    user=Depends(resolve_user),
) -> list[dict]:
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    query = select(Run).where(Run.owner_id == user.id)
    if session_id is not None:
        query = query.where(Run.session_id == session_id)
    if status is not None:
        query = query.where(Run.status == status)
    query = query.order_by(Run.created_at.desc()).limit(limit).offset(offset)
    async with session_scope() as session:
        runs = (await session.execute(query)).scalars().all()
        return [run_to_dict(run) for run in runs]


@router.get("/{run_id}")
async def get_run(run_id: UUID, user=Depends(resolve_user)) -> dict:
    async with session_scope() as session:
        result = await session.execute(
            select(Run).where(Run.id == run_id, Run.owner_id == user.id)
        )
        run = result.scalar_one_or_none()
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        steps = await session.execute(
            select(RunStep)
            .where(RunStep.run_id == run.id)
            .order_by(RunStep.started_at.asc())
        )
        calls = await session.execute(
            select(ToolCall)
            .where(ToolCall.run_id == run.id)
            .order_by(ToolCall.started_at.asc())
        )
        return run_to_dict(
            run,
            steps=list(steps.scalars().all()),
            tool_calls=list(calls.scalars().all()),
        )


async def _ensure_owned(run_id: UUID, owner_id) -> Run:
    async with session_scope() as session:
        result = await session.execute(
            select(Run).where(Run.id == run_id, Run.owner_id == owner_id)
        )
        run = result.scalar_one_or_none()
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        return run


@router.post("/{run_id}/cancel")
async def cancel_run(
    run_id: UUID,
    user=Depends(resolve_user),
    services=Depends(get_services),
) -> dict:
    """Durably cancel through the runner; local task cancellation is secondary."""
    await _ensure_owned(run_id, user.id)
    runner = services.runner
    if runner is None or not hasattr(runner, "cancel"):
        raise HTTPException(status_code=503, detail="Agent runner is not wired up")
    try:
        await runner.cancel(run_id=run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Run not found") from exc
    return await get_run(run_id, user)


@router.post("/{run_id}/resume")
async def resume_run(
    run_id: UUID,
    body: RunResumeBody,
    user=Depends(resolve_user),
    services=Depends(get_services),
) -> dict:
    """Resume through one runner-owned CAS: approval + lease + lifecycle."""
    run = await _ensure_owned(run_id, user.id)
    if run.status != "waiting_approval":
        raise HTTPException(
            status_code=409,
            detail=f"Run is not awaiting approval (status: '{run.status}')",
        )
    runner = services.runner
    if runner is None or not hasattr(runner, "resume"):
        raise HTTPException(status_code=503, detail="Agent runner is not wired up")

    try:
        result = await runner.resume(
            run_id=run_id,
            approval_id=body.approval_id,
            decision=_normalize_approval_decision(body.decision),
            edited_arguments=body.edited_arguments,
        )
    except SessionBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ApprovalError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return result


def _normalize_approval_decision(raw: str | None) -> str | None:
    if raw is None:
        return "approved"
    canonical = {
        "approve": "approved",
        "approved": "approved",
        "approve_with_edits": "approved_with_edits",
        "approved_with_edits": "approved_with_edits",
        "reject": "rejected",
        "rejected": "rejected",
    }
    return canonical.get(raw.strip().lower(), raw)
