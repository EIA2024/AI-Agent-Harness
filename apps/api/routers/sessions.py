"""Session CRUD endpoints (blueprint §41)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select

from personal_ai_os.db.models import Message, Run, Session
from personal_ai_os.db.session import session_scope

from ..deps import resolve_user
from ..schemas import SessionCreate, SessionUpdate
from ..serializers import session_to_dict

router = APIRouter(prefix="/v1/sessions", tags=["sessions"])


@router.post("", status_code=201)
async def create_session(body: SessionCreate, user=Depends(resolve_user)) -> dict:
    """Create a new session (``channel`` defaults to ``api``)."""
    async with session_scope() as s:
        sess = Session(
            owner_id=user.id,
            channel=body.channel,
            project_id=body.project_id,
            title=body.title or "New chat",
            status="active",
        )
        s.add(sess)
        await s.flush()
        await s.refresh(sess)
        return session_to_dict(sess)


@router.get("")
async def list_sessions(
    user=Depends(resolve_user),
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[dict]:
    """List the caller's sessions (optionally filtered by status).

    Enriched (T44) with ``message_count`` and ``active_run_status`` so a
    session picker needs no per-session follow-up requests.
    """
    from sqlalchemy import func

    async with session_scope() as s:
        stmt = select(Session).where(Session.owner_id == user.id).order_by(Session.last_active_at.desc())
        if status:
            stmt = stmt.where(Session.status == status)
        stmt = stmt.limit(limit).offset(offset)
        sessions = list((await s.execute(stmt)).scalars().all())

        session_ids = [x.id for x in sessions]
        # Batched message counts (one GROUP BY — no N+1).
        count_rows = {}
        if session_ids:
            counts = await s.execute(
                select(Message.session_id, func.count(Message.id))
                .where(Message.session_id.in_(session_ids))
                .group_by(Message.session_id)
            )
            count_rows = {sid: n for sid, n in counts.all()}
        # Batched active-run statuses (one IN query).
        active_ids = [x.active_run_id for x in sessions if x.active_run_id]
        run_status = {}
        if active_ids:
            runs = await s.execute(
                select(Run.id, Run.status).where(Run.id.in_(active_ids))
            )
            run_status = {rid: st for rid, st in runs.all()}
        return [
            session_to_dict(
                x,
                message_count=count_rows.get(x.id, 0),
                active_run_status=run_status.get(x.active_run_id) if x.active_run_id else None,
            )
            for x in sessions
        ]


@router.get("/{session_id}")
async def get_session(session_id: UUID, user=Depends(resolve_user)) -> dict:
    """Session detail including a message summary and ``active_run_id``."""
    async with session_scope() as s:
        result = await s.execute(
            select(Session).where(Session.id == session_id, Session.owner_id == user.id)
        )
        sess = result.scalar_one_or_none()
        if sess is None:
            raise HTTPException(status_code=404, detail="Session not found")
        msgs = await s.execute(
            select(Message).where(Message.session_id == sess.id).order_by(Message.created_at.asc())
        )
        return session_to_dict(sess, messages=list(msgs.scalars().all()))


@router.patch("/{session_id}")
async def update_session(session_id: UUID, body: SessionUpdate, user=Depends(resolve_user)) -> dict:
    """Update title / project / status of a session."""
    async with session_scope() as s:
        result = await s.execute(
            select(Session).where(Session.id == session_id, Session.owner_id == user.id)
        )
        sess = result.scalar_one_or_none()
        if sess is None:
            raise HTTPException(status_code=404, detail="Session not found")
        if body.title is not None:
            sess.title = body.title
        if body.project_id is not None:
            sess.project_id = body.project_id
        if body.status is not None:
            sess.status = body.status
        await s.flush()
        await s.refresh(sess)
        return session_to_dict(sess)


@router.delete("/{session_id}")
async def archive_session(session_id: UUID, user=Depends(resolve_user)) -> dict:
    """Archive a session (soft delete: ``status`` -> ``archived``)."""
    async with session_scope() as s:
        result = await s.execute(
            select(Session).where(Session.id == session_id, Session.owner_id == user.id)
        )
        sess = result.scalar_one_or_none()
        if sess is None:
            raise HTTPException(status_code=404, detail="Session not found")
        sess.status = "archived"
        await s.flush()
        await s.refresh(sess)
        return session_to_dict(sess)
