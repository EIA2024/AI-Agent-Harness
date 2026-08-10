"""SSE streaming endpoint — replays a run's persisted steps as events.

MVP implementation (blueprint §42): the stream reads ``run_steps`` from the DB
in start order and emits ``run.started`` -> ``tool.*``/``text.delta`` ->
``run.completed``. A live runner subscription can replace this later.
"""

from __future__ import annotations

import json
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sse_starlette.sse import EventSourceResponse

from personal_ai_os.db.models import Run, RunStep
from personal_ai_os.db.session import session_scope

from ..deps import resolve_user

router = APIRouter(prefix="/v1/runs", tags=["stream"])


def _step_event(step: RunStep) -> dict | None:
    """Map a persisted RunStep to an SSE event payload."""
    data = {
        "run_id": str(step.run_id),
        "step_id": str(step.id),
        "step_type": step.step_type,
        "status": step.status,
    }
    if step.data:
        data.update({k: v for k, v in step.data.items() if k not in data})

    step_type = step.step_type
    if step_type == "tool":
        if step.status == "failed":
            event = "tool.failed"
        elif step.status in ("running", "started"):
            event = "tool.started"
        else:
            event = "tool.completed"
    elif step_type == "decide":
        event = "text.delta"
    elif step_type == "respond":
        return None  # run.completed is emitted from the run status at the end
    else:
        event = step_type
    return {"event": event, "data": json.dumps(data, ensure_ascii=False)}


@router.get("/{run_id}/stream")
async def stream_run(run_id: UUID, user=Depends(resolve_user)):
    """SSE event stream for a run (owner-scoped)."""

    async def event_generator():
        async with session_scope() as s:
            result = await s.execute(
                select(Run).where(Run.id == run_id, Run.owner_id == user.id)
            )
            run = result.scalar_one_or_none()
            if run is None:
                yield {"event": "error", "data": json.dumps({"detail": "Run not found"})}
                return
            steps_result = await s.execute(
                select(RunStep)
                .where(RunStep.run_id == run.id)
                .order_by(RunStep.started_at.asc())
            )
            steps = list(steps_result.scalars().all())
            status = run.status

        yield {
            "event": "run.started",
            "data": json.dumps({"run_id": str(run.id), "status": status}, ensure_ascii=False),
        }
        for step in steps:
            event = _step_event(step)
            if event is not None:
                yield event
        if status == "completed":
            yield {"event": "run.completed", "data": json.dumps({"run_id": str(run.id)})}
        elif status == "failed":
            yield {"event": "run.failed", "data": json.dumps({"run_id": str(run.id)})}
        elif status == "cancelled":
            yield {"event": "run.cancelled", "data": json.dumps({"run_id": str(run.id)})}

    return EventSourceResponse(event_generator())
