"""P1-022/023/026 — CORS allowlist, /readyz, request-size limits."""

from __future__ import annotations

import pytest

from personal_ai_os.gateway.services import ServiceContainer


@pytest.mark.asyncio
async def test_cors_disabled_by_default(make_api, monkeypatch):
    monkeypatch.delenv("PERSONAL_AI_CORS_ORIGINS", raising=False)
    async with make_api(services=ServiceContainer()) as ac:
        r = await ac.options(
            "/v1/sessions",
            headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "POST"},
        )
        assert "access-control-allow-origin" not in r.headers


@pytest.mark.asyncio
async def test_cors_allowlist_when_configured(make_api, monkeypatch):
    monkeypatch.setenv("PERSONAL_AI_CORS_ORIGINS", "https://app.example.com")
    async with make_api(services=ServiceContainer()) as ac:
        r = await ac.options(
            "/v1/sessions",
            headers={"Origin": "https://app.example.com", "Access-Control-Request-Method": "POST"},
        )
        assert r.headers.get("access-control-allow-origin") == "https://app.example.com"


@pytest.mark.asyncio
async def test_readyz_reports_checks(make_api):
    async with make_api(services=ServiceContainer()) as ac:
        r = await ac.get("/readyz")
        assert r.status_code == 200
        body = r.json()
        assert "checks" in body
        assert "database" in body["checks"]


@pytest.mark.asyncio
async def test_oversized_request_body_rejected(make_api):
    async with make_api(services=ServiceContainer()) as ac:
        big = {"title": "x" * 3_000_000}
        r = await ac.post("/v1/sessions", json=big)
        assert r.status_code == 413
        assert "too large" in r.json()["detail"]


@pytest.mark.asyncio
async def test_oversized_message_text_rejected_by_schema(make_api):
    from personal_ai_os.db.models import User
    from personal_ai_os.db.session import session_scope

    async with session_scope() as s:
        u = User(username="body-test", api_key="body-key")
        s.add(u)
        await s.flush()
    async with make_api(services=ServiceContainer()) as ac:
        r = await ac.post(
            "/v1/sessions",
            json={"title": "t"},
            headers={"X-API-Key": "body-key"},
        )
        sid = r.json()["id"]
        huge = {"text": "x" * 500_000}
        r2 = await ac.post(
            f"/v1/sessions/{sid}/messages", json=huge, headers={"X-API-Key": "body-key"}
        )
        assert r2.status_code == 422  # max_length enforced
