"""SSE streaming endpoint — live stream during execution, DB replay after.

For a run that is still executing (streaming started via
``RunRunner.start_streaming``) the endpoint subscribes to the run's live event
queue and forwards ``thinking.delta`` / ``text.delta`` / ``tool.*`` /
``run.completed`` as they happen. Once a run has finished, the same endpoint
replays its persisted ``run_steps`` (blueprint §42).

Every event is wrapped in the versioned envelope (T41): ``schema_version``,
``event_id``, ``seq`` (monotonic per stream), ``timestamp`` and ``run_id`` are
added alongside the payload fields, so live and replay share one serializer
while staying backward-compatible with clients that read specific fields.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sse_starlette.sse import EventSourceResponse

from personal_ai_os.agent_runtime import streams
from personal_ai_os.db.models import Approval, Run, RunStep
from personal_ai_os.db.session import session_scope

from ..deps import resolve_user

router = APIRouter(prefix="/v1/runs", tags=["stream"])

_TERMINAL_EVENTS = ("run.completed", "run.failed", "run.cancelled", "approval.required")


def _envelope(event: str, data: dict, seq: int) -> dict:
    """Wrap a payload in the versioned SSE envelope (additive, compatible)."""
    wrapped: dict = {
        "schema_version": 1,
        "event_id": str(uuid.uuid4()),
        "seq": seq,
        "timestamp": datetime.now(UTC).isoformat(),
        "run_id": data.get("run_id"),
    }
    wrapped.update(data)
    return {"event": event, "data": json.dumps(wrapped, ensure_ascii=False)}


def _step_event(step: RunStep) -> tuple[str, dict] | None:
    """Map a persisted RunStep to (event, payload)."""
    data: dict = {
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
    return event, data


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
            seq = 0
            while True:
                event = await live.get()
                seq += 1
                yield _envelope(event["event"], event["data"], seq)
                if event["event"] in _TERMINAL_EVENTS:
                    break
            return

        # P1-020: if a durable event log exists for this run (written by a
        # possibly-different worker), replay it instead of the RunStep walk —
        # this survives process restarts and multi-worker deployments.
        from personal_ai_os.agent_runtime import event_log

        durable = await event_log.replay_run_events(run.id)
        if durable:
            seq = 0
            for entry in durable:
                seq += 1
                yield _envelope(entry["event"], entry["data"], seq)
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

        seq = 0
        seq += 1
        yield _envelope(
            "run.started",
            {"run_id": str(run.id), "status": status},
            seq,
        )
        # Replay the chain-of-thought (not token-streamed after the fact, but
        # surfaced so the CLI/UI shows it consistently for fast runs too).
        thinking = state.get("thinking")
        if thinking:
            seq += 1
            yield _envelope(
                "thinking.delta",
                {"text": thinking, "replay": True},
                seq,
            )
        for step in steps:
            mapped = _step_event(step)
            if mapped is None:
                continue
            seq += 1
            yield _envelope(mapped[0], mapped[1], seq)
        final = state.get("final_response")
        if final and status == "completed":
            seq += 1
            yield _envelope(
                "text.delta",
                {"text": final, "replay": True},
                seq,
            )
        seq += 1
        if status == "completed":
            yield _envelope("run.completed", {"run_id": str(run.id)}, seq)
        elif status == "failed":
            yield _envelope("run.failed", {"run_id": str(run.id)}, seq)
        elif status == "waiting_approval":
            payload: dict = {"run_id": str(run.id)}
            # T43: enrich the replay terminal with the pending approval record so
            # the CLI renders the approval card without a follow-up fetch.
            async with session_scope() as s:
                appr = (
                    await s.execute(
                        select(Approval)
                        .where(Approval.run_id == run.id, Approval.status == "pending")
                        .order_by(Approval.created_at.desc())
                    )
                ).scalars().first()
                if appr is not None:
                    payload.update(
                        {
                            "approval_id": str(appr.id),
                            "tool_name": appr.tool_name,
                            "risk_level": appr.risk_level,
                            "action_summary": appr.action_summary,
                        }
                    )
            yield _envelope("approval.required", payload, seq)
        elif status == "cancelled":
            yield _envelope("run.cancelled", {"run_id": str(run.id)}, seq)

    return EventSourceResponse(event_generator())
