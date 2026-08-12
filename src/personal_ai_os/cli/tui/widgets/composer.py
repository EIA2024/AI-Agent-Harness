"""Composer widget (CLI v2, T22).

A multi-line text area. Enter submits; Ctrl+J inserts a literal newline (the
plan forbids relying on Shift+Enter alone). TextArea consumes Enter in
``_on_key``, so the subclass intercepts it before the default newline insert.
"""

from __future__ import annotations

from textual import events
from textual.widgets import TextArea


class Composer(TextArea):
    BINDINGS = [
        ("ctrl+j", "insert_newline", "Insert newline"),
    ]

    class Submitted(events.Message):
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    async def _on_key(self, event: events.Key) -> None:
        # Plain Enter submits; modifiers produce distinct key names (ctrl+j,
        # shift+enter) which flow through to the binding system / TextArea.
        if event.key == "enter":
            event.stop()
            self.action_submit()
            return
        await super()._on_key(event)

    def action_submit(self) -> None:
        text = self.text.strip()
        if not text:
            return
        self.post_message(self.Submitted(text))
        self.clear()

    def action_insert_newline(self) -> None:
        self.insert("\n")

    def set_disabled(self, disabled: bool) -> None:
        self.disabled = disabled
        # Re-enabling loses focus (a disabled widget leaves the focus order);
        # hand input focus back to the composer — unless a modal screen (e.g.
        # the approval dialog) is on top, which owns the keyboard.
        if not disabled and self.app.screen is self.screen:
            self.focus()
