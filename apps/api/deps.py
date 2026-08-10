"""Shared FastAPI dependencies: service access + API-key authentication."""

from __future__ import annotations

from fastapi import Header, HTTPException, Request
from sqlalchemy import select

from personal_ai_os.db.models import User
from personal_ai_os.db.session import session_scope
from personal_ai_os.gateway.services import ServiceContainer

from . import config


def get_services(request: Request) -> ServiceContainer:
    """Pull the injected service container off the application state."""
    return request.app.state.services


async def resolve_user(
    x_api_key: str | None = Header(default=None),
) -> User:
    """Resolve the authenticated owner from the ``X-API-Key`` header.

    When the header is missing the development key is used, which maps to the
    ``owner`` user created by the app lifespan. An unrecognized key is a 401.
    """
    key = x_api_key if x_api_key else config.DEV_API_KEY
    async with session_scope() as session:
        result = await session.execute(select(User).where(User.api_key == key))
        user = result.scalar_one_or_none()
        if user is None:
            raise HTTPException(status_code=401, detail="Invalid or missing API key")
        return user


async def ensure_dev_owner() -> User:
    """Create the development ``owner`` user (idempotent). Called on startup."""
    async with session_scope() as session:
        result = await session.execute(select(User).where(User.api_key == config.DEV_API_KEY))
        user = result.scalar_one_or_none()
        if user is None:
            user = User(
                username=config.DEV_USERNAME,
                display_name="Dev Owner",
                api_key=config.DEV_API_KEY,
            )
            session.add(user)
            await session.flush()
            await session.refresh(user)
        return user
