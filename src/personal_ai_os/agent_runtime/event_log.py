"""Durable run event log (P1-020).

The in-process live queue is lost on restart / doesn't exist in other workers.
Key run events are appended here so the SSE endpoint can replay a run's stream
from durable storage instead of dropping it.
"""

from __future__ import annotations

from typing import Any

from personal_ai_os.db.models import EventLog
from personal_ai_os.db.session import session_scope


async def append_run_event(run_id, event_type: str, data: dict) -> None:
    """Append one durable event for a run. Fail-soft (never breaks the run)."""
    try:
        async with session_scope() as s:
            s.add(
                EventLog(
                    run_id=run_id,
                    event_type=event_type,
                    data=data,
                    seq=0,
                )
            )
    except Exception:  # noqa: BLE001 - durable log is best-effort
        pass


async def replay_run_events(run_id) -> list[dict]:
    """Return the durable events for a run in insertion order, or []."""
    from sqlalchemy import select

    try:
        async with session_scope() as s:
            rows = (
                await s.execute(
                    select(EventLog)
                    .where(EventLog.run_id == run_id)
                    .order_by(EventLog.created_at.asc(), EventLog.id.asc())
                )
            ).scalars().all()
            return [
                {"event": row.event_type, "data": row.data or {}, "seq": row.seq}
                for row in rows
            ]
    except Exception:  # noqa: BLE001
        return []


async def has_durable_events(run_id) -> bool:
    return bool(await replay_run_events(run_id))


def _persist_live(run_id: Any, event: str, data: dict) -> None:
    """Sync wrapper used by the runner's push site (best-effort)."""
    try:
        import asyncio

        asyncio.get_running_loop().create_task(append_run_event(run_id, event, data))
    except (RuntimeError, Exception):  # noqa: BLE001 - no running loop or failure
        pass
