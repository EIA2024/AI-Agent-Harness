"""Approval endpoints: list / approve / reject / edit."""

from __future__ import annotations

import uuid

import pytest

from personal_ai_os.db.models import Approval, Run, User
from personal_ai_os.db.session import session_scope
from personal_ai_os.gateway.services import ServiceContainer


class FakeApprovalEngine:
    def __init__(self):
        self.resolved: list[dict] = []

    async def resolve(self, approval_id, *, decision, approved_by, edited_arguments=None):
        self.resolved.append(
            {
                "approval_id": approval_id,
                "decision": decision,
                "approved_by": approved_by,
                "edited_arguments": edited_arguments,
            }
        )
        return {"ok": True}


async def _make_user(api_key: str) -> User:
    async with session_scope() as s:
        u = User(username=f"u{uuid.uuid4().hex[:8]}", api_key=api_key)
        s.add(u)
        await s.flush()
        await s.refresh(u)
        return u


async def _make_approval(owner_id, *, status: str = "pending", risk: int = 3) -> Approval:
    async with session_scope() as s:
        run = Run(owner_id=owner_id, status="running", input={"text": ""})
        s.add(run)
        await s.flush()
        a = Approval(
            run_id=run.id,
            session_id=None,
            owner_id=owner_id,
            action_summary="send email",
            tool_name="email.send",
            arguments_preview={"to": "x@example.com"},
            risk_level=risk,
            status=status,
        )
        s.add(a)
        await s.flush()
        await s.refresh(a)
        return a


@pytest.mark.asyncio
async def test_list_pending_and_approve(make_api, db):
    engine = FakeApprovalEngine()
    user = await _make_user("approve-key")
    approval = await _make_approval(user.id)
    headers = {"X-API-Key": "approve-key"}

    async with make_api(services=ServiceContainer(approval_engine=engine)) as ac:
        pending = await ac.get("/v1/approvals", headers=headers)
        assert pending.status_code == 200
        assert any(a["id"] == str(approval.id) for a in pending.json())

        r = await ac.post(f"/v1/approvals/{approval.id}/approve", headers=headers)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "approved"
        assert r.json()["approved_by"]

        assert len(engine.resolved) == 1
        assert engine.resolved[0]["decision"] == "approve"
        assert engine.resolved[0]["approval_id"] == approval.id

        # no longer pending
        pending2 = await ac.get("/v1/approvals", headers=headers)
        assert all(a["id"] != str(approval.id) for a in pending2.json())

        # double approve -> 409
        again = await ac.post(f"/v1/approvals/{approval.id}/approve", headers=headers)
        assert again.status_code == 409


@pytest.mark.asyncio
async def test_reject(make_api, db):
    user = await _make_user("approve-key")
    approval = await _make_approval(user.id)
    headers = {"X-API-Key": "approve-key"}

    async with make_api(services=ServiceContainer()) as ac:
        r = await ac.post(f"/v1/approvals/{approval.id}/reject", headers=headers)
        assert r.status_code == 200
        assert r.json()["status"] == "rejected"


@pytest.mark.asyncio
async def test_edit_approves_with_edited_arguments(make_api, db):
    engine = FakeApprovalEngine()
    user = await _make_user("approve-key")
    approval = await _make_approval(user.id)
    headers = {"X-API-Key": "approve-key"}

    async with make_api(services=ServiceContainer(approval_engine=engine)) as ac:
        r = await ac.post(
            f"/v1/approvals/{approval.id}/edit",
            json={"edited_arguments": {"to": "new@example.com", "subject": "edited"}},
            headers=headers,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "approved"
        assert body["arguments_preview"] == {"to": "new@example.com", "subject": "edited"}
        assert engine.resolved[0]["edited_arguments"] == {"to": "new@example.com", "subject": "edited"}


@pytest.mark.asyncio
async def test_approval_owner_isolation(make_api, db):
    user_a = await _make_user("a-key")
    user_b = await _make_user("b-key")
    approval = await _make_approval(user_a.id)

    async with make_api(services=ServiceContainer()) as ac:
        # B cannot see or act on A's approval
        pending_b = await ac.get("/v1/approvals", headers={"X-API-Key": "b-key"})
        assert all(a["id"] != str(approval.id) for a in pending_b.json())

        r = await ac.post(f"/v1/approvals/{approval.id}/approve", headers={"X-API-Key": "b-key"})
        assert r.status_code == 404

        # A can
        r2 = await ac.post(f"/v1/approvals/{approval.id}/approve", headers={"X-API-Key": "a-key"})
        assert r2.status_code == 200
