"""Message endpoints — send a message (starts a Run) and read history."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from personal_ai_os.db.models import Message, Session
from personal_ai_os.db.session import session_scope

from ..deps import get_services, resolve_user
from ..schemas import MessageCreate
from ..serializers import message_to_dict

router = APIRouter(prefix="/v1/sessions/{session_id}/messages", tags=["messages"])


@router.post("", status_code=201)
async def post_message(
    session_id: UUID,
    body: MessageCreate,
    user=Depends(resolve_user),
    services=Depends(get_services),
) -> dict:
    """Record a user message and start a Run via the injected runner.

    Returns ``{"run_id", "status", "message_id"}``. A missing runner (not yet
    wired to a runtime) yields a 503 so the client knows the pipeline is down.
    """
    async with session_scope() as s:
        result = await s.execute(
            select(Session).where(Session.id == session_id, Session.owner_id == user.id)
        )
        sess = result.scalar_one_or_none()
        if sess is None:
            raise HTTPException(status_code=404, detail="Session not found")
        if sess.status == "archived":
            raise HTTPException(status_code=409, detail="Session is archived")
        agent_id = sess.agent_id
        msg = Message(
            session_id=sess.id,
            owner_id=user.id,
            role="user",
            content=body.text,
            metadata_={"attachments": body.attachments or []},
        )
        s.add(msg)
        await s.flush()
        message_id = msg.id

    if services.runner is None:
        raise HTTPException(status_code=503, detail="Agent runner is not wired up")

    run_result = await services.runner.start(
        session_id=session_id,
        owner_id=user.id,
        user_input=body.text,
        agent_id=agent_id,
    )
    run_id = UUID(str(run_result["run_id"]))
    run_status = run_result.get("status", "running")

    async with session_scope() as s:
        sess = await s.get(Session, session_id)
        if sess is not None:
            sess.active_run_id = run_id
            sess.last_active_at = datetime.utcnow()

    return {
        "run_id": str(run_id),
        "status": run_status,
        "message_id": str(message_id),
    }


@router.get("")
async def list_messages(session_id: UUID, user=Depends(resolve_user)) -> list[dict]:
    """Full message history for a session (chronological)."""
    async with session_scope() as s:
        result = await s.execute(
            select(Session).where(Session.id == session_id, Session.owner_id == user.id)
        )
        if result.scalar_one_or_none() is None:
            raise HTTPException(status_code=404, detail="Session not found")
        msgs = await s.execute(
            select(Message).where(Message.session_id == session_id).order_by(Message.created_at.asc())
        )
        return [message_to_dict(m) for m in msgs.scalars().all()]
