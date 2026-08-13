"""Built-in native connectors (Department 09).

Factories here produce ready-to-register connector instances. The filesystem
connector requires an explicit root (``PERSONAL_AI_WORKSPACE_ROOT`` in
production, P1-004); the factory falls back to the cwd only as a dev
convenience.
"""

from __future__ import annotations

import os

from connectors.calculator.connector import CalculatorConnector
from connectors.filesystem.connector import FilesystemConnector
from connectors.http_fetch.connector import HttpFetchConnector


def _filesystem_root() -> str:
    configured = os.environ.get("PERSONAL_AI_WORKSPACE_ROOT")
    if configured:
        return configured
    if os.environ.get("APP_ENV", "development").lower() == "production":
        raise RuntimeError("production requires PERSONAL_AI_WORKSPACE_ROOT")
    return os.getcwd()


def _http_allowed_domains() -> list[str]:
    raw = os.environ.get("PERSONAL_AI_HTTP_ALLOWED_DOMAINS", "")
    domains = [item.strip() for item in raw.split(",") if item.strip()]
    if not domains and os.environ.get("APP_ENV", "development").lower() == "production":
        raise RuntimeError("production requires PERSONAL_AI_HTTP_ALLOWED_DOMAINS")
    return domains


BUILTIN_CONNECTORS = [
    CalculatorConnector(),
    FilesystemConnector(allowed_root=_filesystem_root()),
    HttpFetchConnector(allowed_domains=_http_allowed_domains()),
]


def get_builtin_connectors(*, filesystem_root: str | None = None):
    """Return fresh instances of the built-in connectors.

    ``filesystem_root`` overrides the filesystem connector's allowed root
    (defaults to ``PERSONAL_AI_WORKSPACE_ROOT`` or the cwd in dev).
    """
    return [
        CalculatorConnector(),
        FilesystemConnector(allowed_root=filesystem_root or _filesystem_root()),
        HttpFetchConnector(allowed_domains=_http_allowed_domains()),
    ]


__all__ = [
    "BUILTIN_CONNECTORS",
    "get_builtin_connectors",
    "CalculatorConnector",
    "FilesystemConnector",
    "HttpFetchConnector",
]
