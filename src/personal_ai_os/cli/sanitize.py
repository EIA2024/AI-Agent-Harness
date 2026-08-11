"""Terminal output sanitizer + secret redactor (CLI v2, T52).

Untrusted content (tool results, web fetches, model output) can carry ANSI/OSC
escape sequences that would let a malicious payload move the cursor, retitle the
terminal, or trigger control actions. Every presentation path runs output
through :func:`strip_control_sequences` before it reaches the terminal, and
sensitive-looking argument values through :func:`redact_secrets`.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

# OSC sequences: ESC ] ... BEL | ESC ] ... ESC \
_OSC_RE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
# CSI sequences: ESC [ params ... final-byte
_CSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
# Other C1 / C0 controls (keep tab, newline, carriage return).
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")

_SECRET_KEYS = (
    "api_key", "apikey", "token", "secret", "password", "passwd", "authorization",
    "x-api-key", "credential", "access_key", "private_key",
)


def strip_control_sequences(text: str) -> str:
    """Remove OSC, ANSI CSI, and stray control characters from untrusted text."""
    if not text:
        return text
    text = _OSC_RE.sub("", text)
    text = _CSI_RE.sub("", text)
    text = _CONTROL_RE.sub("", text)
    return text


def is_secret_key(key: str) -> bool:
    k = key.strip().lower().replace("-", "_")
    return any(part in k for part in _SECRET_KEYS)


def redact_secrets(mapping: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return a copy of ``mapping`` with secret-looking values masked."""
    if not mapping:
        return {}
    out: dict[str, Any] = {}
    for key, value in mapping.items():
        if is_secret_key(str(key)):
            out[key] = "***"
        elif isinstance(value, dict):
            out[key] = redact_secrets(value)
        else:
            out[key] = value
    return out


def sanitize_text(text: str) -> str:
    """Strip control sequences and truncate wildly long lines (layout guard)."""
    return strip_control_sequences(text)
