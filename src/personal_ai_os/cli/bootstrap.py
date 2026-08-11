"""CLI wiring: config from environment + client factory (CLI v2)."""

from __future__ import annotations

import os

from personal_ai_os.cli.api.client import DEFAULT_API_KEY, DEFAULT_API_URL, AsyncAPIClient


def api_url() -> str:
    return os.environ.get("PERSONAL_AI_API_URL", DEFAULT_API_URL)


def api_key() -> str:
    return os.environ.get("PERSONAL_AI_API_KEY", DEFAULT_API_KEY)


def build_client(
    *,
    base_url: str | None = None,
    key: str | None = None,
    transport=None,
) -> AsyncAPIClient:
    """Build an AsyncAPIClient from env (or explicit overrides / test transport)."""
    return AsyncAPIClient(
        base_url=base_url or api_url(),
        api_key=key or api_key(),
        transport=transport,
    )
