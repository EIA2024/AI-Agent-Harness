"""Opt-in user notifications (CLI v2, T53).

Notifications never carry sensitive content — approval notifications only say
an approval is pending (plan §9.6). Enabled via ``PERSONAL_AI_NOTIFY`` env or
``--notify`` style flags; off by default so pipes/CI are never disturbed.
"""

from __future__ import annotations

import os
import sys

_ENABLED_VALUES = ("1", "true", "yes", "on")


def notifications_enabled() -> bool:
    return os.environ.get("PERSONAL_AI_NOTIFY", "").strip().lower() in _ENABLED_VALUES


def approval_required() -> None:
    """Emit a terminal bell when the user opted into notifications."""
    if not notifications_enabled():
        return
    try:
        sys.stderr.write("\a")
        sys.stderr.flush()
    except OSError:  # pragma: no cover - closed pipe
        pass
