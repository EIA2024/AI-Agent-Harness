"""P1-022/023/026 — CORS allowlist, /readyz, request-size limits."""

from __future__ import annotations

import uuid

import pytest

from personal_ai_os.gateway.services import ServiceContainer


@pytest.mark.asyncio
async def test_production_database_checks_migrations_without_create_all(monkeypatch):
    from apps.api import main

    calls: list[str] = []

    async def validate():
        calls.append("validate")

    async def init():
        calls.append("create_all")

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@db/app")
    monkeypatch.setattr(main, "_validate_schema_revision", validate)
    monkeypatch.setattr(main.db_session, "init_db", init)

    await main.ensure_database()

    assert calls == ["validate"]


def test_production_lazy_wiring_fails_closed(monkeypatch):
    from personal_ai_os.gateway.services import _lazy

    monkeypatch.setenv("APP_ENV", "production")

    def broken():
        raise RuntimeError("missing service")

    with pytest.raises(RuntimeError, match="missing service"):
        _lazy(broken)


def test_production_connector_boundaries_are_required(monkeypatch):
    from connectors import _filesystem_root, _http_allowed_domains

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("PERSONAL_AI_WORKSPACE_ROOT", raising=False)
    monkeypatch.delenv("PERSONAL_AI_HTTP_ALLOWED_DOMAINS", raising=False)

    with pytest.raises(RuntimeError, match="WORKSPACE_ROOT"):
        _filesystem_root()
    with pytest.raises(RuntimeError, match="ALLOWED_DOMAINS"):
        _http_allowed_domains()


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
        assert r.status_code == 503
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
async def test_chunked_oversized_request_body_rejected(make_api):
    async def chunks():
        for _ in range(3):
            yield b"x" * 1_000_000

    async with make_api(services=ServiceContainer()) as ac:
        response = await ac.post(
            "/v1/sessions",
            content=chunks(),
            headers={"content-type": "application/json"},
        )

    assert response.status_code == 413


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


@pytest.mark.asyncio
async def test_api_key_auth_verifies_hash_and_plaintext(make_api):
    """P1-021 — auth works for hashed AND legacy plaintext keys."""

    from apps.api.deps import _hash_api_key
    from personal_ai_os.db.models import User
    from personal_ai_os.db.session import session_scope

    async with session_scope() as s:
        s.add(User(username=f"h{uuid.uuid4().hex[:6]}", api_key="hashed-key",
                  api_key_hash=_hash_api_key("hashed-key")))
        s.add(User(username=f"p{uuid.uuid4().hex[:6]}", api_key="plain-key"))
        await s.flush()
    async with make_api(services=ServiceContainer()) as ac:
        r1 = await ac.get("/v1/sessions", headers={"X-API-Key": "hashed-key"})
        assert r1.status_code == 200
        r2 = await ac.get("/v1/sessions", headers={"X-API-Key": "plain-key"})
        assert r2.status_code == 200
        r3 = await ac.get("/v1/sessions", headers={"X-API-Key": "wrong-key"})
        assert r3.status_code == 401
