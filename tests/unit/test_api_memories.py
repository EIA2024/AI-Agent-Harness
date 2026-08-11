"""Memory endpoints: CRUD + search (injected store and DB fallback)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from personal_ai_os.common.models import Memory
from personal_ai_os.db.models import User
from personal_ai_os.db.session import session_scope
from personal_ai_os.gateway.services import ServiceContainer


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
    async with make_api(services=ServiceContainer()) as ac:
        created = await ac.post(
            "/v1/memories",
            json={"type": "fact", "scope": "global", "content": "My name is Alice"},
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["status"] == "active"
        assert body["content"] == "My name is Alice"
        mid = body["id"]

        listing = await ac.get("/v1/memories")
        assert listing.status_code == 200
        assert mid in [m["id"] for m in listing.json()]

        filtered = await ac.get("/v1/memories", params={"type": "fact"})
        assert len(filtered.json()) == 1
        filtered_none = await ac.get("/v1/memories", params={"type": "other"})
        assert filtered_none.json() == []

        one = await ac.get(f"/v1/memories/{mid}")
        assert one.status_code == 200
        assert one.json()["content"] == "My name is Alice"

        patched = await ac.patch(f"/v1/memories/{mid}", json={"content": "My name is Alice B"})
        assert patched.status_code == 200
        assert patched.json()["content"] == "My name is Alice B"

        forgotten = await ac.delete(f"/v1/memories/{mid}")
        assert forgotten.status_code == 200
        assert forgotten.json()["status"] == "forgotten"

        missing = await ac.get(f"/v1/memories/{uuid.uuid4()}")
        assert missing.status_code == 404


@pytest.mark.asyncio
async def test_memory_search_with_injected_store(make_api):
    store = FakeMemoryStore()
    async with make_api(services=ServiceContainer(memory_store=store)) as ac:
        r = await ac.post("/v1/memories/search", json={"query": "python", "limit": 5})
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 1
        assert data[0]["content"] == "result for python"
        assert store.searches[0].query == "python"
        assert store.searches[0].limit == 5


@pytest.mark.asyncio
async def test_memory_search_db_fallback(make_api):
    async with make_api(services=ServiceContainer()) as ac:
        await ac.post("/v1/memories", json={"type": "fact", "scope": "global", "content": "I like hiking"})
        await ac.post("/v1/memories", json={"type": "fact", "scope": "global", "content": "I like coding"})

        r = await ac.post("/v1/memories/search", json={"query": "hiking"})
        assert r.status_code == 200
        contents = [m["content"] for m in r.json()]
        assert "I like hiking" in contents
        assert "I like coding" not in contents


@pytest.mark.asyncio
async def test_memory_owner_isolation(make_api, db):
    async with session_scope() as s:
        other = User(username=f"u{uuid.uuid4().hex[:8]}", api_key="other-key")
        s.add(other)
        await s.flush()

    async with make_api(services=ServiceContainer()) as ac:
        created = await ac.post("/v1/memories", json={"type": "fact", "content": "secret"})
        mid = created.json()["id"]

        listing = await ac.get("/v1/memories", headers={"X-API-Key": "other-key"})
        assert listing.status_code == 200
        assert listing.json() == []

        fetch = await ac.get(f"/v1/memories/{mid}", headers={"X-API-Key": "other-key"})
        assert fetch.status_code == 404
