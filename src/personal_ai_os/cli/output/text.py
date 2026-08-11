"""Plain-text headless rendering (CLI v2, T14)."""

from __future__ import annotations

from personal_ai_os.cli.domain.state import AppState


def final_answer(state: AppState) -> str:
    """The assistant's final text for the run."""
    return state.final_response or state.assistant_text


def render_transcript(state: AppState) -> str:
    """Human-readable transcript for ``exec`` progress / audit output.

    Plain text, no ANSI. Progress, tool and status lines are meant for stderr;
    the final assistant answer is the only thing ``exec -o text`` writes to
    stdout (see :func:`final_answer`).
    """
    lines: list[str] = []
    for cell in state.transcript:
        if cell.kind == "assistant" and not cell.payload.get("streaming"):
            lines.append(cell.text)
            continue
        if cell.text:
            lines.append(f"{cell.kind}: {cell.text}")
    return "\n".join(lines)
