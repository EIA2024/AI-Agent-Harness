"""Structured JSON output (CLI v2, T14)."""

from __future__ import annotations

from typing import Any

from personal_ai_os.cli.domain.state import AppState
from personal_ai_os.cli.output.text import final_answer

_SCHEMA_VERSION = 1


def run_summary(state: AppState, *, usage: dict[str, Any] | None = None) -> dict[str, Any]:
    """Public JSON result contract for ``exec -o json`` (plan §12.5)."""
    status = state.run_status
    output = None
    if status == "completed":
        output = {"text": final_answer(state)}
    error = None
    if state.last_error:
        error = {"message": state.last_error}
    return {
        "v": _SCHEMA_VERSION,
        "status": status,
        "session_id": state.session_id,
        "run_id": state.run_id,
        "output": output,
        "usage": usage,
        "error": error,
    }
