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

    A missing header always 401s — there is no silent fallback. The development
    key (``PERSONAL_AI_DEV_API_KEY``) only works when the env var is explicitly
    set *and* the client sends it in the header.
    """
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing API key")
    async with session_scope() as session:
        result = await session.execute(select(User).where(User.api_key == x_api_key))
        user = result.scalar_one_or_none()
        if user is None:
            raise HTTPException(status_code=401, detail="Invalid API key")
        return user


async def ensure_dev_owner() -> User | None:
    """Create the development ``owner`` user (idempotent). Called on startup.

    Only creates the dev user when ``PERSONAL_AI_DEV_API_KEY`` is explicitly set.
    Returns the user if created/found, or ``None`` when dev mode is disabled.
    """
    dev_key = config.DEV_API_KEY
    if not dev_key:
        return None
    async with session_scope() as session:
        result = await session.execute(select(User).where(User.api_key == dev_key))
        user = result.scalar_one_or_none()
        if user is None:
            user = User(
                username=config.DEV_USERNAME,
                display_name="Dev Owner",
                api_key=dev_key,
            )
            session.add(user)
            await session.flush()
            await session.refresh(user)
        return user
