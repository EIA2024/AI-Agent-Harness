"""Terminal capability detection (CLI v2, T26)."""

from __future__ import annotations

import os
import shutil
import sys


def is_tty(stream=None) -> bool:
    stream = stream if stream is not None else sys.stdout
    try:
        return bool(stream.isatty())
    except Exception:  # noqa: BLE001
        return False


def color_enabled(stream=None) -> bool:
    """Respect NO_COLOR (https://no-color.org) and non-TTY output."""
    if os.environ.get("NO_COLOR") is not None:
        return False
    return is_tty(stream)


def terminal_width() -> int:
    try:
        return shutil.get_terminal_size().columns
    except Exception:  # noqa: BLE001
        return 80


def reduced_motion() -> bool:
    """True when the user prefers reduced animation (accessibility)."""
    env = os.environ.get("REDUCED_MOTION", "").strip().lower()
    if env:
        return env in ("1", "true", "yes")
    return False
