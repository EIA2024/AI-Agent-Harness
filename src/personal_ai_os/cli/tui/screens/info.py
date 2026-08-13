"""Generic info/detail modal (CLI v2, T33/T35)."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static


class InfoScreen(ModalScreen[None]):
    """Scrollable read-only overlay for status / help / tool detail."""

    BINDINGS = [("escape", "dismiss_modal", "Close"), ("q", "dismiss_modal", "Close")]

    def __init__(self, title: str, lines: list[str]) -> None:
        super().__init__()
        self._title = title
        self._lines = lines

    def compose(self) -> ComposeResult:
        with Vertical(id="info-panel"):
            yield Static(f"[bold]{self._title}[/]", id="info-title")
            for line in self._lines:
                yield Static(line)

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)
