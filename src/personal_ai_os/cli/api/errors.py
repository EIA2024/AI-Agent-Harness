"""Typed CLI error taxonomy (CLI v2, T10).

The API/transport layer never prints and never calls ``SystemExit``: it raises
typed exceptions that the command layer / TUI maps to recovery actions.
"""

from __future__ import annotations

from typing import Any


class CLIError(Exception):
    """Base for all CLI errors."""


class APIError(CLIError):
    """A non-2xx response from the Personal AI OS REST API."""

    def __init__(self, status_code: int, detail: str, *, retryable: bool = False) -> None:
        self.status_code = status_code
        self.detail = detail
        self.retryable = retryable
        super().__init__(f"HTTP {status_code}: {detail}")

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status_code, "detail": self.detail}


class AuthError(APIError):
    """401 — missing/invalid API key."""


class NotFoundError(APIError):
    """404 — resource missing (or not owned by this user)."""


class ConflictError(APIError):
    """409 — state conflict (double approval, concurrent resume, archived session)."""


class RateLimitError(APIError):
    """429 — too many requests."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(status_code, detail, retryable=True)


class ServerError(APIError):
    """5xx — server-side failure."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(status_code, detail, retryable=True)


class TransportError(CLIError):
    """Network failure: connection refused, timeout, DNS, TLS."""


class StreamProtocolError(CLIError):
    """The SSE stream was malformed beyond recovery."""


class ConfigError(CLIError):
    """Local configuration problem (missing/invalid API URL, key, profile)."""


def classify(status_code: int, detail: str) -> APIError:
    """Map an HTTP status code to the most specific typed error."""
    if status_code == 401:
        return AuthError(status_code, detail)
    if status_code == 404:
        return NotFoundError(status_code, detail)
    if status_code == 409:
        return ConflictError(status_code, detail)
    if status_code == 429:
        return RateLimitError(status_code, detail)
    if status_code >= 500:
        return ServerError(status_code, detail)
    return APIError(status_code, detail)
