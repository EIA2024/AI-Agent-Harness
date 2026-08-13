"""Standards-compliant SSE client decoder (CLI v2, T11).

Implements the core of the WHATWG EventSource framing: ``event:`` / ``data:`` /
``id:`` / ``retry:`` fields, multi-line ``data:`` joined with ``\\n``,
comment-only lines ignored, and an explicit ``finish()`` to flush a final event
at EOF. Designed to be testable against raw fixture text and driven by an async
HTTP stream from the client layer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ServerEvent:
    """One decoded server event before normalization."""

    event: str
    data: dict[str, Any] = field(default_factory=dict)
    id: str | None = None
    retry: int | None = None
    malformed: bool = False
    raw_data: str | None = None


class SSEDecoder:
    """Incremental SSE parser.

    Feed one line at a time with :meth:`feed_line`; completed events are
    returned. Call :meth:`finish` when the stream ends to flush any trailing
    event that was not terminated by a blank line.
    """

    def __init__(self) -> None:
        self._event: str | None = None
        self._data_lines: list[str] = []
        self._event_id: str | None = None
        self._retry: int | None = None

    def feed_line(self, line: str) -> list[ServerEvent]:
        """Consume one raw stream line (CR/LF stripped defensively)."""
        line = line.rstrip("\r\n")
        if not line:
            return self._flush()
        if line.startswith(":"):
            return []  # comment / keep-alive
        field_name, sep, value = line.partition(":")
        if not sep:
            return []  # line with no colon: no field
        value = value[1:] if value.startswith(" ") else value
        if field_name == "event":
            self._event = value
        elif field_name == "data":
            self._data_lines.append(value)
        elif field_name == "id":
            self._event_id = value
        elif field_name == "retry":
            try:
                self._retry = int(value)
            except ValueError:
                pass
        return []

    def finish(self) -> list[ServerEvent]:
        """Flush a trailing event at end-of-stream (no blank line seen)."""
        return self._flush()

    def _flush(self) -> list[ServerEvent]:
        if self._event is None and not self._data_lines:
            self._event = None
            self._data_lines = []
            self._retry = None
            return []
        event_name = self._event or "message"
        raw_data = "\n".join(self._data_lines) if self._data_lines else ""
        payload: dict[str, Any] = {}
        malformed = False
        if raw_data:
            try:
                parsed = json.loads(raw_data)
                payload = parsed if isinstance(parsed, dict) else {"value": parsed}
            except json.JSONDecodeError:
                malformed = True
                payload = {}
        event = ServerEvent(
            event=event_name,
            data=payload,
            id=self._event_id,
            retry=self._retry,
            malformed=malformed,
            raw_data=raw_data or None,
        )
        self._event = None
        self._data_lines = []
        self._retry = None
        return [event]
