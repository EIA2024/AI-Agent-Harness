"""Shared helpers for admin commands (tables / JSON output)."""

from __future__ import annotations

import json
import sys
from typing import Any, TextIO

from rich.console import Console
from rich.table import Table


def emit(
    rows: list[dict[str, Any]],
    *,
    columns: list[tuple[str, str]],
    json_mode: bool = False,
    stdout: TextIO = sys.stdout,
) -> None:
    """Render a list of dicts as a human table or JSON array."""
    if json_mode:
        stdout.write(json.dumps(rows, ensure_ascii=False, indent=2) + "\n")
        return
    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 1))
    for label, _key in columns:
        table.add_column(label)
    for row in rows:
        table.add_row(*[str(row.get(key, "")) for _label, key in columns])
    Console(file=stdout, highlight=False).print(table)


def short_id(value: str | None, length: int = 8) -> str:
    return (value or "")[:length]
