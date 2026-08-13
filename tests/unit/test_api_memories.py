"""Memory endpoints: CRUD + search (injected store and DB fallback)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select as _select

from personal_ai_os.common.models import Memory
from personal_ai_os.db.models import User
from personal_ai_os.db.session import session_scope
from personal_ai_os.gateway.services import ServiceContainer

_MEM_KEY = "mem-test-key"
_AUTH = {"X-API-Key": _MEM_KEY}


async def _ensure_user():
    async with session_scope() as s:
        r = await s.execute(_select(User).where(User.api_key == _MEM_KEY))
        if r.scalar_one_or_none() is None:
            u = User(username=f"mem-{uuid.uuid4().hex[:8]}", api_key=_MEM_KEY)
            s.add(u)
            await s.flush()


class FakeMemoryStore:
    def __init__(self):
        self.searches: list = []

    async def search(self, query):
        self.searches.append(query)
        return [
            Memory(
                id=uuid.uuid4(),
                owner_id=query.owner_id,
                type="fact",
                scope="global",
                content=f"result for {query.query}",
                summary=None,
                importance=0.9,
                confidence=0.8,
                source_type="manual",
                source_id=None,
                sensitivity="personal",
                status="active",
                created_at=datetime.now(UTC),
            )
        ]


@pytest.mark.asyncio
async def test_memory_crud(make_api):
    await _ensure_user()
    async with make_api(services=ServiceContainer()) as ac:
        created = await ac.post(
            "/v1/memories",
            json={"type": "fact", "scope": "global", "content": "My name is Alice"},
            headers=_AUTH,
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["status"] == "active"
        assert body["content"] == "My name is Alice"
        mid = body["id"]

        listing = await ac.get("/v1/memories", headers=_AUTH)
        assert listing.status_code == 200
        assert mid in [m["id"] for m in listing.json()]

        filtered = await ac.get("/v1/memories", params={"type": "fact"}, headers=_AUTH)
        assert len(filtered.json()) == 1
        filtered_none = await ac.get("/v1/memories", params={"type": "other"}, headers=_AUTH)
        assert filtered_none.json() == []

        one = await ac.get(f"/v1/memories/{mid}", headers=_AUTH)
        assert one.status_code == 200
        assert one.json()["content"] == "My name is Alice"

        patched = await ac.patch(f"/v1/memories/{mid}", json={"content": "My name is Alice B"}, headers=_AUTH)
        assert patched.status_code == 200
        assert patched.json()["content"] == "My name is Alice B"

        forgotten = await ac.delete(f"/v1/memories/{mid}", headers=_AUTH)
        assert forgotten.status_code == 200
        assert forgotten.json()["status"] == "forgotten"

        missing = await ac.get(f"/v1/memories/{uuid.uuid4()}", headers=_AUTH)
        assert missing.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_scores",
    [
        {"importance": -0.01},
        {"importance": 1.01},
        {"confidence": -0.01},
        {"confidence": 1.01},
    ],
)
async def test_create_memory_rejects_out_of_range_scores(make_api, invalid_scores):
    await _ensure_user()
    async with make_api(services=ServiceContainer()) as ac:
        response = await ac.post(
            "/v1/memories",
            json={"type": "fact", "content": "invalid scores"} | invalid_scores,
            headers=_AUTH,
        )

        assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_update",
    [
        {"importance": -0.01},
        {"importance": 1.01},
        {"confidence": -0.01},
        {"confidence": 1.01},
        {"status": "nonsense"},
    ],
)
async def test_update_memory_rejects_invalid_values(make_api, invalid_update):
    await _ensure_user()
    async with make_api(services=ServiceContainer()) as ac:
        created = await ac.post(
            "/v1/memories",
            json={"type": "fact", "content": "valid memory"},
            headers=_AUTH,
        )
        mid = created.json()["id"]

        response = await ac.patch(
            f"/v1/memories/{mid}", json=invalid_update, headers=_AUTH
        )

        assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["active", "draft", "superseded", "forgotten"])
async def test_memory_accepts_score_boundaries_and_public_statuses(make_api, status):
    await _ensure_user()
    async with make_api(services=ServiceContainer()) as ac:
        created = await ac.post(
            "/v1/memories",
            json={
                "type": "fact",
                "content": "boundary scores",
                "importance": 0,
                "confidence": 1,
            },
            headers=_AUTH,
        )
        assert created.status_code == 201
        assert created.json()["importance"] == 0
        assert created.json()["confidence"] == 1

        response = await ac.patch(
            f"/v1/memories/{created.json()['id']}",
            json={"importance": 1, "confidence": 0, "status": status},
            headers=_AUTH,
        )

        assert response.status_code == 200
        assert response.json()["importance"] == 1
        assert response.json()["confidence"] == 0
        assert response.json()["status"] == status


@pytest.mark.asyncio
async def test_memory_search_with_injected_store(make_api):
    await _ensure_user()
    store = FakeMemoryStore()
    async with make_api(services=ServiceContainer(memory_store=store)) as ac:
        r = await ac.post("/v1/memories/search", json={"query": "python", "limit": 5}, headers=_AUTH)
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 1
        assert data[0]["content"] == "result for python"
        assert store.searches[0].query == "python"
        assert store.searches[0].limit == 5


@pytest.mark.asyncio
async def test_memory_search_db_fallback(make_api):
    await _ensure_user()
    async with make_api(services=ServiceContainer()) as ac:
        await ac.post("/v1/memories", json={"type": "fact", "scope": "global", "content": "I like hiking"}, headers=_AUTH)
        await ac.post("/v1/memories", json={"type": "fact", "scope": "global", "content": "I like coding"}, headers=_AUTH)

        r = await ac.post("/v1/memories/search", json={"query": "hiking"}, headers=_AUTH)
        assert r.status_code == 200
        contents = [m["content"] for m in r.json()]
        assert "I like hiking" in contents
        assert "I like coding" not in contents


@pytest.mark.asyncio
async def test_memory_owner_isolation(make_api, db):
    await _ensure_user()
    async with session_scope() as s:
        other = User(username=f"u{uuid.uuid4().hex[:8]}", api_key="other-key")
        s.add(other)
        await s.flush()

    async with make_api(services=ServiceContainer()) as ac:
        created = await ac.post("/v1/memories", json={"type": "fact", "content": "secret"}, headers=_AUTH)
        mid = created.json()["id"]

        listing = await ac.get("/v1/memories", headers={"X-API-Key": "other-key"})
        assert listing.status_code == 200
        assert listing.json() == []

        fetch = await ac.get(f"/v1/memories/{mid}", headers={"X-API-Key": "other-key"})
        assert fetch.status_code == 404
