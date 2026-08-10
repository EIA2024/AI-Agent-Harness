"""Built-in native connectors (Department 09).

Factories here produce ready-to-register connector instances. The filesystem
connector roots at ``os.getcwd()`` unless ``filesystem_root`` is given.
"""

from __future__ import annotations

from connectors.calculator.connector import CalculatorConnector
from connectors.filesystem.connector import FilesystemConnector
from connectors.http_fetch.connector import HttpFetchConnector

BUILTIN_CONNECTORS = [
    CalculatorConnector(),
    FilesystemConnector(),
    HttpFetchConnector(),
]


def get_builtin_connectors(*, filesystem_root: str | None = None):
    """Return fresh instances of the built-in connectors.

    ``filesystem_root`` overrides the filesystem connector's allowed root
    (defaults to ``os.getcwd()`` at construction time).
    """
    return [
        CalculatorConnector(),
        FilesystemConnector(allowed_root=filesystem_root) if filesystem_root is not None
        else FilesystemConnector(),
        HttpFetchConnector(),
    ]


__all__ = [
    "BUILTIN_CONNECTORS",
    "get_builtin_connectors",
    "CalculatorConnector",
    "FilesystemConnector",
    "HttpFetchConnector",
]
