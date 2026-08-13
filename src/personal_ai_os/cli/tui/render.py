"""Pure rendering helpers for the TUI (CLI v2, T23).

These produce plain-text lines from the presentation domain so they are
unit-testable and reusable by the plain/accessible fallback mode. The Textual
widgets apply semantic styling on top. All untrusted cell text passes through
the terminal sanitizer (T52).
"""

from __future__ import annotations

from rich.cells import set_cell_size

from personal_ai_os.cli.domain.state import AppState, TranscriptCell
from personal_ai_os.cli.sanitize import strip_control_sequences

_STATUS_GLYPHS = {
    "running": "*",
    "waiting_approval": "!",
    "completed": "OK",
    "failed": "ERR",
    "cancelled": "-",
}


def status_glyph(run_status: str) -> str:
    return _STATUS_GLYPHS.get(run_status, "?")


def status_bar_text(state: AppState, *, width: int | None = None) -> str:
    """Single-line status: session · run · state · connection."""
    session = (state.session_id or "-")[:8]
    run = (state.run_id or "-")[:8]
    rendered = (
        f"session {session} · run {run} · {state.run_status} · "
        f"{state.connection_state.value}"
    )
    return set_cell_size(rendered, width) if width is not None else rendered


def cell_lines(cell: TranscriptCell) -> list[str]:
    """Plain-text representation of one transcript cell (sanitized)."""
    kind = cell.kind
    if kind == "assistant":
        return [strip_control_sequences(line) for line in cell.text.splitlines()] or [""]
    if kind == "tool":
        status = cell.payload.get("status", "")
        name = cell.payload.get("tool_name", "")
        return [f"> {strip_control_sequences(name)} [{status}]"]
    if kind == "approval":
        return ["! Approval required"]
    if kind == "run_status":
        return [strip_control_sequences(cell.text)] if cell.text else []
    if kind == "progress":
        return [f"... {strip_control_sequences(cell.text)}"] if cell.text else []
    if kind == "error":
        return [f"! {strip_control_sequences(cell.text)}"]
    if kind == "warning":
        return [f"! {strip_control_sequences(cell.text)}"]
    if kind == "notice":
        return [strip_control_sequences(cell.text)] if cell.text else []
    return [strip_control_sequences(cell.text)] if cell.text else []


def transcript_lines(state: AppState, *, max_cells: int | None = None) -> list[str]:
    """Render the transcript as plain lines (optionally trimming to last N cells)."""
    cells = state.transcript
    if max_cells is not None and len(cells) > max_cells:
        cells = cells[-max_cells:]
    lines: list[str] = []
    for cell in cells:
        lines.extend(cell_lines(cell))
    return lines
