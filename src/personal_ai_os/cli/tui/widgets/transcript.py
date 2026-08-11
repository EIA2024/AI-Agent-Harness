"""Transcript widget (CLI v2, T23)."""

from __future__ import annotations

from rich.text import Text
from textual.widgets import Static

from personal_ai_os.cli.domain.state import AppState
from personal_ai_os.cli.tui.render import transcript_lines

_MAX_CELLS = 500


class Transcript(Static):
    """Renders the presentation-domain transcript as styled lines."""

    def render_state(self, state: AppState) -> None:
        text = Text()
        for line in transcript_lines(state, max_cells=_MAX_CELLS):
            text.append(line + "\n")
        self.update(text)
        self.scroll_end(animate=False)
