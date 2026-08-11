"""Automation endpoints — CRUD + manual run trigger (MVP: run is a stub)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from personal_ai_os.db.models import Automation
from personal_ai_os.db.session import session_scope

from ..deps import get_services, resolve_user
from ..schemas import AutomationCreate, AutomationUpdate
from ..serializers import automation_to_dict

router = APIRouter(prefix="/v1/automations", tags=["automations"])


@router.get("")
async def list_automations(user=Depends(resolve_user)) -> list[dict]:
    async with session_scope() as s:
        result = await s.execute(
            select(Automation)
            .where(Automation.owner_id == user.id)
            .order_by(Automation.created_at.desc())
        )
        return [automation_to_dict(a) for a in result.scalars().all()]


@router.post("", status_code=201)
async def create_automation(body: AutomationCreate, user=Depends(resolve_user)) -> dict:
    async with session_scope() as s:
        a = Automation(
            owner_id=user.id,
            name=body.name,
            description=body.description,
            trigger_type=body.trigger_type,
            trigger_config=body.trigger_config,
            prompt=body.prompt,
            notify_channel=body.notify_channel,
            status="active",
        )
        s.add(a)
        await s.flush()
        await s.refresh(a)
        return automation_to_dict(a)


@router.patch("/{automation_id}")
async def update_automation(
    automation_id: UUID, body: AutomationUpdate, user=Depends(resolve_user)
) -> dict:
    async with session_scope() as s:
        result = await s.execute(
            select(Automation).where(Automation.id == automation_id, Automation.owner_id == user.id)
        )
        a = result.scalar_one_or_none()
        if a is None:
            raise HTTPException(status_code=404, detail="Automation not found")
        for field in ("name", "description", "trigger_type", "prompt", "status"):
            value = getattr(body, field)
            if value is not None:
                setattr(a, field, value)
        if body.trigger_config is not None:
            a.trigger_config = body.trigger_config
        if body.enabled is not None:
            a.enabled = body.enabled
        await s.flush()
        await s.refresh(a)
        return automation_to_dict(a)


@router.delete("/{automation_id}")
async def delete_automation(automation_id: UUID, user=Depends(resolve_user)) -> dict:
    async with session_scope() as s:
        result = await s.execute(
            select(Automation).where(Automation.id == automation_id, Automation.owner_id == user.id)
        )
        a = result.scalar_one_or_none()
        if a is None:
            raise HTTPException(status_code=404, detail="Automation not found")
        await s.delete(a)
        return {"id": str(automation_id), "deleted": True}


@router.post("/{automation_id}/run")
async def run_automation(
    automation_id: UUID,
    user=Depends(resolve_user),
    services=Depends(get_services),
) -> dict:
    """Manually trigger an automation.

    MVP: execution is stubbed — the run request is recorded on the automation
    and a 501 is returned until the scheduler department is wired up.
    """
    async with session_scope() as s:
        result = await s.execute(
            select(Automation).where(Automation.id == automation_id, Automation.owner_id == user.id)
        )
        a = result.scalar_one_or_none()
        if a is None:
            raise HTTPException(status_code=404, detail="Automation not found")
        a.last_run_at = datetime.now(UTC)
        await s.flush()

    if services.scheduler is None:
        raise HTTPException(
            status_code=501,
            detail="Scheduler is not wired up; automation execution is not implemented",
        )
    await services.scheduler.run(automation_id=automation_id, owner_id=user.id)
    return {"id": str(automation_id), "status": "triggered"}
