"""P0-006 regression — no weak deployment defaults, no DB exposure.

Static checks on the compose file (runnable in CI without docker) plus the
insecure-config startup detector.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
import yaml

COMPOSE = Path(__file__).resolve().parents[2] / "deploy" / "compose" / "docker-compose.yml"


def _compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def test_postgres_not_published_to_host():
    pg = _compose()["services"]["postgres"]
    assert pg.get("ports") in (None, []), "postgres must not be published to the host"
    assert pg.get("expose") == ["5432"]


def test_api_binds_loopback_only():
    api = _compose()["services"]["api"]
    assert api.get("ports"), "api must be reachable"
    assert all("127.0.0.1:" in p for p in api["ports"])


def test_no_secret_defaults_in_compose():
    compose_text = COMPOSE.read_text(encoding="utf-8")
    assert "dev-key" not in compose_text, "no default dev key"
    assert ":pass@" not in compose_text, "no hardcoded DB password"
    assert "POSTGRES_PASSWORD: pass" not in compose_text
    # every credential is required from the environment
    pg_env = _compose()["services"]["postgres"]["environment"]
    assert "${POSTGRES_USER:?" in pg_env["POSTGRES_USER"]
    assert "${POSTGRES_PASSWORD:?" in pg_env["POSTGRES_PASSWORD"]


def test_insecure_config_detector_warns_on_weak_dev_key(monkeypatch, caplog):
    import apps.api.main as main_mod

    monkeypatch.setenv("PERSONAL_AI_DEV_API_KEY", "dev-key")
    with caplog.at_level(logging.WARNING, logger="personal_ai_os.config"):
        main_mod._warn_insecure_config()
    assert "weak value" in caplog.text


def test_insecure_config_detector_warns_on_placeholder_db_password(monkeypatch, caplog):
    import apps.api.main as main_mod

    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@db:5432/x")
    with caplog.at_level(logging.WARNING, logger="personal_ai_os.config"):
        main_mod._warn_insecure_config()
    assert "placeholder password" in caplog.text


@pytest.mark.asyncio
async def test_production_requires_explicit_database_url(monkeypatch):
    """P1-024 — APP_ENV=production with no DATABASE_URL fails startup."""
    import apps.api.main as main_mod

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        await main_mod.ensure_database()
