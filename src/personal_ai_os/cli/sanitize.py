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
# Unicode bidi / text-direction override controls (display spoofing, CWE-1505).
_BIDI_RE = re.compile("[‪-‮⁦-⁩]")

_SECRET_KEYS = (
    "api_key", "apikey", "token", "secret", "password", "passwd", "authorization",
    "x-api-key", "credential", "access_key", "private_key", "access_token",
    "refresh_token", "passphrase", "cookie", "sessionid", "session_id",
    "csrf", "csrf_token", "client_secret", "bearer",
)

# Layout guard: truncate an unbroken run of characters this long.
_MAX_UNBROKEN = 10_000


def strip_control_sequences(text: str) -> str:
    """Remove OSC, ANSI CSI, and stray control characters from untrusted text."""
    if not text:
        return text
    text = _OSC_RE.sub("", text)
    text = _CSI_RE.sub("", text)
    text = _CONTROL_RE.sub("", text)
    text = _BIDI_RE.sub("", text)
    return text


def truncate_long_lines(text: str, *, max_line: int = _MAX_UNBROKEN) -> str:
    """Break unbroken runs longer than ``max_line`` (terminal DoS guard)."""
    if len(text) <= max_line:
        return text
    out: list[str] = []
    for line in text.split("\n"):
        while len(line) > max_line:
            out.append(line[:max_line])
            line = line[max_line:]
        out.append(line)
    return "\n".join(out)


def is_secret_key(key: str) -> bool:
    k = key.strip().lower().replace("-", "_")
    return any(part in k for part in _SECRET_KEYS)


def redact_secrets(mapping: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return a copy of ``mapping`` with secret-looking values masked.

    Recurses through nested dicts AND lists (e.g. ``{"headers": [...]}``).
    """
    if not mapping:
        return {}
    out: dict[str, Any] = {}
    for key, value in mapping.items():
        if is_secret_key(str(key)):
            out[key] = _mask_value(value)
        else:
            out[key] = _redact_value(value)
    return out


def _mask_value(value: Any) -> Any:
    if isinstance(value, dict):
        return redact_secrets(value)
    if isinstance(value, list):
        return [_mask_value(item) for item in value]
    return "***"


def _redact_value(value: Any) -> Any:
    if isinstance(value, dict):
        return redact_secrets(value)
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    return value


def sanitize_text(text: str) -> str:
    """Strip control sequences and truncate wildly long lines (layout guard)."""
    return truncate_long_lines(strip_control_sequences(text))
