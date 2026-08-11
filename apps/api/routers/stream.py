"""SSE streaming endpoint — live stream during execution, DB replay after.

For a run that is still executing (streaming started via
``RunRunner.start_streaming``) the endpoint subscribes to the run's live event
queue and forwards ``thinking.delta`` / ``text.delta`` / ``tool.*`` /
``run.completed`` as they happen. Once a run has finished, the same endpoint
replays its persisted ``run_steps`` (blueprint §42).
"""

from __future__ import annotations

import json
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sse_starlette.sse import EventSourceResponse

from personal_ai_os.agent_runtime import streams
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

        # Live path: the run is currently executing and has a live queue.
        live = streams.get(run_id)
        if live is not None:
            while True:
                event = await live.get()
                yield {"event": event["event"], "data": json.dumps(event["data"], ensure_ascii=False)}
                if event["event"] in ("run.completed", "run.failed", "run.cancelled", "approval.required"):
                    break
            return

        # Replay path: run already finished (or paused for approval).
        async with session_scope() as s:
            steps_result = await s.execute(
                select(RunStep)
                .where(RunStep.run_id == run.id)
                .order_by(RunStep.started_at.asc())
            )
            steps = list(steps_result.scalars().all())
            status = run.status
            state = run.state or {}

        yield {
            "event": "run.started",
            "data": json.dumps({"run_id": str(run.id), "status": status}, ensure_ascii=False),
        }
        # Replay the chain-of-thought (not token-streamed after the fact, but
        # surfaced so the CLI/UI shows it consistently for fast runs too).
        thinking = state.get("thinking")
        if thinking:
            yield {
                "event": "thinking.delta",
                "data": json.dumps({"text": thinking, "replay": True}, ensure_ascii=False),
            }
        for step in steps:
            event = _step_event(step)
            if event is not None:
                yield event
        final = state.get("final_response")
        if final and status == "completed":
            yield {
                "event": "text.delta",
                "data": json.dumps({"text": final, "replay": True}, ensure_ascii=False),
            }
        if status == "completed":
            yield {"event": "run.completed", "data": json.dumps({"run_id": str(run.id)})}
        elif status == "failed":
            yield {"event": "run.failed", "data": json.dumps({"run_id": str(run.id)})}
        elif status == "waiting_approval":
            yield {"event": "approval.required", "data": json.dumps({"run_id": str(run.id)})}
        elif status == "cancelled":
            yield {"event": "run.cancelled", "data": json.dumps({"run_id": str(run.id)})}

    return EventSourceResponse(event_generator())
