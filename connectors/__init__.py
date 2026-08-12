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
    return os.environ.get("PERSONAL_AI_WORKSPACE_ROOT") or os.getcwd()


BUILTIN_CONNECTORS = [
    CalculatorConnector(),
    FilesystemConnector(allowed_root=_filesystem_root()),
    HttpFetchConnector(),
]


def get_builtin_connectors(*, filesystem_root: str | None = None):
    """Return fresh instances of the built-in connectors.

    ``filesystem_root`` overrides the filesystem connector's allowed root
    (defaults to ``PERSONAL_AI_WORKSPACE_ROOT`` or the cwd in dev).
    """
    return [
        CalculatorConnector(),
        FilesystemConnector(allowed_root=filesystem_root or _filesystem_root()),
        HttpFetchConnector(),
    ]


__all__ = [
    "BUILTIN_CONNECTORS",
    "get_builtin_connectors",
    "CalculatorConnector",
    "FilesystemConnector",
    "HttpFetchConnector",
]
