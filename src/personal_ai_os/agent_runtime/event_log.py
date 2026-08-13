"""Durable run event log (P1-020).

The in-process live queue is lost on restart / doesn't exist in other workers.
Key run events are appended here so the SSE endpoint can replay a run's stream
from durable storage instead of dropping it.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from personal_ai_os.common.utils import LogSanitizer
from personal_ai_os.db.models import EventLog
from personal_ai_os.db.session import session_scope

logger = logging.getLogger(__name__)


def _run_uuid(run_id) -> uuid.UUID:  # noqa: ANN001
    return run_id if isinstance(run_id, uuid.UUID) else uuid.UUID(str(run_id))


async def append_run_event(
    run_id,
    event_type: str,
    data: dict,
    *,
    seq: int | None = None,
) -> dict | None:
    """Append one durable event with a stable, run-local sequence number.

    Callers that already own the producer sequence may provide ``seq``. Other
    callers allocate the next value transactionally and retry a concurrent
    unique-key race. Persistence remains fail-soft for the agent run, but a
    failure is logged instead of being silently discarded.
    """
    run_id = _run_uuid(run_id)
    data = LogSanitizer.sanitize_dict(dict(data))
    attempts = 3 if seq is None else 1
    for attempt in range(attempts):
        try:
            async with session_scope() as s:
                event_seq = seq
                if event_seq is None:
                    event_seq = int(
                        (
                            await s.execute(
                                select(func.coalesce(func.max(EventLog.seq), 0) + 1).where(
                                    EventLog.run_id == run_id
                                )
                            )
                        ).scalar_one()
                    )
                row = EventLog(
                    run_id=run_id,
                    event_type=event_type,
                    data=data,
                    seq=event_seq,
                )
                s.add(row)
                await s.flush()
                return {
                    "event": row.event_type,
                    "data": row.data or {},
                    "seq": row.seq,
                    "event_id": f"{run_id}:{row.seq}",
                    "timestamp": row.created_at.isoformat(),
                }
        except IntegrityError:
            if attempt + 1 < attempts:
                continue
            logger.warning("event log sequence collision for run %s", run_id, exc_info=True)
        except Exception:  # noqa: BLE001 - event persistence must not fail the run
            logger.warning("failed to persist %s for run %s", event_type, run_id, exc_info=True)
            break
    return None


async def replay_run_events(run_id) -> list[dict]:
    """Return the durable events for a run in insertion order, or []."""
    run_id = _run_uuid(run_id)
    try:
        async with session_scope() as s:
            rows = (
                await s.execute(
                    select(EventLog)
                    .where(EventLog.run_id == run_id)
                    .order_by(EventLog.seq.asc())
                )
            ).scalars().all()
            return [
                {
                    "event": row.event_type,
                    "data": row.data or {},
                    "seq": row.seq,
                    "event_id": f"{row.run_id}:{row.seq}",
                    "timestamp": row.created_at.isoformat(),
                }
                for row in rows
            ]
    except Exception:  # noqa: BLE001
        logger.warning("failed to replay event log for run %s", run_id, exc_info=True)
        return []


async def has_durable_events(run_id) -> bool:
    return bool(await replay_run_events(run_id))


async def last_run_event_seq(run_id) -> int:
    """Return the latest durable sequence for a run, or zero."""
    run_id = _run_uuid(run_id)
    async with session_scope() as session:
        return int(
            (
                await session.execute(
                    select(func.coalesce(func.max(EventLog.seq), 0)).where(
                        EventLog.run_id == run_id
                    )
                )
            ).scalar_one()
        )
