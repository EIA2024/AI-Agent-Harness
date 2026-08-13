"""Shared FastAPI dependencies: service access + API-key authentication."""

from __future__ import annotations

import os

from fastapi import Header, HTTPException, Request
from sqlalchemy import select

from personal_ai_os.db.models import User
from personal_ai_os.db.session import session_scope
from personal_ai_os.gateway.services import ServiceContainer

from . import config


def get_services(request: Request) -> ServiceContainer:
    return request.app.state.services


def _key_hash_secret() -> bytes:
    explicit = os.environ.get("PERSONAL_AI_KEY_HASH_SECRET")
    if explicit:
        return explicit.encode()
    if os.environ.get("APP_ENV", "development").lower() == "production":
        raise RuntimeError(
            "PERSONAL_AI_KEY_HASH_SECRET is required in production; refusing "
            "to use a public/default API-key hashing secret"
        )
    # Development still needs a stable secret across restarts. Reuse the
    # explicitly configured dev credential as local-only HMAC key material
    # rather than a repository-wide constant.
    dev_key = os.environ.get("PERSONAL_AI_DEV_API_KEY")
    if not dev_key:
        raise RuntimeError(
            "Set PERSONAL_AI_KEY_HASH_SECRET (or PERSONAL_AI_DEV_API_KEY in development)"
        )
    return ("dev-hash:" + dev_key).encode()


def _hash_api_key(key: str) -> str:
    import hashlib
    import hmac

    return hmac.new(_key_hash_secret(), key.encode(), hashlib.sha256).hexdigest()


async def resolve_user(x_api_key: str | None = Header(default=None)) -> User:
    """Authenticate by HMAC digest; migrate a matching legacy plaintext row once."""
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing API key")
    digest = _hash_api_key(x_api_key)
    async with session_scope() as session:
        user = (
            await session.execute(select(User).where(User.api_key_hash == digest))
        ).scalar_one_or_none()
        if user is None:
            # Transitional compatibility only: a successful legacy login moves
            # the secret to hashed-at-rest form in the same transaction.
            user = (
                await session.execute(select(User).where(User.api_key == x_api_key))
            ).scalar_one_or_none()
            if user is not None:
                user.api_key_hash = digest
                user.api_key = None
                await session.flush()
        if user is None:
            raise HTTPException(status_code=401, detail="Invalid API key")
        return user


async def ensure_dev_owner() -> User | None:
    """Create/migrate the explicit development owner without storing its raw key."""
    dev_key = config.DEV_API_KEY
    if not dev_key:
        return None
    digest = _hash_api_key(dev_key)
    async with session_scope() as session:
        user = (
            await session.execute(select(User).where(User.api_key_hash == digest))
        ).scalar_one_or_none()
        if user is None:
            legacy = (
                await session.execute(select(User).where(User.api_key == dev_key))
            ).scalar_one_or_none()
            if legacy is not None:
                legacy.api_key_hash = digest
                legacy.api_key = None
                await session.flush()
                return legacy

            same_name = (
                await session.execute(
                    select(User).where(User.username == config.DEV_USERNAME)
                )
            ).scalar_one_or_none()
            if same_name is not None:
                raise RuntimeError(
                    "Development owner exists but configured API key does not match; "
                    "rotate it explicitly instead of silently replacing credentials"
                )

            user = User(
                username=config.DEV_USERNAME,
                display_name="Dev Owner",
                api_key=None,
                api_key_hash=digest,
            )
            session.add(user)
            await session.flush()
            await session.refresh(user)
        elif user.api_key is not None:
            user.api_key = None
            await session.flush()
        return user
