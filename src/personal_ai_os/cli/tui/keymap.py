"""Keymap reference (CLI v2, T25/T26).

The live bindings live on the widgets/app (Textual ``BINDINGS``); this module
documents them in one place for help text and tests.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class KeyBinding:
    key: str
    action: str
    description: str


BINDINGS: list[KeyBinding] = [
    KeyBinding("enter", "submit", "Send message"),
    KeyBinding("ctrl+j", "insert_newline", "Insert newline"),
    KeyBinding("ctrl+c", "interrupt", "Cancel run / exit"),
    KeyBinding("ctrl+l", "clear_view", "Clear transcript view"),
]
