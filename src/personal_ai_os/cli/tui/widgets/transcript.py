"""Transcript widget (CLI v2, T23)."""

from __future__ import annotations

from rich.text import Text
from textual.widgets import Static

from personal_ai_os.cli.domain.state import AppState
from personal_ai_os.cli.tui.render import transcript_lines

_MAX_CELLS = 500


class Transcript(Static):
    """Renders the presentation-domain transcript as styled lines.

    The widget itself grows with content (``height: auto``) inside the
    ``#scroll`` VerticalScroll, which is the scrollable area. Rendering keeps
    the view pinned to the latest lines unless the user has scrolled up to read.
    """

    def render_state(self, state: AppState) -> None:
        text = Text()
        for line in transcript_lines(state, max_cells=_MAX_CELLS):
            text.append(line + "\n")
        self.update(text)
        self._follow_tail()

    def _follow_tail(self) -> None:
        """Auto-scroll the container to the bottom when already near the bottom."""
        scroll = self.parent
        if scroll is None or not hasattr(scroll, "scroll_end"):
            return
        at_bottom = scroll.max_scroll_y == 0 or (
            scroll.scroll_offset.y >= scroll.max_scroll_y - 1
        )
        if at_bottom:
            scroll.scroll_end(animate=False)
