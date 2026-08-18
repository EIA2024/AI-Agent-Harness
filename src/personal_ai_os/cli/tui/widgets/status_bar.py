"""Status bar widget (CLI v2, T25)."""

from __future__ import annotations

from textual.widgets import Static

from personal_ai_os.cli.domain.state import AppState
from personal_ai_os.cli.tui.render import status_bar_text


class StatusBar(Static):
    """One-line context including API, provider, model, and run state."""

    def render_state(self, state: AppState) -> None:
        self.update(status_bar_text(state))
