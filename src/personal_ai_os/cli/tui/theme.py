"""Semantic theme tokens for the TUI (CLI v2, T26).

No business code writes fixed RGB; widgets reference these semantic names.
The Textual app maps them to its own stylesheet.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Tokens:
    text_primary: str = "text"
    text_muted: str = "text-muted"
    accent: str = "accent"
    success: str = "success"
    warning: str = "warning"
    danger: str = "error"
    border: str = "border"
    risk_r4: str = "bold red"


DEFAULT_TOKENS = Tokens()


def token(name: str) -> str:
    return getattr(DEFAULT_TOKENS, name, "text")
