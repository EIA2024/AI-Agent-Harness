"""Session endpoints + API-key authentication."""

from __future__ import annotations

import uuid

import pytest

from personal_ai_os.db.models import User
from personal_ai_os.db.session import session_scope
from personal_ai_os.gateway.services import ServiceContainer


async def _make_user(api_key: str) -> User:
    async with session_scope() as s:
        u = User(username=f"u{uuid.uuid4().hex[:8]}", api_key=api_key)
        s.add(u)
        await s.flush()
        await s.refresh(u)
        return u


@pytest.mark.asyncio
async def test_create_and_list_and_get_session(make_api):
    async with make_api(services=ServiceContainer()) as ac:
        r = await ac.post("/v1/sessions", json={"channel": "api", "title": "My chat"})
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["status"] == "active"
        assert body["channel"] == "api"
        assert body["title"] == "My chat"
        assert body["owner_id"]  # dev owner
        assert body["active_run_id"] is None
        sid = body["id"]

        listing = await ac.get("/v1/sessions")
        assert listing.status_code == 200
        ids = [s["id"] for s in listing.json()]
        assert sid in ids

        detail = await ac.get(f"/v1/sessions/{sid}")
        assert detail.status_code == 200
        detail_body = detail.json()
        assert detail_body["id"] == sid
        assert detail_body["messages"] == []

        # 404 for a random id
        missing = await ac.get(f"/v1/sessions/{uuid.uuid4()}")
        assert missing.status_code == 404


@pytest.mark.asyncio
async def test_patch_and_delete_archive(make_api):
    async with make_api(services=ServiceContainer()) as ac:
        sid = (await ac.post("/v1/sessions", json={})).json()["id"]

        patched = await ac.patch(f"/v1/sessions/{sid}", json={"title": "renamed"})
        assert patched.status_code == 200
        assert patched.json()["title"] == "renamed"

        deleted = await ac.delete(f"/v1/sessions/{sid}")
        assert deleted.status_code == 200
        assert deleted.json()["status"] == "archived"

        # archived no longer shows in an active list
        active = await ac.get("/v1/sessions", params={"status": "active"})
        assert sid not in [s["id"] for s in active.json()]


@pytest.mark.asyncio
async def test_wrong_api_key_rejected(make_api):
    async with make_api(services=ServiceContainer()) as ac:
        r = await ac.post("/v1/sessions", json={}, headers={"X-API-Key": "totally-wrong"})
        assert r.status_code == 401


@pytest.mark.asyncio
async def test_missing_key_uses_dev_owner(make_api):
    async with make_api(services=ServiceContainer()) as ac:
        r = await ac.post("/v1/sessions", json={})
        assert r.status_code == 201
        assert r.json()["owner_id"]


@pytest.mark.asyncio
async def test_owner_isolation_between_users(make_api, db):
    alice = await _make_user("alice-key")
    bob = await _make_user("bob-key")

    async with make_api(services=ServiceContainer()) as ac:
        alice_session = await ac.post(
            "/v1/sessions", json={"title": "alice chat"}, headers={"X-API-Key": "alice-key"}
        )
        assert alice_session.status_code == 201
        alice_sid = alice_session.json()["id"]

        # Alice sees her session; Bob does not.
        bob_list = await ac.get("/v1/sessions", headers={"X-API-Key": "bob-key"})
        assert alice_sid not in [s["id"] for s in bob_list.json()]

        bob_fetch = await ac.get(f"/v1/sessions/{alice_sid}", headers={"X-API-Key": "bob-key"})
        assert bob_fetch.status_code == 404

        bob_delete = await ac.delete(f"/v1/sessions/{alice_sid}", headers={"X-API-Key": "bob-key"})
        assert bob_delete.status_code == 404


@pytest.mark.asyncio
async def test_healthz(make_api):
    async with make_api(services=ServiceContainer()) as ac:
        r = await ac.get("/healthz")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"
