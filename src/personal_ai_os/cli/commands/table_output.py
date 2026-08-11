"""Shared helpers for admin commands (tables / JSON output)."""

from __future__ import annotations

import json
import sys
from typing import Any, TextIO

from rich.console import Console
from rich.table import Table

from personal_ai_os.cli.sanitize import strip_control_sequences, truncate_long_lines


def emit(
    rows: list[dict[str, Any]],
    *,
    columns: list[tuple[str, str]],
    json_mode: bool = False,
    stdout: TextIO = sys.stdout,
) -> None:
    """Render a list of dicts as a human table or JSON array.

    Cell values are sanitized (T52) — tool names, summaries and memory content
    come from model/remote-controlled data and must not reach the terminal with
    live escape sequences.
    """
    if json_mode:
        stdout.write(json.dumps(rows, ensure_ascii=False, indent=2) + "\n")
        return
    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 1))
    for label, _key in columns:
        table.add_column(label)
    for row in rows:
        cells = []
        for _label, key in columns:
            value = str(row.get(key, ""))
            cells.append(truncate_long_lines(strip_control_sequences(value), max_line=2000))
        table.add_row(*cells)
    Console(file=stdout, highlight=False).print(table)


def short_id(value: str | None, length: int = 8) -> str:
    return (value or "")[:length]
