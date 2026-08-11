"""Run lifecycle endpoints — inspect, cancel, resume."""

from __future__ import annotations

from datetime import UTC, datetime
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
        run.completed_at = datetime.now(UTC)
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
    """Resume a paused/interrupted run.

    When ``body.approval_id`` is supplied, the run's runner resolves the
    approval (binding the decision + edited arguments) and continues the graph.
    Approval resolution lives entirely in the runner so the receipt can be
    threaded into the resume command; the API layer never calls the approval
    engine directly.

    Uses a conditional UPDATE (``WHERE status = 'waiting_approval'``) so
    concurrent resume attempts are safe — exactly one wins, the other gets 409.
    """
    runner = services.runner
    if runner is None or not hasattr(runner, "resume"):
        raise HTTPException(status_code=503, detail="Agent runner is not wired up")

    from sqlalchemy import update as sa_update

    async with session_scope() as s:
        # Conditional update: only flip to 'running' when the run is genuinely
        # paused. This is atomic — two concurrent resumes cannot both pass.
        result = await s.execute(
            sa_update(Run)
            .where(
                Run.id == run_id,
                Run.owner_id == user.id,
                Run.status == "waiting_approval",
            )
            .values(status="running", started_at=Run.started_at)
        )
        if result.rowcount == 0:
            # Either the run doesn't exist, doesn't belong to the owner,
            # or is not in a resumable state. Fetch it to give a precise error.
            check = await s.execute(
                select(Run).where(Run.id == run_id, Run.owner_id == user.id)
            )
            run = check.scalar_one_or_none()
            if run is None:
                raise HTTPException(status_code=404, detail="Run not found")
            if run.status in ("completed", "failed", "cancelled"):
                raise HTTPException(
                    status_code=409, detail=f"Cannot resume run in status '{run.status}'"
                )
            raise HTTPException(
                status_code=409, detail=f"Run is not awaiting approval (status: '{run.status}')"
            )
        # Re-fetch for the response
        run = (await s.execute(
            select(Run).where(Run.id == run_id)
        )).scalar_one()
        data = run_to_dict(run)

    decision = _normalize_approval_decision(body.decision)
    await runner.resume(
        run_id=run_id,
        approval_id=body.approval_id,
        decision=decision,
        edited_arguments=body.edited_arguments,
    )
    return data


def _normalize_approval_decision(raw: str | None) -> str | None:
    """Map loose client strings to the canonical ApprovalEngine decisions."""
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

