"""Run lifecycle endpoints — inspect, cancel, resume."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from personal_ai_os.db.models import Run, RunStep, ToolCall
from personal_ai_os.db.session import session_scope

from ..deps import get_services, resolve_user
from ..schemas import RunResumeBody
from ..serializers import run_to_dict

router = APIRouter(prefix="/v1/runs", tags=["runs"])


@router.get("/{run_id}")
async def get_run(run_id: UUID, user=Depends(resolve_user)) -> dict:
    """Run status + state + steps + tool-call summary."""
    async with session_scope() as s:
        result = await s.execute(select(Run).where(Run.id == run_id, Run.owner_id == user.id))
        run = result.scalar_one_or_none()
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        steps = await s.execute(
            select(RunStep).where(RunStep.run_id == run.id).order_by(RunStep.started_at.asc())
        )
        calls = await s.execute(
            select(ToolCall).where(ToolCall.run_id == run.id).order_by(ToolCall.started_at.asc())
        )
        return run_to_dict(
            run,
            steps=list(steps.scalars().all()),
            tool_calls=list(calls.scalars().all()),
        )


@router.post("/{run_id}/cancel")
async def cancel_run(
    run_id: UUID,
    user=Depends(resolve_user),
    services=Depends(get_services),
) -> dict:
    """Cancel a run (best effort; also notifies the runner if supported)."""
    async with session_scope() as s:
        result = await s.execute(select(Run).where(Run.id == run_id, Run.owner_id == user.id))
        run = result.scalar_one_or_none()
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        run.status = "cancelled"
        run.completed_at = datetime.utcnow()
        await s.flush()
        await s.refresh(run)
        data = run_to_dict(run)

    runner = services.runner
    if runner is not None and hasattr(runner, "cancel"):
        try:
            await runner.cancel(run_id=run_id)
        except Exception:  # noqa: BLE001 - cancel is best-effort
            pass
    return data


@router.post("/{run_id}/resume")
async def resume_run(
    run_id: UUID,
    body: RunResumeBody,
    user=Depends(resolve_user),
    services=Depends(get_services),
) -> dict:
    """Resume a paused/interrupted run, resolving an approval when supplied."""
    if body.approval_id is not None and services.approval_engine is not None:
        await services.approval_engine.resolve(
            body.approval_id,
            decision=body.decision or "approve",
            approved_by=user.id,
            edited_arguments=body.edited_arguments,
        )

    async with session_scope() as s:
        result = await s.execute(select(Run).where(Run.id == run_id, Run.owner_id == user.id))
        run = result.scalar_one_or_none()
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        if run.status in ("completed", "failed", "cancelled"):
            raise HTTPException(status_code=409, detail=f"Cannot resume run in status '{run.status}'")
        run.status = "running"
        run.started_at = run.started_at or datetime.utcnow()
        await s.flush()
        await s.refresh(run)
        data = run_to_dict(run)

    runner = services.runner
    if runner is not None and hasattr(runner, "resume"):
        await runner.resume(run_id=run_id)
    return data
