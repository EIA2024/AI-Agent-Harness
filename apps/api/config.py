"""API configuration (MVP: environment-driven, no pydantic-settings dependency)."""

from __future__ import annotations

import os

# API-key auth (dev only). Production will swap in WebAuthn/OAuth + JWT.
DEV_API_KEY = os.environ.get("PERSONAL_AI_DEV_API_KEY", "dev-key")
DEV_USERNAME = os.environ.get("PERSONAL_AI_DEV_USERNAME", "owner")

# Local SQLite fallback used when no DATABASE_URL is configured.
DB_DIR = os.environ.get("PERSONAL_AI_DB_DIR", "data")
DB_FILE = os.environ.get("PERSONAL_AI_DB_FILE", "app.db")
