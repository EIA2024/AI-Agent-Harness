"""Runs (inspect/cancel/resume), automations CRUD, and audit endpoints."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from personal_ai_os.db.models import AuditEvent, Run, User
from personal_ai_os.db.session import session_scope
from personal_ai_os.gateway.services import ServiceContainer

_AUTO_KEY = "auto-test-key"
_AUTO_AUTH = {"X-API-Key": _AUTO_KEY}


class FakeRunner:
    def __init__(self):
        self.cancelled: list = []
        self.resumed: list = []

    async def cancel(self, *, run_id):
        self.cancelled.append(run_id)
        async with session_scope() as session:
            run = await session.get(Run, run_id)
            run.status = "cancelled"

    async def resume(self, *, run_id, approval_id=None, decision=None, edited_arguments=None):
        self.resumed.append((run_id, approval_id, decision, edited_arguments))
        async with session_scope() as session:
            run = await session.get(Run, run_id)
            run.status = "running"
            await session.flush()
            await session.refresh(run)
            return {
                "id": str(run.id),
                "status": run.status,
                "input": run.input,
                "state": run.state,
            }


async def _make_user(api_key: str) -> User:
    async with session_scope() as s:
        u = User(username=f"u{uuid.uuid4().hex[:8]}", api_key=api_key)
        s.add(u)
        await s.flush()
        await s.refresh(u)
        return u


async def _ensure_auto_user():
    async with session_scope() as s:
        from sqlalchemy import select
        r = await s.execute(select(User).where(User.api_key == _AUTO_KEY))
        if r.scalar_one_or_none() is None:
            u = User(username=f"auto-{uuid.uuid4().hex[:8]}", api_key=_AUTO_KEY)
            s.add(u)
            await s.flush()


async def _make_run(owner_id, *, status: str = "running") -> Run:
    async with session_scope() as s:
        run = Run(owner_id=owner_id, status=status, input={"text": "hi"}, started_at=datetime.now(UTC))
        s.add(run)
        await s.flush()
        await s.refresh(run)
        return run


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_detail(make_api, db):
    u = await _make_user("runs-key")
    run = await _make_run(u.id, status="completed")
    headers = {"X-API-Key": "runs-key"}

    async with make_api(services=ServiceContainer()) as ac:
        r = await ac.get(f"/v1/runs/{run.id}", headers=headers)
        assert r.status_code == 200
        body = r.json()
        assert body["id"] == str(run.id)
        assert body["status"] == "completed"
        assert body["input"] == {"text": "hi"}
        assert body["steps"] == []
        assert body["tool_calls"] == []

        missing = await ac.get(f"/v1/runs/{uuid.uuid4()}", headers=headers)
        assert missing.status_code == 404


@pytest.mark.asyncio
async def test_run_detail_hides_internal_context_and_secrets(make_api, db):
    u = await _make_user("runs-private-key")
    async with session_scope() as session:
        run = Run(
            owner_id=u.id,
            status="completed",
            input={"api_key": "input-secret"},
            state={
                "final_response": "ok",
                "_cached_context": {"system_prompt": "private prompt"},
                "tool_results": [{"token": "result-secret"}],
            },
            started_at=datetime.now(UTC),
        )
        session.add(run)
        await session.flush()
        run_id = run.id

    async with make_api(services=ServiceContainer()) as ac:
        response = await ac.get(
            f"/v1/runs/{run_id}", headers={"X-API-Key": "runs-private-key"}
        )

    body = response.json()
    assert "_cached_context" not in body["state"]
    assert body["input"]["api_key"] == "[REDACTED]"
    assert body["state"]["tool_results"][0]["token"] == "[REDACTED]"
    assert "input-secret" not in response.text
    assert "result-secret" not in response.text


@pytest.mark.asyncio
async def test_run_list(make_api, db):
    """T40 — owner-scoped runs listing with filters + pagination."""
    u = await _make_user("runs-key")
    await _make_run(u.id, status="completed")
    await _make_run(u.id, status="failed")
    # another owner's run must be invisible
    other = await _make_user("other-key")
    await _make_run(other.id, status="running")
    headers = {"X-API-Key": "runs-key"}

    async with make_api(services=ServiceContainer()) as ac:
        r = await ac.get("/v1/runs", headers=headers)
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 2  # only this owner's runs
        statuses = {x["status"] for x in body}
        assert statuses == {"completed", "failed"}

        filtered = await ac.get("/v1/runs", params={"status": "failed"}, headers=headers)
        assert len(filtered.json()) == 1
        assert filtered.json()[0]["status"] == "failed"

        one = await ac.get("/v1/runs", params={"limit": 1}, headers=headers)
        assert len(one.json()) == 1


@pytest.mark.asyncio
async def test_run_cancel(make_api, db):
    u = await _make_user("runs-key")
    run = await _make_run(u.id, status="running")
    runner = FakeRunner()
    headers = {"X-API-Key": "runs-key"}

    async with make_api(services=ServiceContainer(runner=runner)) as ac:
        r = await ac.post(f"/v1/runs/{run.id}/cancel", headers=headers)
        assert r.status_code == 200
        assert r.json()["status"] == "cancelled"
        assert runner.cancelled == [run.id]


@pytest.mark.asyncio
async def test_run_resume(make_api, db):
    u = await _make_user("runs-key")
    run = await _make_run(u.id, status="waiting_approval")
    runner = FakeRunner()
    headers = {"X-API-Key": "runs-key"}

    async with make_api(services=ServiceContainer(runner=runner)) as ac:
        r = await ac.post(f"/v1/runs/{run.id}/resume", json={}, headers=headers)
        assert r.status_code == 200
        assert r.json()["status"] == "running"
        assert runner.resumed == [(run.id, None, "approved", None)]

        # completed runs cannot be resumed
        done = await _make_run(u.id, status="completed")
        blocked = await ac.post(f"/v1/runs/{done.id}/resume", json={}, headers=headers)
        assert blocked.status_code == 409


@pytest.mark.asyncio
async def test_run_owner_isolation(make_api, db):
    u_a = await _make_user("a-key")
    u_b = await _make_user("b-key")
    run = await _make_run(u_a.id)

    async with make_api(services=ServiceContainer()) as ac:
        r = await ac.get(f"/v1/runs/{run.id}", headers={"X-API-Key": "b-key"})
        assert r.status_code == 404
        r = await ac.post(f"/v1/runs/{run.id}/cancel", headers={"X-API-Key": "b-key"})
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Automations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_automation_crud(make_api):
    await _ensure_auto_user()
    async with make_api(services=ServiceContainer()) as ac:
        created = await ac.post(
            "/v1/automations",
            json={
                "name": "Morning brief",
                "trigger_type": "cron",
                "trigger_config": {"cron": "0 8 * * *"},
                "prompt": "Summarize my email",
            },
            headers=_AUTO_AUTH,
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["name"] == "Morning brief"
        assert body["status"] == "active"
        aid = body["id"]

        listing = await ac.get("/v1/automations", headers=_AUTO_AUTH)
        assert listing.status_code == 200
        assert aid in [a["id"] for a in listing.json()]

        patched = await ac.patch(f"/v1/automations/{aid}", json={"enabled": False}, headers=_AUTO_AUTH)
        assert patched.status_code == 200
        assert patched.json()["enabled"] is False

        deleted = await ac.delete(f"/v1/automations/{aid}", headers=_AUTO_AUTH)
        assert deleted.status_code == 200
        assert deleted.json()["deleted"] is True

        listing2 = await ac.get("/v1/automations", headers=_AUTO_AUTH)
        assert aid not in [a["id"] for a in listing2.json()]


@pytest.mark.asyncio
async def test_automation_run_returns_501_without_scheduler(make_api):
    await _ensure_auto_user()
    async with make_api(services=ServiceContainer()) as ac:
        aid = (
            await ac.post("/v1/automations", json={"name": "x", "prompt": "do it"}, headers=_AUTO_AUTH)
        ).json()["id"]
        r = await ac.post(f"/v1/automations/{aid}/run", headers=_AUTO_AUTH)
        assert r.status_code == 501


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_audit_list(make_api, db):
    u = await _make_user("audit-key")
    async with session_scope() as s:
        s.add(
            AuditEvent(
                owner_id=u.id,
                actor_type="user",
                actor_id=str(u.id),
                event_type="session.created",
                resource_type="session",
                details={"via": "test"},
            )
        )
        await s.flush()

    # create a second user for the cross-owner test
    u2 = await _make_user("other-key")

    async with make_api(services=ServiceContainer()) as ac:
        r = await ac.get("/v1/audit", params={"limit": 5}, headers={"X-API-Key": "audit-key"})
        assert r.status_code == 200
        events = r.json()
        assert len(events) == 1
        assert events[0]["event_type"] == "session.created"
        assert events[0]["details"] == {"via": "test"}

        # other user sees nothing
        other = await ac.get("/v1/audit", headers={"X-API-Key": "other-key"})
        assert other.json() == []


@pytest.mark.asyncio
async def test_automation_run_501_does_not_touch_last_run_at(make_api):
    """P1-034 — a 501 stub must not record a run attempt."""
    await _ensure_auto_user()
    from sqlalchemy import select

    from personal_ai_os.db.models import Automation

    async with make_api(services=ServiceContainer()) as ac:
        created = await ac.post(
            "/v1/automations",
            json={
                "name": "Unwired",
                "trigger_type": "cron",
                "trigger_config": {"cron": "0 8 * * *"},
                "prompt": "do something",
            },
            headers={"X-API-Key": _AUTO_KEY},
        )
        auto_id = created.json()["id"]
        ran = await ac.post(f"/v1/automations/{auto_id}/run", headers={"X-API-Key": _AUTO_KEY})
        assert ran.status_code == 501
        async with session_scope() as s:
            row = (await s.execute(select(Automation).where(Automation.id == uuid.UUID(auto_id)))).scalars().first()
            assert row is not None
            assert row.last_run_at is None
