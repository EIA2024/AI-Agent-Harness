"""Message endpoints: runner wiring, run creation, history."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from personal_ai_os.db.models import Run, User
from personal_ai_os.db.session import session_scope
from personal_ai_os.gateway.services import ServiceContainer

_TEST_KEY = "msg-test-key"
_AUTH = {"X-API-Key": _TEST_KEY}


async def _ensure_user():
    async with session_scope() as s:
        u = User(username=f"msg-{uuid.uuid4().hex[:8]}", api_key=_TEST_KEY)
        s.add(u)
        await s.flush()
        return u


async def _create_session(ac):
    """Create a session and return its id (with auth)."""
    r = await ac.post("/v1/sessions", json={}, headers=_AUTH)
    assert r.status_code == 201, r.text
    return r.json()["id"]


class FakeRunner:
    """Mirrors the real runtime: persists a Run + user Message, then returns ids."""

    def __init__(self):
        self.calls: list[dict] = []

    async def start(self, *, session_id, owner_id, user_input, agent_id=None):
        self.calls.append(
            {"session_id": session_id, "owner_id": owner_id, "user_input": user_input, "agent_id": agent_id}
        )
        run_id = uuid.uuid4()
        async with session_scope() as s:
            run = Run(
                id=run_id,
                owner_id=owner_id,
                session_id=session_id,
                status="completed",
                input={"text": user_input},
                started_at=datetime.now(UTC),
                completed_at=datetime.now(UTC),
            )
            s.add(run)
            await s.flush()  # ensure Run row is visible before referencing its FK
            from personal_ai_os.db.models import Message
            msg = Message(
                session_id=session_id,
                run_id=run_id,
                owner_id=owner_id,
                role="user",
                content=user_input,
            )
            s.add(msg)
            await s.flush()
        return {"run_id": str(run_id), "status": "completed"}


@pytest.mark.asyncio
async def test_post_message_without_runner_returns_503(make_api):
    await _ensure_user()
    async with make_api(services=ServiceContainer()) as ac:
        sid = await _create_session(ac)
        r = await ac.post(f"/v1/sessions/{sid}/messages", json={"text": "hi"}, headers=_AUTH)
        assert r.status_code == 503
        assert "runner" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_post_message_with_runner_starts_run(make_api):
    await _ensure_user()
    runner = FakeRunner()
    async with make_api(services=ServiceContainer(runner=runner)) as ac:
        sid = await _create_session(ac)

        r = await ac.post(f"/v1/sessions/{sid}/messages", json={"text": "hello there"}, headers=_AUTH)
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["run_id"]
        assert body["status"] == "completed"
        assert body["message_id"]  # runner persists the user message

        # runner was called with the right arguments
        assert len(runner.calls) == 1
        call = runner.calls[0]
        assert call["user_input"] == "hello there"
        assert str(call["session_id"]) == sid
        assert call["owner_id"]

        # session now tracks the active run
        detail = await ac.get(f"/v1/sessions/{sid}", headers=_AUTH)
        assert detail.json()["active_run_id"] == body["run_id"]

        # message history recorded (one user message from runner)
        history = await ac.get(f"/v1/sessions/{sid}/messages", headers=_AUTH)
        assert history.status_code == 200
        msgs = history.json()
        assert len(msgs) == 1
        assert msgs[0]["role"] == "user"
        assert msgs[0]["content"] == "hello there"
        assert msgs[0]["session_id"] == sid


@pytest.mark.asyncio
async def test_post_message_404_for_unknown_session(make_api):
    await _ensure_user()
    runner = FakeRunner()
    async with make_api(services=ServiceContainer(runner=runner)) as ac:
        r = await ac.post(f"/v1/sessions/{uuid.uuid4()}/messages", json={"text": "hi"}, headers=_AUTH)
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_post_message_to_archived_session_409(make_api):
    await _ensure_user()
    runner = FakeRunner()
    async with make_api(services=ServiceContainer(runner=runner)) as ac:
        sid = await _create_session(ac)
        await ac.delete(f"/v1/sessions/{sid}", headers=_AUTH)
        r = await ac.post(f"/v1/sessions/{sid}/messages", json={"text": "hi"}, headers=_AUTH)
        assert r.status_code == 409
        assert len(runner.calls) == 0


@pytest.mark.asyncio
async def test_owner_isolation_for_messages(make_api, db):
    await _ensure_user()
    async with session_scope() as s:
        other = User(username=f"u{uuid.uuid4().hex[:8]}", api_key="other-key")
        s.add(other)
        await s.flush()

    runner = FakeRunner()
    async with make_api(services=ServiceContainer(runner=runner)) as ac:
        sid = await _create_session(ac)

        # Another user cannot see or post into this session.
        r = await ac.post(
            f"/v1/sessions/{sid}/messages", json={"text": "nope"}, headers={"X-API-Key": "other-key"}
        )
        assert r.status_code == 404
        assert len(runner.calls) == 0

        history = await ac.get(f"/v1/sessions/{sid}/messages", headers={"X-API-Key": "other-key"})
        assert history.status_code == 404
