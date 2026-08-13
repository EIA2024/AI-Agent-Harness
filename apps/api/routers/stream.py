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

import asyncio
import json
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sse_starlette.sse import EventSourceResponse

from personal_ai_os.agent_runtime import streams
from personal_ai_os.db.models import Approval, Run, RunStep
from personal_ai_os.db.session import session_scope

from ..deps import resolve_user

router = APIRouter(prefix="/v1/runs", tags=["stream"])

_TERMINAL_EVENTS = ("run.completed", "run.failed", "run.cancelled", "approval.required")


def _envelope(
    event: str,
    data: dict,
    seq: int,
    *,
    run_id: str | None = None,
    event_id: str | None = None,
    timestamp: str | None = None,
) -> dict:
    """Wrap a payload in the versioned SSE envelope (additive, compatible)."""
    effective_run_id = str(data.get("run_id") or run_id or "")
    stable_event_id = event_id or f"{effective_run_id}:{seq}"
    wrapped: dict = {
        "schema_version": 1,
        "event_id": stable_event_id,
        "seq": seq,
        "timestamp": timestamp or datetime.now(UTC).isoformat(),
        "run_id": effective_run_id or None,
    }
    wrapped.update(data)
    return {
        "id": stable_event_id,
        "event": event,
        "data": json.dumps(wrapped, ensure_ascii=False),
    }


def _cursor(value: str | None) -> int:
    if not value:
        return 0
    tail = value.rsplit(":", 1)[-1]
    try:
        return max(0, int(tail))
    except ValueError:
        return 0


def _durable_envelope(entry: dict, run_id: UUID) -> dict:
    return _envelope(
        entry["event"],
        entry["data"],
        int(entry["seq"]),
        run_id=str(run_id),
        event_id=entry.get("event_id"),
        timestamp=entry.get("timestamp"),
    )


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
async def stream_run(
    run_id: UUID,
    request: Request,
    user=Depends(resolve_user),
):
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

        last_seq = _cursor(request.headers.get("last-event-id"))
        from personal_ai_os.agent_runtime import event_log

        # Subscribe before replay so events produced during the DB query land
        # in this client's independent queue. The queue is seeded from local
        # history; the sequence check removes overlap with durable replay.
        live = streams.subscribe(run_id)
        if live is not None:
            try:
                durable = await event_log.replay_run_events(run.id)
                for entry in durable:
                    if int(entry["seq"]) <= last_seq:
                        continue
                    last_seq = int(entry["seq"])
                    yield _durable_envelope(entry, run_id)
                    if entry["event"] in _TERMINAL_EVENTS:
                        return
                while True:
                    entry = await live.get()
                    if entry["event"] == "stream.gap":
                        yield {
                            "event": "stream.gap",
                            "data": json.dumps(
                                {
                                    "schema_version": 1,
                                    "run_id": str(run_id),
                                    **entry["data"],
                                },
                                ensure_ascii=False,
                            ),
                        }
                        continue
                    seq = int(entry.get("seq") or 0)
                    if seq <= last_seq:
                        continue
                    last_seq = seq
                    yield _envelope(
                        entry["event"],
                        entry["data"],
                        seq,
                        run_id=str(run_id),
                        event_id=entry.get("event_id"),
                        timestamp=entry.get("timestamp"),
                    )
                    if entry["event"] in _TERMINAL_EVENTS:
                        return
            finally:
                streams.unsubscribe(run_id, live)

        # Cross-worker/restart path: durable entries keep their original IDs
        # and sequences, and Last-Event-ID resumes strictly after the cursor.
        durable = await event_log.replay_run_events(run.id)
        if durable:
            emitted_terminal = False
            for entry in durable:
                if int(entry["seq"]) <= last_seq:
                    continue
                last_seq = int(entry["seq"])
                yield _durable_envelope(entry, run_id)
                emitted_terminal = emitted_terminal or entry["event"] in _TERMINAL_EVENTS
            if emitted_terminal:
                return

        # A run may be executing in another API worker. There is no local
        # queue in that case, so tail the durable log until that worker writes a
        # terminal event instead of fabricating a short replay and closing.
        async with session_scope() as s:
            current = await s.get(Run, run.id)
            current_status = current.status if current is not None else "failed"
        while live is None and current_status == "running":
            await asyncio.sleep(0.1)
            durable = await event_log.replay_run_events(run.id)
            for entry in durable:
                if int(entry["seq"]) <= last_seq:
                    continue
                last_seq = int(entry["seq"])
                yield _durable_envelope(entry, run_id)
                if entry["event"] in _TERMINAL_EVENTS:
                    return
            async with session_scope() as s:
                current = await s.get(Run, run.id)
                current_status = current.status if current is not None else "failed"

        # Replay path: run already finished (or paused for approval).
        async with session_scope() as s:
            steps_result = await s.execute(
                select(RunStep)
                .where(RunStep.run_id == run.id)
                .order_by(RunStep.started_at.asc())
            )
            steps = list(steps_result.scalars().all())
            refreshed = await s.get(Run, run.id)
            status = refreshed.status if refreshed is not None else "failed"
            state = refreshed.state or {} if refreshed is not None else {}

        seq = last_seq + 1
        yield _envelope(
            "run.started",
            {"run_id": str(run.id), "status": status},
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
                    from personal_ai_os.common.utils import LogSanitizer

                    pending = state.get("pending_approval") or {}
                    payload.update(
                        {
                            "approval_id": str(appr.id),
                            "tool_call_id": pending.get("tool_call_id"),
                            "tool_name": appr.tool_name,
                            "risk_level": appr.risk_level,
                            "action_summary": appr.action_summary,
                            "arguments_preview": LogSanitizer.sanitize_dict(
                                appr.arguments_preview or {}
                            ),
                        }
                    )
            yield _envelope("approval.required", payload, seq)
        elif status == "cancelled":
            yield _envelope("run.cancelled", {"run_id": str(run.id)}, seq)

    return EventSourceResponse(event_generator())
