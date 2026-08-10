"""SessionRouter: session creation / reuse / archive, plus inbound helpers."""

from __future__ import annotations

import uuid

import pytest

from personal_ai_os.common.models import InboundMessage
from personal_ai_os.db.models import Project, User
from personal_ai_os.db.session import session_scope
from personal_ai_os.gateway.inbound import normalize_inbound, session_key
from personal_ai_os.gateway.router import SessionRouter


async def _make_user() -> User:
    async with session_scope() as s:
        u = User(username=f"u{uuid.uuid4().hex[:8]}", api_key=f"k{uuid.uuid4().hex[:8]}")
        s.add(u)
        await s.flush()
        await s.refresh(u)
        return u


def _msg(owner_id, conv_key, text="hello"):
    return InboundMessage(
        channel="telegram",
        channel_account_id="bot-1",
        sender_id="123",
        owner_id=owner_id,
        conversation_key=conv_key,
        text=text,
    )


@pytest.mark.asyncio
async def test_normalize_inbound(db):
    owner = uuid.uuid4()
    msg = normalize_inbound(
        channel="cli",
        sender_id="u1",
        owner_id=str(owner),
        conversation_key="conv-1",
        text="hi",
        metadata={"platform": "test"},
    )
    assert msg.channel == "cli"
    assert msg.sender_id == "u1"
    assert msg.owner_id == owner
    assert msg.conversation_key == "conv-1"
    assert msg.text == "hi"
    assert msg.metadata == {"platform": "test"}
    assert msg.attachments == []
    assert isinstance(msg.event_id, uuid.UUID)


def test_session_key_deterministic_and_distinct():
    k1 = session_key(channel="telegram", sender_id="123", conversation_key="conv-1")
    k2 = session_key(channel="telegram", sender_id="123", conversation_key="conv-1")
    k3 = session_key(channel="telegram", sender_id="123", conversation_key="conv-2")
    assert k1 == k2
    assert k1 != k3
    assert isinstance(k1, str) and len(k1) == 64


@pytest.mark.asyncio
async def test_get_or_create_creates_then_reuses(db):
    u = await _make_user()
    router = SessionRouter()

    first = await router.get_or_create(_msg(u.id, "conv-1"))
    assert first.status == "active"
    assert first.owner_id == u.id
    assert first.channel == "telegram"
    assert first.external_conversation_id == "conv-1"
    assert first.title == "hello"  # first 20 chars of the text

    second = await router.get_or_create(_msg(u.id, "conv-1"))
    assert second.id == first.id  # same conversation -> reused

    # Different conversation_key -> a different session.
    other = await router.get_or_create(_msg(u.id, "conv-2"))
    assert other.id != first.id


@pytest.mark.asyncio
async def test_sessions_are_isolated_per_owner(db):
    u1 = await _make_user()
    u2 = await _make_user()
    router = SessionRouter()

    s1 = await router.get_or_create(_msg(u1.id, "shared-conv"))
    s2 = await router.get_or_create(_msg(u2.id, "shared-conv"))
    assert s1.id != s2.id
    assert s1.owner_id == u1.id
    assert s2.owner_id == u2.id


@pytest.mark.asyncio
async def test_project_id_binds_on_creation(db):
    u = await _make_user()
    async with session_scope() as s:
        project = Project(owner_id=u.id, name="proj")
        s.add(project)
        await s.flush()
        project_id = project.id

    router = SessionRouter()
    s = await router.get_or_create(_msg(u.id, "conv-p"), project_id=project_id)
    assert s.project_id == project_id


@pytest.mark.asyncio
async def test_archive_and_get(db):
    u = await _make_user()
    router = SessionRouter()
    s = await router.get_or_create(_msg(u.id, "conv-1"))

    await router.touch(s.id)
    touched = await router.get(s.id)
    assert touched.last_active_at is not None

    await router.archive(s.id)
    archived = await router.get(s.id)
    assert archived.status == "archived"

    # An archived session is no longer returned as the active route target.
    recreated = await router.get_or_create(_msg(u.id, "conv-1"))
    assert recreated.id != s.id


@pytest.mark.asyncio
async def test_get_missing_and_touch_missing(db):
    u = await _make_user()
    router = SessionRouter()
    assert await router.get(uuid.uuid4()) is None
    await router.touch(uuid.uuid4())  # must not raise
    await router.archive(uuid.uuid4())  # must not raise
